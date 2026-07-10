"""The engine reproduces the specific claims README.md makes about it.

Task 07's headline: the matching tests are the proof that the engine does what
the README says it does. README.md commits to concrete, checkable numbers —

  * a tier distribution over the full 150-record fixture (114 green / 25
    yellow / 11 red, default config), and
  * two worked exemplars (a confident green and a deliberate red).

These tests lock those claims against the real pipeline (retrieval → scoring →
tiering over the ingested fixture). If the engine is retuned they fail until
README.md is updated to match — which is the point: the README and the engine
are not allowed to drift apart silently.

Coverage here is deliberately engine-level and end-to-end; the per-signal unit
behaviour lives in test_scoring.py / test_normalize.py, and the pure tier
boundary lives in test_tier_boundary_config.py.
"""

import os
import sqlite3
import tempfile
from datetime import datetime, timezone

import pytest

from app.core.db import init_schema
from app.models.schemas import RecordOut, Tier
from app.services.ingest import ingest_catalog, ingest_records
from app.services.matching.engine import LexicalMatchingEngine

# README.md → "Tier distribution (full fixture, default config)".
README_DISTRIBUTION = {Tier.green: 114, Tier.yellow: 25, Tier.red: 11}


@pytest.fixture(scope="module")
def engine():
    """Engine over a temp DB with the full fixture (catalog + records) ingested."""
    with tempfile.TemporaryDirectory() as tmp:
        saved = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = tmp

        conn = sqlite3.connect(os.path.join(tmp, "specmatch.db"))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        init_schema(conn)
        ingest_catalog(conn)
        ingest_records(conn)

        try:
            yield LexicalMatchingEngine(conn)
        finally:
            conn.close()
            if saved is None:
                os.environ.pop("DATA_DIR", None)
            else:
                os.environ["DATA_DIR"] = saved


@pytest.fixture(scope="module")
def results(engine):
    """The full match run, computed once and shared across the README claims."""
    return engine.match_all()


@pytest.fixture(scope="module")
def by_id(results):
    return {r.record_id: r for r in results}


# ---------------------------------------------------------------------------
# The headline distribution claim
# ---------------------------------------------------------------------------


def test_full_fixture_distribution_matches_readme(results):
    """The 114 / 25 / 11 split README publishes is exactly what the engine
    produces — the whole point of a "meaningfully distributed" set of tiers.

    If you retune weights or thresholds, this will fail: update the README
    distribution table (and the exemplars below) in the same change.
    """
    assert len(results) == 150

    counts = {tier: 0 for tier in Tier}
    for r in results:
        counts[r.tier] += 1

    assert counts == README_DISTRIBUTION, (
        "distribution drifted from README.md: "
        f"{counts[Tier.green]}G / {counts[Tier.yellow]}Y / {counts[Tier.red]}R"
    )
    # Neither greens nor yellows swallow the set (the engine discriminates).
    assert counts[Tier.green] > counts[Tier.yellow] > counts[Tier.red]


# ---------------------------------------------------------------------------
# The two worked exemplars
# ---------------------------------------------------------------------------


def test_green_exemplar_conc_rm(by_id):
    """README: `CONC RM 30MPa w/ 25% FA` → green 0.961 → CAT-0015 (auto-select)."""
    conc = by_id["SRC-0054"]
    assert conc.tier is Tier.green
    top = conc.candidates[0]
    assert top.catalog_id == "CAT-0015", f"got {top.catalog_id}: {top.description}"
    # Lock the exact score README publishes for this exemplar.
    assert top.score == pytest.approx(0.961, abs=0.01), f"README quotes 0.961, got {top.score}"
    # Only green auto-selects, and it selects the top candidate.
    assert conc.selected_catalog_id == "CAT-0015"


def test_red_exemplar_misc_mtl_allow(by_id):
    """README: `MISC MTL ALLOW` → red 0.346 (no genuine match, no selection).

    The deliberate red the task asks for: pure junk with no category or unit
    that no catalog entry genuinely matches. Locks both the red tier and the
    exact score README publishes, and that nothing is auto-selected.
    """
    junk = by_id["SRC-0074"]
    assert junk.tier is Tier.red
    score = junk.candidates[0].score
    assert score < 0.60, "must fall below review_min to be red"
    assert score == pytest.approx(0.346, abs=0.01), f"README quotes 0.346, got {score}"
    assert junk.selected_catalog_id is None


# ---------------------------------------------------------------------------
# A constructed record sequence spanning all three tiers
# ---------------------------------------------------------------------------

# Each row is copied verbatim from data/source_records.csv; the expected tier
# is the queue README.md's distribution places it in. The sequence deliberately
# includes multiple reds — a suite that never lands a red isn't proving the
# engine can tell junk from a real match.
SEQUENCE = [
    # raw_text,                       category,           unit,  expected tier
    ("CONC RM 30MPa w/ 25% FA",       "Concrete",         "m3",  Tier.green),
    ("BATT  INSUL MW R-22",           "Insulation",       "m2",  Tier.green),
    ("STL HSS 6x6x1/4",               "Structural Steel", "kg",  Tier.yellow),
    ("ASSORTED ANCHORS + FASTENERS",  "Miscellaneous",    "kg",  Tier.yellow),
    ("MISC MTL ALLOW",                None,               None,  Tier.red),
    ("XX DO NOT USE XX",              None,               None,  Tier.red),
    ("MOB/DEMOB",                     None,               None,  Tier.red),
]


@pytest.mark.parametrize("raw_text,category,unit,expected", SEQUENCE)
def test_constructed_records_land_in_expected_tier(
    engine, raw_text, category, unit, expected
):
    record = RecordOut(
        record_id="SRC-SEQ",
        raw_text=raw_text,
        category=category,
        unit=unit,
        ingested_at=datetime.now(timezone.utc),
    )
    result = engine.match_record(record)
    assert result.tier is expected, (
        f"{raw_text!r} landed {result.tier.value} "
        f"(score {result.candidates[0].score}); README expects {expected.value}"
    )


def test_sequence_includes_a_deliberate_red():
    """Guard rail on the fixture above: at least one row must be a red."""
    assert any(expected is Tier.red for *_, expected in SEQUENCE)
