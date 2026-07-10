"""Tests for the scoring module."""

import pytest
from datetime import datetime, timezone

from app.models.schemas import CatalogEntry, RecordOut
from app.services.matching.scoring import (
    LexicalScorer,
    extract_attributes,
    token_set_similarity,
    _category_agreement,
    _unit_compatibility,
    _attribute_match,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(raw_text: str, category: str | None = None,
                 unit: str | None = None) -> RecordOut:
    return RecordOut(
        record_id="SRC-TEST",
        raw_text=raw_text,
        category=category,
        unit=unit,
        ingested_at=datetime.now(timezone.utc),
    )


def _make_entry(description: str, category: str = "Concrete",
                unit: str = "m3") -> CatalogEntry:
    return CatalogEntry(
        catalog_id="CAT-TEST",
        description=description,
        category=category,
        unit=unit,
    )


# ---------------------------------------------------------------------------
# token_set_similarity tests
# ---------------------------------------------------------------------------

class TestTokenSetSimilarity:
    """Token-set similarity using difflib."""

    def test_identical_strings(self):
        assert token_set_similarity("hello world", "hello world") == 1.0

    def test_reordered_tokens(self):
        score = token_set_similarity("foo bar baz", "baz foo bar")
        assert score >= 0.9, f"Reordered tokens should score high, got {score}"

    def test_disjoint_strings(self):
        score = token_set_similarity("abc def", "xyz uvw")
        assert score < 0.3, f"Disjoint strings should score low, got {score}"

    def test_empty_string_a(self):
        assert token_set_similarity("", "hello") == 0.0

    def test_empty_string_b(self):
        assert token_set_similarity("hello", "") == 0.0

    def test_both_empty(self):
        assert token_set_similarity("", "") == 0.0

    def test_partial_overlap(self):
        score = token_set_similarity("ready-mix concrete 30 mpa", "ready-mix concrete 35 mpa")
        assert 0.5 < score < 1.0, f"Partial overlap should be moderate, got {score}"

    def test_subset(self):
        """When one string is a subset of the other, score should be high."""
        score = token_set_similarity("concrete 30 mpa", "ready-mix concrete 30 mpa 25% fly ash")
        assert score >= 0.6, f"Subset should score reasonably high, got {score}"


# ---------------------------------------------------------------------------
# extract_attributes tests
# ---------------------------------------------------------------------------

class TestExtractAttributes:
    """Verify structured spec extraction is conservative and correct."""

    def test_mpa_value(self):
        attrs = extract_attributes("Ready-mix concrete, 30 MPa")
        assert "30mpa" in attrs

    def test_mpa_glued(self):
        attrs = extract_attributes("CONC RM 30MPA")
        assert "30mpa" in attrs

    def test_r_value(self):
        attrs = extract_attributes("Batt insulation, mineral wool, R-22")
        assert "r-22" in attrs

    def test_awg_gauge(self):
        attrs = extract_attributes("Copper conductor, RW90, #4/0 AWG")
        assert "#4/0awg" in attrs

    def test_awg_gauge_no_hash(self):
        attrs = extract_attributes("12 AWG")
        assert "#12awg" in attrs

    def test_hss_dimensions(self):
        attrs = extract_attributes("Steel HSS 6x6x1/4, ASTM A500")
        assert "6x6x1/4" in attrs

    def test_percentage(self):
        attrs = extract_attributes("25% fly ash")
        assert "25%" in attrs

    def test_mm_size(self):
        attrs = extract_attributes("50 mm thick")
        assert "50mm" in attrs

    def test_mm_size_glued(self):
        attrs = extract_attributes("600MM wide")
        assert "600mm" in attrs

    def test_wbeam(self):
        attrs = extract_attributes("W360X57")
        assert "w360x57" in attrs

    def test_channel(self):
        attrs = extract_attributes("C310X31")
        assert "c310x31" in attrs

    def test_rebar_size(self):
        attrs = extract_attributes("Reinforcing steel bar, 15M, Grade 400W")
        assert "15m" in attrs

    def test_grade(self):
        attrs = extract_attributes("Grade 400W")
        assert "grade400w" in attrs

    def test_fly_ash(self):
        attrs = extract_attributes("25% fly ash")
        assert "flyash" in attrs

    def test_slag(self):
        attrs = extract_attributes("25% slag")
        assert "slag" in attrs

    def test_air_entrained(self):
        attrs = extract_attributes("air entrained concrete")
        assert "airentrained" in attrs

    def test_type(self):
        attrs = extract_attributes("Type X fire rated")
        assert "typex" in attrs

    def test_schedule(self):
        attrs = extract_attributes("Schedule 40 black")
        assert "schedule40" in attrs

    def test_lumber_grade(self):
        attrs = extract_attributes("Dimensional lumber, 38x140 mm, SPF No.1/Btr")
        assert "no1/btr" in attrs

    def test_lumber_grade_no2(self):
        attrs = extract_attributes("SPF No. 2")
        assert "no2" in attrs

    def test_facing_unfaced(self):
        attrs = extract_attributes("Batt insulation, fibreglass, R-40, unfaced")
        assert "unfaced" in attrs

    def test_facing_faced(self):
        attrs = extract_attributes("Batt insulation, fibreglass, R-40, faced")
        assert "faced" in attrs

    def test_no_attributes(self):
        attrs = extract_attributes("miscellaneous material allowance")
        assert len(attrs) == 0

    def test_multiple_attributes(self):
        attrs = extract_attributes("Ready-mix concrete, 30 MPa, 25% fly ash, air entrained")
        assert "30mpa" in attrs
        assert "25%" in attrs
        assert "flyash" in attrs
        assert "airentrained" in attrs


# ---------------------------------------------------------------------------
# category_agreement tests
# ---------------------------------------------------------------------------

class TestCategoryAgreement:
    def test_match(self):
        assert _category_agreement("Concrete", "Concrete") == 1.0

    def test_match_case_insensitive(self):
        assert _category_agreement("concrete", "Concrete") == 1.0

    def test_mismatch(self):
        assert _category_agreement("Concrete", "Wood") == 0.0

    def test_missing_record_category(self):
        assert _category_agreement(None, "Concrete") is None

    def test_empty_record_category(self):
        assert _category_agreement("", "Concrete") is None


# ---------------------------------------------------------------------------
# unit_compatibility tests
# ---------------------------------------------------------------------------

class TestUnitCompatibility:
    def test_match(self):
        assert _unit_compatibility("m3", "m3") == 1.0

    def test_match_case_insensitive(self):
        assert _unit_compatibility("M3", "m3") == 1.0

    def test_mismatch(self):
        assert _unit_compatibility("m3", "kg") == 0.0

    def test_missing_record_unit(self):
        assert _unit_compatibility(None, "m3") is None

    def test_empty_record_unit(self):
        assert _unit_compatibility("", "m3") is None


# ---------------------------------------------------------------------------
# attribute_match tests
# ---------------------------------------------------------------------------

class TestAttributeMatch:
    def test_identical_specs(self):
        score = _attribute_match("30 MPa 25% fly ash", "30 MPa 25% fly ash")
        assert score == 1.0

    def test_different_specs(self):
        score = _attribute_match("30 MPa", "50 MPa")
        assert score == 0.0

    def test_partial_overlap(self):
        score = _attribute_match("30 MPa 25% fly ash", "30 MPa 35% fly ash")
        # 30mpa, flyash in common; 25% vs 35% differ → partial
        assert 0.0 < score < 1.0

    def test_no_specs_both(self):
        """Neither has specs → neutral."""
        assert _attribute_match("misc material", "general stuff") is None

    def test_one_side_has_specs(self):
        """Only one side has specs → 0.0."""
        assert _attribute_match("30 MPa concrete", "misc material") == 0.0


# ---------------------------------------------------------------------------
# LexicalScorer (composite) tests
# ---------------------------------------------------------------------------

class TestLexicalScorer:
    """Test the full scorer producing Candidate objects."""

    def test_score_returns_candidate(self):
        scorer = LexicalScorer()
        record = _make_record("CONC RM 30MPA", category="Concrete", unit="m3")
        entry = _make_entry("Ready-mix concrete, 30 MPa", category="Concrete", unit="m3")
        candidate = scorer.score(record, entry)
        assert candidate.catalog_id == "CAT-TEST"
        assert 0.0 <= candidate.score <= 1.0
        assert "string_similarity" in candidate.signals

    def test_perfect_match_scores_high(self):
        scorer = LexicalScorer()
        record = _make_record("CONC RM 30MPA W/ 25% FA", category="Concrete", unit="m3")
        entry = _make_entry("Ready-mix concrete, 30 MPa, 25% fly ash", category="Concrete", unit="m3")
        candidate = scorer.score(record, entry)
        assert candidate.score >= 0.8, f"Perfect match should score >= 0.8, got {candidate.score}"

    def test_category_mismatch_penalizes(self):
        scorer = LexicalScorer()
        record = _make_record("CONC RM 30MPA", category="Concrete", unit="m3")
        entry_match = _make_entry("Ready-mix concrete, 30 MPa", category="Concrete", unit="m3")
        entry_mismatch = _make_entry("Ready-mix concrete, 30 MPa", category="Wood", unit="m3")
        score_match = scorer.score(record, entry_match).score
        score_mismatch = scorer.score(record, entry_mismatch).score
        assert score_match > score_mismatch

    def test_unit_mismatch_penalizes(self):
        scorer = LexicalScorer()
        record = _make_record("CONC RM 30MPA", category="Concrete", unit="m3")
        entry_match = _make_entry("Ready-mix concrete, 30 MPa", category="Concrete", unit="m3")
        entry_mismatch = _make_entry("Ready-mix concrete, 30 MPa", category="Concrete", unit="kg")
        score_match = scorer.score(record, entry_match).score
        score_mismatch = scorer.score(record, entry_mismatch).score
        assert score_match > score_mismatch

    def test_neutral_signals_excluded(self):
        """When record has no category/unit, those signals should be neutral
        (excluded), not penalize the score."""
        scorer = LexicalScorer()
        record_full = _make_record("CONC RM 30MPA", category="Concrete", unit="m3")
        record_sparse = _make_record("CONC RM 30MPA", category=None, unit=None)
        entry = _make_entry("Ready-mix concrete, 30 MPa", category="Concrete", unit="m3")
        score_full = scorer.score(record_full, entry).score
        score_sparse = scorer.score(record_sparse, entry).score
        # Sparse should not be drastically lower — neutral, not penalized
        assert score_sparse >= score_full * 0.6, (
            f"Sparse record scored {score_sparse} vs full {score_full}"
        )

    def test_signal_breakdown_present(self):
        scorer = LexicalScorer()
        record = _make_record("CONC RM 30MPA", category="Concrete", unit="m3")
        entry = _make_entry("Ready-mix concrete, 30 MPa", category="Concrete", unit="m3")
        candidate = scorer.score(record, entry)
        assert "string_similarity" in candidate.signals
        assert "category_agreement" in candidate.signals
        assert "unit_compatibility" in candidate.signals

    def test_junk_record_scores_low(self):
        """Deliberate junk should not get a high score."""
        scorer = LexicalScorer()
        record = _make_record("MISC MTL ALLOW")
        entry = _make_entry("Ready-mix concrete, 30 MPa, 25% fly ash", category="Concrete", unit="m3")
        candidate = scorer.score(record, entry)
        assert candidate.score < 0.5, f"Junk should score low, got {candidate.score}"


class TestKnownFixturePairs:
    """Spot-check known record→catalog pairs from the fixture data."""

    def test_src_0054_matches_cat_0015(self):
        """SRC-0054 (CONC RM 30MPa w/ 25% FA) → CAT-0015."""
        scorer = LexicalScorer()
        record = _make_record("CONC RM 30MPa w/ 25% FA", category="Concrete", unit="m3")
        entry = _make_entry(
            "Ready-mix concrete, 30 MPa, 25% fly ash",
            category="Concrete", unit="m3",
        )
        candidate = scorer.score(record, entry)
        assert candidate.score >= 0.85, (
            f"SRC-0054 should match CAT-0015 confidently, got {candidate.score}"
        )

    def test_src_0001_matches_cat_0186(self):
        """SRC-0001 (BATT INSUL MW R-22) → CAT-0186."""
        scorer = LexicalScorer()
        record = _make_record("BATT  INSUL MW R-22", category="Insulation", unit="m2")
        entry = _make_entry(
            "Batt insulation, mineral wool, R-22",
            category="Insulation", unit="m2",
        )
        candidate = scorer.score(record, entry)
        assert candidate.score >= 0.85, (
            f"SRC-0001 should match CAT-0186 confidently, got {candidate.score}"
        )
