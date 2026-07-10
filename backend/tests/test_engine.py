"""Integration tests for the matching engine.

These tests use a real (in-memory or temporary) database with ingested
fixture data to verify the full match pipeline: retrieval → scoring →
tiering → persistence → readback.
"""

import json
import os
import sqlite3
import tempfile

import pytest

from app.core.db import init_schema
from app.models.schemas import MatchResult, Tier
from app.services.ingest import ingest_catalog, ingest_records
from app.services.matching.engine import LexicalMatchingEngine, run_matching


@pytest.fixture
def engine_conn():
    """Provide a temporary database with catalog + records ingested."""
    with tempfile.TemporaryDirectory() as tmp:
        old_data_dir = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = tmp

        db_path = os.path.join(tmp, "specmatch.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        init_schema(conn)
        ingest_catalog(conn)
        ingest_records(conn)

        yield conn

        conn.close()
        if old_data_dir is not None:
            os.environ["DATA_DIR"] = old_data_dir
        else:
            os.environ.pop("DATA_DIR", None)


@pytest.fixture
def engine(engine_conn):
    """Provide a ready-to-use LexicalMatchingEngine instance."""
    return LexicalMatchingEngine(engine_conn)


# ---------------------------------------------------------------------------
# match_record tests
# ---------------------------------------------------------------------------

class TestMatchRecord:
    """Test matching a single record."""

    def test_produces_valid_match_result(self, engine, engine_conn):
        """match_record should return a MatchResult with correct structure."""
        from datetime import datetime, timezone
        from app.models.schemas import RecordOut

        record = RecordOut(
            record_id="SRC-0054",
            raw_text="CONC RM 30MPa w/ 25% FA",
            category="Concrete",
            unit="m3",
            ingested_at=datetime.now(timezone.utc),
        )
        result = engine.match_record(record)

        assert isinstance(result, MatchResult)
        assert result.record_id == "SRC-0054"
        assert result.source_text == "CONC RM 30MPa w/ 25% FA"
        assert result.tier in [Tier.green, Tier.yellow, Tier.red]
        assert len(result.candidates) > 0
        assert len(result.candidates) <= 5  # top_k from config

    def test_candidates_have_signal_breakdown(self, engine, engine_conn):
        from datetime import datetime, timezone
        from app.models.schemas import RecordOut

        record = RecordOut(
            record_id="SRC-0054",
            raw_text="CONC RM 30MPa w/ 25% FA",
            category="Concrete",
            unit="m3",
            ingested_at=datetime.now(timezone.utc),
        )
        result = engine.match_record(record)

        for candidate in result.candidates:
            assert "string_similarity" in candidate.signals
            assert 0.0 <= candidate.score <= 1.0

    def test_candidates_sorted_by_score(self, engine, engine_conn):
        from datetime import datetime, timezone
        from app.models.schemas import RecordOut

        record = RecordOut(
            record_id="SRC-0054",
            raw_text="CONC RM 30MPa w/ 25% FA",
            category="Concrete",
            unit="m3",
            ingested_at=datetime.now(timezone.utc),
        )
        result = engine.match_record(record)

        scores = [c.score for c in result.candidates]
        assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# Tier assignment tests
# ---------------------------------------------------------------------------

class TestTierAssignment:
    """Verify the engine assigns tiers correctly using config thresholds."""

    def test_concrete_record_lands_green(self, engine):
        """SRC-0054 (CONC RM 30MPa w/ 25% FA) should match confidently."""
        from datetime import datetime, timezone
        from app.models.schemas import RecordOut

        record = RecordOut(
            record_id="SRC-0054",
            raw_text="CONC RM 30MPa w/ 25% FA",
            category="Concrete",
            unit="m3",
            ingested_at=datetime.now(timezone.utc),
        )
        result = engine.match_record(record)
        assert result.tier == Tier.green, (
            f"Expected green, got {result.tier.value} with score {result.candidates[0].score}"
        )

    def test_insulation_record_lands_green(self, engine):
        """SRC-0001 (BATT INSUL MW R-22) should match confidently."""
        from datetime import datetime, timezone
        from app.models.schemas import RecordOut

        record = RecordOut(
            record_id="SRC-0001",
            raw_text="BATT  INSUL MW R-22",
            category="Insulation",
            unit="m2",
            ingested_at=datetime.now(timezone.utc),
        )
        result = engine.match_record(record)
        assert result.tier == Tier.green, (
            f"Expected green, got {result.tier.value} with score {result.candidates[0].score}"
        )

    def test_junk_record_lands_red(self, engine):
        """MISC MTL ALLOW should land red — no meaningful catalog match."""
        from datetime import datetime, timezone
        from app.models.schemas import RecordOut

        record = RecordOut(
            record_id="SRC-0074",
            raw_text="MISC MTL ALLOW",
            category=None,
            unit=None,
            ingested_at=datetime.now(timezone.utc),
        )
        result = engine.match_record(record)
        assert result.tier == Tier.red, (
            f"Expected red, got {result.tier.value} with score {result.candidates[0].score}"
        )


# ---------------------------------------------------------------------------
# Auto-select tests
# ---------------------------------------------------------------------------

class TestAutoSelect:
    """Green matches should auto-select the top candidate."""

    def test_green_auto_selects(self, engine):
        from datetime import datetime, timezone
        from app.models.schemas import RecordOut

        record = RecordOut(
            record_id="SRC-0054",
            raw_text="CONC RM 30MPa w/ 25% FA",
            category="Concrete",
            unit="m3",
            ingested_at=datetime.now(timezone.utc),
        )
        result = engine.match_record(record)
        if result.tier == Tier.green:
            assert result.selected_catalog_id is not None
            assert result.selected_catalog_id == result.candidates[0].catalog_id

    def test_red_does_not_auto_select(self, engine):
        from datetime import datetime, timezone
        from app.models.schemas import RecordOut

        record = RecordOut(
            record_id="SRC-0074",
            raw_text="MISC MTL ALLOW",
            category=None,
            unit=None,
            ingested_at=datetime.now(timezone.utc),
        )
        result = engine.match_record(record)
        if result.tier == Tier.red:
            assert result.selected_catalog_id is None


# ---------------------------------------------------------------------------
# Persistence tests
# ---------------------------------------------------------------------------

class TestPersistence:
    """Verify match results are persisted to the database."""

    def test_match_persisted_to_db(self, engine, engine_conn):
        from datetime import datetime, timezone
        from app.models.schemas import RecordOut

        record = RecordOut(
            record_id="SRC-0054",
            raw_text="CONC RM 30MPa w/ 25% FA",
            category="Concrete",
            unit="m3",
            ingested_at=datetime.now(timezone.utc),
        )
        engine.match_record(record)

        row = engine_conn.execute(
            "SELECT record_id, payload, tier FROM matches WHERE record_id = ?",
            ("SRC-0054",),
        ).fetchone()

        assert row is not None
        assert row["record_id"] == "SRC-0054"
        assert row["tier"] in ("green", "yellow", "red")

        # Verify payload deserializes correctly.
        payload = json.loads(row["payload"])
        assert payload["record_id"] == "SRC-0054"
        assert "candidates" in payload
        assert len(payload["candidates"]) > 0

    def test_readback_matches_original(self, engine, engine_conn):
        from datetime import datetime, timezone
        from app.models.schemas import RecordOut

        record = RecordOut(
            record_id="SRC-0054",
            raw_text="CONC RM 30MPa w/ 25% FA",
            category="Concrete",
            unit="m3",
            ingested_at=datetime.now(timezone.utc),
        )
        original = engine.match_record(record)

        row = engine_conn.execute(
            "SELECT payload FROM matches WHERE record_id = ?",
            ("SRC-0054",),
        ).fetchone()
        readback = MatchResult.model_validate_json(row["payload"])

        assert readback.record_id == original.record_id
        assert readback.tier == original.tier
        assert len(readback.candidates) == len(original.candidates)


# ---------------------------------------------------------------------------
# match_all tests
# ---------------------------------------------------------------------------

class TestMatchAll:
    """Test the full engine run across all records."""

    def test_match_all_processes_all_records(self, engine, engine_conn):
        """match_all should produce results for every ingested record."""
        results = engine.match_all()

        # There are 150 source records in the fixture.
        assert len(results) == 150

    def test_match_all_tier_distribution(self, engine, engine_conn):
        """The tier distribution should reflect honest assessment:
        - Good construction records → mostly green
        - Junk/vague records → red
        - Not everything in yellow (that dodges the problem)
        """
        results = engine.match_all()

        greens = sum(1 for r in results if r.tier == Tier.green)
        yellows = sum(1 for r in results if r.tier == Tier.yellow)
        reds = sum(1 for r in results if r.tier == Tier.red)

        # Sanity: we should have at least some of each tier.
        assert greens > 0, "No green results — engine is too conservative"
        assert reds > 0, "No red results — engine is too generous"
        # Green should be the majority for well-structured records.
        assert greens > yellows, (
            f"Expected more greens than yellows: {greens}G/{yellows}Y/{reds}R"
        )

    def test_match_all_persists_all(self, engine, engine_conn):
        """All results should be persisted in the matches table."""
        engine.match_all()

        count = engine_conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        assert count == 150


# ---------------------------------------------------------------------------
# Spot-check known matches
# ---------------------------------------------------------------------------

class TestKnownMatches:
    """Verify specific record→catalog matches land correctly."""

    def test_src_0054_to_cat_0015(self, engine):
        """CONC RM 30MPa w/ 25% FA → Ready-mix concrete, 30 MPa, 25% fly ash."""
        from datetime import datetime, timezone
        from app.models.schemas import RecordOut

        record = RecordOut(
            record_id="SRC-0054",
            raw_text="CONC RM 30MPa w/ 25% FA",
            category="Concrete",
            unit="m3",
            ingested_at=datetime.now(timezone.utc),
        )
        result = engine.match_record(record)
        top = result.candidates[0]
        assert top.catalog_id == "CAT-0015", (
            f"Expected CAT-0015, got {top.catalog_id}: {top.description}"
        )

    def test_src_0109_hss_match(self, engine):
        """STL HSS 6x6x1/4 should match a Steel HSS 6x6x1/4 entry."""
        from datetime import datetime, timezone
        from app.models.schemas import RecordOut

        record = RecordOut(
            record_id="SRC-0109",
            raw_text="STL HSS 6x6x1/4",
            category="Structural Steel",
            unit="kg",
            ingested_at=datetime.now(timezone.utc),
        )
        result = engine.match_record(record)
        top = result.candidates[0]
        assert "6x6x1/4" in top.description.lower(), (
            f"Expected HSS 6x6x1/4 match, got: {top.description}"
        )


# ---------------------------------------------------------------------------
# run_matching convenience function
# ---------------------------------------------------------------------------

class TestRunMatching:
    """Test the run_matching() convenience function."""

    def test_run_matching_with_conn(self, engine_conn):
        results = run_matching(engine_conn)
        assert len(results) == 150
