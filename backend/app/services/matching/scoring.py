"""Candidate scoring for the matching engine.

Four signals are computed and combined into a composite confidence score:

1. **string_similarity** — token-set similarity via ``difflib.SequenceMatcher``
   on normalized text.
2. **category_agreement** — 1.0 if categories match, 0.0 if they differ,
   *neutral* if either is missing.
3. **unit_compatibility** — same logic as category.
4. **attribute_match** — Jaccard similarity of extracted structured specs
   (MPa, R-values, AWG, dimensions, percentages).

Neutral signals are excluded from both numerator and denominator of the
weighted average, so absent information neither rewards nor punishes a
candidate — per the plan.
"""

from __future__ import annotations

import difflib
import re

from app.config import get_settings
from app.models.schemas import Candidate, CatalogEntry, RecordOut
from app.services.matching.interfaces import CandidateScorer
from app.services.matching.normalize import normalize_text


# ---------------------------------------------------------------------------
# Attribute extraction patterns — conservative (missed spec = neutral,
# never wrong).
# ---------------------------------------------------------------------------

# Matches: 30 MPa, 30MPa, 30 mpa, etc.
_MPA_RE = re.compile(r"(\d+)\s*mpa", re.IGNORECASE)

# Matches: R-12, R-22, R-40, etc.
_RVALUE_RE = re.compile(r"R-(\d+)", re.IGNORECASE)

# Matches: #4/0 AWG, #12 AWG, #1/0 AWG, etc.
_AWG_RE = re.compile(r"#?([\d/]+)\s*awg", re.IGNORECASE)

# Matches: 6x6x1/4, 8x4x3/16, 2x2x1/8, 4x2x1/2 (HSS dimensions)
_HSS_DIM_RE = re.compile(r"\b(\d+x\d+x\d+/\d+)\b", re.IGNORECASE)

# Matches: 25%, 50%, 15% (percentage specs)
_PCT_RE = re.compile(r"(\d+)%")

# Matches: 50mm, 50 mm, 600MM, 15.9 mm, 12.7mm, etc.
_MM_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*mm", re.IGNORECASE)

# Matches dimensional lumber sizes like 38x89, 38x140, 130x304
_LUMBER_DIM_RE = re.compile(r"\b(\d+x\d+)\b(?!\s*x)", re.IGNORECASE)

# Matches W-beam sizes like W360X57, W150X22
_WBEAM_RE = re.compile(r"\bW(\d+)[xX](\d+)\b", re.IGNORECASE)

# Matches channel sizes like C310X31, C200X17
_CHANNEL_RE = re.compile(r"\bC(\d+)[xX](\d+)\b", re.IGNORECASE)

# Matches rebar sizes like 10M, 15M, 20M, 25M, 30M, 35M (when preceded by context)
_REBAR_SIZE_RE = re.compile(r"\b(\d{2})M\b", re.IGNORECASE)

# Matches Grade 400W, GR400, grade 400w, Grade B, Grade C
_GRADE_RE = re.compile(r"\bgrade\s+([a-z0-9]+)\b", re.IGNORECASE)

# Matches: fly ash, slag (additive types in concrete)
_ADDITIVE_RE = re.compile(r"\b(fly\s*ash|slag)\b", re.IGNORECASE)

# Matches: air entrained
_AIR_ENTRAINED_RE = re.compile(r"\bair\s*entrained\b", re.IGNORECASE)

# Matches: Type X, Type C, Type S, Type N, Type L, Type K
_TYPE_RE = re.compile(r"\btype\s+([A-Z])\b", re.IGNORECASE)

# Matches: Schedule 40 / SCH40
_SCHEDULE_RE = re.compile(r"\bschedule\s*(\d+)\b", re.IGNORECASE)

# Matches lumber grades like: No.1/Btr, No. 2, NO.1
_LUMBER_GRADE_RE = re.compile(r"\bno\.?\s*(\d+(?:/btr)?)\b", re.IGNORECASE)

# Matches insulation facing: faced, unfaced
_FACING_RE = re.compile(r"\b(unfaced|faced)\b", re.IGNORECASE)


def extract_attributes(text: str) -> set[str]:
    """Extract structured attribute specs from text.

    Returns a set of canonical attribute strings like ``{'30mpa', 'r-22',
    '25%', '6x6x1/4'}``.  The extraction is conservative — a missed spec
    scores neutral (absent from both sides), never generates false agreement.
    """
    attrs: set[str] = set()

    for m in _MPA_RE.finditer(text):
        attrs.add(f"{m.group(1)}mpa")

    for m in _RVALUE_RE.finditer(text):
        attrs.add(f"r-{m.group(1)}")

    for m in _AWG_RE.finditer(text):
        attrs.add(f"#{m.group(1)}awg")

    for m in _HSS_DIM_RE.finditer(text):
        attrs.add(m.group(1).lower())

    for m in _PCT_RE.finditer(text):
        attrs.add(f"{m.group(1)}%")

    for m in _MM_SIZE_RE.finditer(text):
        attrs.add(f"{m.group(1)}mm")

    for m in _WBEAM_RE.finditer(text):
        attrs.add(f"w{m.group(1)}x{m.group(2)}")

    for m in _CHANNEL_RE.finditer(text):
        attrs.add(f"c{m.group(1)}x{m.group(2)}")

    for m in _REBAR_SIZE_RE.finditer(text):
        attrs.add(f"{m.group(1)}m")

    for m in _GRADE_RE.finditer(text):
        attrs.add(f"grade{m.group(1).lower()}")

    for m in _ADDITIVE_RE.finditer(text):
        attrs.add(m.group(1).lower().replace(" ", ""))

    if _AIR_ENTRAINED_RE.search(text):
        attrs.add("airentrained")

    for m in _TYPE_RE.finditer(text):
        attrs.add(f"type{m.group(1).lower()}")

    for m in _SCHEDULE_RE.finditer(text):
        attrs.add(f"schedule{m.group(1)}")

    for m in _LUMBER_GRADE_RE.finditer(text):
        attrs.add(f"no{m.group(1).lower()}")

    for m in _FACING_RE.finditer(text):
        attrs.add(m.group(1).lower())

    return attrs


def token_set_similarity(a: str, b: str) -> float:
    """Token-set similarity using ``difflib.SequenceMatcher``.

    Both strings are tokenised, sorted, and re-joined before comparison.
    This makes the metric robust to word reordering and extra tokens —
    unlike a plain character-level ratio.
    """
    if not a or not b:
        return 0.0

    tokens_a = sorted(a.lower().split())
    tokens_b = sorted(b.lower().split())

    # Intersection + remainder approach (like fuzzywuzzy token_set_ratio):
    # Compare sorted intersection joined with each remainder.
    set_a = set(tokens_a)
    set_b = set(tokens_b)
    intersection = set_a & set_b
    diff_a = set_a - set_b
    diff_b = set_b - set_a

    sorted_inter = " ".join(sorted(intersection))
    combined_a = " ".join(sorted(intersection | diff_a))
    combined_b = " ".join(sorted(intersection | diff_b))

    # Take the max of three ratios:
    # 1. sorted intersection vs combined_a (how much a adds beyond common)
    # 2. sorted intersection vs combined_b (how much b adds beyond common)
    # 3. combined_a vs combined_b (full comparison)
    ratios = [
        difflib.SequenceMatcher(None, sorted_inter, combined_a).ratio(),
        difflib.SequenceMatcher(None, sorted_inter, combined_b).ratio(),
        difflib.SequenceMatcher(None, combined_a, combined_b).ratio(),
    ]
    return max(ratios)


def _category_agreement(
    record_cat: str | None, entry_cat: str
) -> float | None:
    """Return 1.0 if categories match, 0.0 if they differ, None if neutral."""
    if not record_cat:
        return None  # neutral — record has no category
    return 1.0 if record_cat.lower() == entry_cat.lower() else 0.0


def _unit_compatibility(
    record_unit: str | None, entry_unit: str
) -> float | None:
    """Return 1.0 if units match, 0.0 if they differ, None if neutral."""
    if not record_unit:
        return None  # neutral
    return 1.0 if record_unit.lower() == entry_unit.lower() else 0.0


def _attribute_match(record_text: str, entry_text: str) -> float | None:
    """Return Jaccard similarity of extracted attributes.

    Returns None (neutral) if neither text yields extractable attributes.
    """
    rec_attrs = extract_attributes(record_text)
    ent_attrs = extract_attributes(entry_text)

    if not rec_attrs and not ent_attrs:
        return None  # neutral

    if not rec_attrs or not ent_attrs:
        return 0.0  # one side has specs, the other doesn't

    intersection = rec_attrs & ent_attrs
    union = rec_attrs | ent_attrs
    return len(intersection) / len(union)


class LexicalScorer(CandidateScorer):
    """Score one candidate against one source record.

    Produces a ``Candidate`` with a composite score in [0, 1] and the
    per-signal breakdown that produced it.  Signal weights come from
    ``get_settings().matching.weights`` — never hardcoded.
    """

    def score(self, record: RecordOut, entry: CatalogEntry) -> Candidate:
        """Return the candidate with composite score and signal breakdown."""
        settings = get_settings()
        weights = settings.matching.weights

        # Normalize both texts for string comparison.
        norm_rec = normalize_text(record.raw_text)
        norm_ent = normalize_text(entry.description)

        # Compute individual signals.
        str_sim = token_set_similarity(norm_rec, norm_ent)
        cat_agree = _category_agreement(record.category, entry.category)
        unit_compat = _unit_compatibility(record.unit, entry.unit)
        attr_match = _attribute_match(norm_rec, norm_ent)

        # Build per-signal breakdown dict.
        signals: dict[str, float] = {
            "string_similarity": round(str_sim, 4),
        }
        if cat_agree is not None:
            signals["category_agreement"] = cat_agree
        if unit_compat is not None:
            signals["unit_compatibility"] = unit_compat
        if attr_match is not None:
            signals["attribute_match"] = round(attr_match, 4)

        # Weighted average of present signals only.
        numerator = 0.0
        denominator = 0.0

        signal_values: dict[str, float | None] = {
            "string_similarity": str_sim,
            "category_agreement": cat_agree,
            "unit_compatibility": unit_compat,
            "attribute_match": attr_match,
        }

        for signal_name, value in signal_values.items():
            if value is not None and signal_name in weights:
                w = weights[signal_name]
                numerator += w * value
                denominator += w

        composite = numerator / denominator if denominator > 0 else 0.0
        composite = max(0.0, min(1.0, composite))  # clamp to [0, 1]

        return Candidate(
            catalog_id=entry.catalog_id,
            description=entry.description,
            score=round(composite, 4),
            signals=signals,
        )
