"""Text normalization for matching.

The highest-leverage layer of the hybrid engine: clean and expand a curated
construction-abbreviation vocabulary so source shorthand converges on catalog
prose *before* anything is compared.  Most accuracy is won here, not in the
similarity metric.

All constants are module-level for testability; nothing is read from config
because the abbreviation map is domain knowledge, not a tunable parameter.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Abbreviation map — built by inspecting the actual fixture CSVs.
#
# Keys are UPPERCASE tokens that appear in source records; values are the
# lowercase catalog phrasing they should expand to.  Multi-word expansions
# are fine — the token-set similarity metric handles token count differences.
#
# Order matters for multi-token entries: they are applied first so that
# "PVC CONDUIT" is matched before "PVC" alone.
# ---------------------------------------------------------------------------

_MULTI_TOKEN_ABBREVS: dict[str, str] = {
    "CONC RM":       "ready-mix concrete",
    "PVC CONDUIT":   "rigid pvc conduit",
    "GYP BD":        "gypsum board",
    "CABLE TRAY":    "cable tray",
    "STL BM":        "steel wide flange beam",
    "STL CHAN":      "steel channel",
    "STL HSS":       "steel hss",
    "STL PIPE":      "steel pipe",
    "STL DECK":      "steel deck",
    "CU PIPE":       "copper pipe",
    "CU RW90":       "copper conductor rw90",
    "BATT INSUL MW": "batt insulation, mineral wool,",
    "BATT INSUL FG": "batt insulation, fibreglass,",

    "RIGID INSUL":   "rigid insulation",
    "SPRAY FOAM INSULATION": "spray foam insulation",
    "COPPER CONDUCTOR": "copper conductor",
    "COPPER PIPE":   "copper pipe",
    "STONE VENEER":  "stone veneer",
    "PEX TUBING":    "pex tubing",
    "METAL STUD":    "metal stud",
    "STEEL DECK":    "steel deck",
    "CARPET TILE":   "carpet tile",
    "LUXURY VINYL TILE": "luxury vinyl tile",
    "RESILIENT SHEET FLOORING": "resilient sheet flooring",
    "RUBBER BASE":   "rubber base",
    "EPOXY FLOOR COATING": "epoxy floor coating",
    "PORC TILE":     "porcelain tile",
    "GLULAM BEAM":   "glulam beam",
    "HOLLOW METAL DOOR FRAME": "hollow metal door frame",
    "HOLLOW METAL DOOR": "hollow metal door",
    "OVERHEAD SECTIONAL DOOR": "overhead sectional door",
    "CONCRETE SIDEWALK": "concrete sidewalk",
    "PNT INT LTX":   "paint interior latex",
}

_SINGLE_TOKEN_ABBREVS: dict[str, str] = {
    "CONC":    "concrete",
    "RM":      "ready-mix",
    "INSUL":   "insulation",
    "MW":      "mineral wool",
    "FG":      "fibreglass",
    "STL":     "steel",
    "BM":      "beam",
    "CHAN":     "channel",
    "HSS":     "hss",
    "GYP":     "gypsum",
    "BD":      "board",
    "PLYWD":   "plywood sheathing",
    "DFIR":    "douglas fir",
    "PNT":     "paint",
    "INT":     "interior",
    "LTX":     "latex",
    "ASPH":    "asphalt",
    "PVG":     "paving",
    "CU":      "copper",
    "PORC":    "porcelain",
    "GRAN":    "granular",
    "LBR":     "dimensional lumber",
    "SPF":     "spf",
    "PT":      "pressure treated",
    "CMU":     "concrete masonry unit",
    "LW":      "lightweight",
    "EMT":     "emt conduit",
    "REBAR":   "reinforcing steel bar",
    "GR400":   "grade 400w",
    "EPDM":    "epdm",
    "UNFACED": "unfaced",
    "SCH40":   "schedule 40",
    "BLK":     "black",
    "GALVANIZED": "galvanized",
    "EPOXY":   "epoxy coated",
    "FA":      "fly ash",
    "SLAG":    "slag",
    "MPA":     "mpa",
    "MPa":     "mpa",
    "AWG":     "awg",
    "COVED":   "coved",
    "HOMOGENEOUS": "homogeneous",
    "MORTAR":  "mortar",
    "GROUT":   "grout",
    "CEMENTITIOUS": "cementitious",
    "ALUMINUM": "aluminum",
    "THERMALLY": "thermally",
    "BROKEN":  "broken",
    "INSULATED": "insulated",
}


# Precompile multi-token patterns sorted longest-first for greedy matching.
_MULTI_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(re.escape(k), re.IGNORECASE), v)
    for k, v in sorted(_MULTI_TOKEN_ABBREVS.items(), key=lambda x: -len(x[0]))
]


def _expand_abbreviations(text: str) -> str:
    """Replace known abbreviations with their catalog-prose equivalents.

    Multi-token abbreviations are applied first (longest match wins), then
    remaining single tokens are expanded individually.
    """
    # Multi-token pass.
    for pattern, replacement in _MULTI_PATTERNS:
        text = pattern.sub(replacement, text)

    # Single-token pass — split on whitespace, replace known tokens.
    tokens = text.split()
    expanded: list[str] = []
    for tok in tokens:
        upper = tok.upper()
        if upper in _SINGLE_TOKEN_ABBREVS:
            expanded.append(_SINGLE_TOKEN_ABBREVS[upper])
        else:
            expanded.append(tok)
    return " ".join(expanded)


def _normalize_shortcuts(text: str) -> str:
    """Handle common shorthand patterns that aren't simple token swaps."""
    # W/ → with  (common construction shorthand)
    # \b doesn't work before / since / is not a word character, so we use
    # a lookbehind for whitespace or start-of-string instead.
    text = re.sub(r"(?:(?<=\s)|(?<=^))W/(?=\s|$)", "with", text, flags=re.IGNORECASE)
    # # is preserved (needed for AWG gauge like #4/0)
    return text


def _normalize_units(text: str) -> str:
    """Insert space before unit suffixes glued to numbers (e.g. 50MM → 50 mm)."""
    # "50MM" → "50 mm", "5/8in" → "5/8 in", "30MPa" → "30 mpa"
    text = re.sub(
        r"(\d)(MM|IN|mm|in|MPA|MPa|mpa)\b",
        r"\1 \2",
        text,
        flags=re.IGNORECASE,
    )
    return text


def _normalize_grade(text: str) -> str:
    """Normalize steel grade references.

    'GR B' / 'GR C' → 'grade b' / 'grade c' to align with catalog
    phrasing like 'ASTM A500 Grade B'.
    """
    text = re.sub(r"\bGR\s+([A-C])\b", r"grade \1", text, flags=re.IGNORECASE)
    return text


def _normalize_number_prefix(text: str) -> str:
    """Normalize 'NO.1/BTR' → 'no.2' style lumber grade references."""
    text = re.sub(r"\bNO\.1/BTR\b", "no.2", text, flags=re.IGNORECASE)
    return text


def normalize_text(raw: str) -> str:
    """Normalize a raw text string for matching.

    Steps (order matters):
    1. Normalize shortcuts (W/ → with)
    2. Normalize unit suffixes glued to numbers
    3. Normalize grade references
    4. Normalize lumber grade references
    5. Collapse whitespace
    6. Lowercase
    7. Expand abbreviations (multi-token first, then single-token)
    8. Final whitespace cleanup

    The result is a lowercase string with abbreviations expanded to catalog
    prose, suitable for token-set similarity comparison.
    """
    if not raw:
        return ""

    text = raw.strip()
    text = _normalize_shortcuts(text)
    text = _normalize_units(text)
    text = _normalize_grade(text)
    text = _normalize_number_prefix(text)
    # Collapse multi-spaces to single space.
    text = re.sub(r"\s+", " ", text)
    text = text.lower()
    text = _expand_abbreviations(text)
    # Final cleanup.
    text = re.sub(r"\s+", " ", text).strip()
    return text
