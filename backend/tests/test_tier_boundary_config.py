"""Tier boundaries are driven by config/settings.yaml, not hardcoded.

Task 07 asks for "tier-boundary behavior driven by config values". The Issue #2
reproduction (test_boundary.py) pins the *inclusive lower bound* semantics with
literal thresholds; this file proves the boundaries actually track the config:

  1. `assign_tier`'s boundaries follow the LIVE settings.yaml values, and
  2. swapping the config (via SPECMATCH_CONFIG) moves those boundaries — both at
     the `assign_tier` level and end-to-end through the matching engine, whose
     tier for a fixed score flips when only the threshold changes.

The swap fixture restores SPECMATCH_CONFIG and clears the get_settings lru_cache
on teardown (per CLAUDE.md's cache-clear rule) so the rest of the session reads
the real config again.
"""

import os
import sqlite3
import tempfile
from datetime import datetime, timezone

import pytest
import yaml

from app.config import get_settings
from app.core.db import init_schema
from app.models.schemas import RecordOut, Tier
from app.services.ingest import ingest_catalog
from app.services.matching.engine import LexicalMatchingEngine
from app.services.matching.tiering import assign_tier

# The four scoring signals are a contract shared with settings.yaml; a swapped
# config still has to carry them for get_settings() to parse.
_WEIGHTS = {
    "string_similarity": 0.45,
    "category_agreement": 0.20,
    "unit_compatibility": 0.10,
    "attribute_match": 0.25,
}


# ---------------------------------------------------------------------------
# 1. Boundaries follow the live config (no literals)
# ---------------------------------------------------------------------------


def test_assign_tier_boundaries_follow_live_config():
    """Read the thresholds from settings.yaml and confirm assign_tier honours
    them as inclusive lower bounds — at the boundary, and just below it."""
    t = get_settings().tiers
    tiny = 1e-9

    assert assign_tier(t.accept_min, t) is Tier.green
    assert assign_tier(t.accept_min - tiny, t) is Tier.yellow
    assert assign_tier(t.review_min, t) is Tier.yellow
    assert assign_tier(t.review_min - tiny, t) is Tier.red


# ---------------------------------------------------------------------------
# Config-swap fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def use_config(tmp_path):
    """Activate a settings.yaml with the given thresholds and return the reloaded
    Settings. Restores the env var and clears the settings cache on teardown."""
    saved = os.environ.get("SPECMATCH_CONFIG")

    def _activate(accept_min, review_min):
        cfg = {
            "matching": {"top_k": 5, "weights": dict(_WEIGHTS)},
            "tiers": {"accept_min": accept_min, "review_min": review_min},
        }
        path = tmp_path / "settings.yaml"
        path.write_text(yaml.safe_dump(cfg))
        os.environ["SPECMATCH_CONFIG"] = str(path)
        get_settings.cache_clear()
        return get_settings()

    yield _activate

    if saved is None:
        os.environ.pop("SPECMATCH_CONFIG", None)
    else:
        os.environ["SPECMATCH_CONFIG"] = saved
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 2. Swapping the config moves the boundaries
# ---------------------------------------------------------------------------


def test_thresholds_are_read_from_yaml(use_config):
    s = use_config(accept_min=0.90, review_min=0.50)
    assert s.tiers.accept_min == 0.90
    assert s.tiers.review_min == 0.50


def test_boundaries_move_with_swapped_config(use_config):
    """A score fixed in the middle changes tier when the thresholds around it
    move — proof the boundary is the config value, not a constant in code."""
    t = use_config(accept_min=0.90, review_min=0.50).tiers

    # 0.85 is green under the default accept_min (0.85), yellow under 0.90.
    assert assign_tier(0.85, t) is Tier.yellow
    # 0.55 is red under the default review_min (0.60), yellow under 0.50.
    assert assign_tier(0.55, t) is Tier.yellow
    # Extremes still resolve as expected.
    assert assign_tier(0.95, t) is Tier.green
    assert assign_tier(0.45, t) is Tier.red


# ---------------------------------------------------------------------------
# 3. End-to-end: the engine's tier for a fixed score tracks the config
# ---------------------------------------------------------------------------


@pytest.fixture
def catalog_conn():
    """Temp DB with the catalog ingested (records aren't needed to score a
    hand-built RecordOut; matches has no FK to records)."""
    with tempfile.TemporaryDirectory() as tmp:
        saved = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = tmp

        conn = sqlite3.connect(os.path.join(tmp, "specmatch.db"))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        init_schema(conn)
        ingest_catalog(conn)

        try:
            yield conn
        finally:
            conn.close()
            if saved is None:
                os.environ.pop("DATA_DIR", None)
            else:
                os.environ["DATA_DIR"] = saved


def _conc_record():
    return RecordOut(
        record_id="SRC-0054",
        raw_text="CONC RM 30MPa w/ 25% FA",
        category="Concrete",
        unit="m3",
        ingested_at=datetime.now(timezone.utc),
    )


def test_engine_tier_flips_when_only_the_threshold_moves(use_config, catalog_conn):
    """SRC-0054 scores ~0.961. It is green under the default accept_min (0.85);
    raise accept_min above the score via config and the *same* record — same
    score — lands yellow. The score is unchanged; only the tier boundary moved.

    The engine reads its thresholds from get_settings() at construction, so each
    leg builds a fresh engine after the config is (re)activated.
    """
    record = _conc_record()

    # Default thresholds → green.
    use_config(accept_min=0.85, review_min=0.60)
    green = LexicalMatchingEngine(catalog_conn).match_record(record)
    assert green.tier is Tier.green
    assert green.candidates[0].score >= 0.90  # comfortably above 0.85

    # Raise accept_min above the score → yellow, without touching the score.
    use_config(accept_min=0.99, review_min=0.60)
    yellow = LexicalMatchingEngine(catalog_conn).match_record(record)
    assert yellow.tier is Tier.yellow
    assert yellow.candidates[0].score == green.candidates[0].score
