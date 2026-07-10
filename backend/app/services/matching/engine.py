"""The matching engine.

Orchestrates retrieval, scoring, tier assignment, and persistence.

Design constraints (from interfaces.py):
- Implement the interfaces in interfaces.py.
- Composite scores combine at least two distinct signals; the weights come
  from Settings.matching.weights (config/settings.yaml).
- Tier assignment goes through tiering.assign_tier with thresholds from
  Settings.tiers.
- Persist the top-k candidates per record (Settings.matching.top_k) with
  enough information to answer: what matched, with what score, from which
  signals, and why it landed in its tier.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from app.config import get_settings
from app.core.db import get_conn
from app.core.errors import DependencyError
from app.core.logging import log_event
from app.models.schemas import (
    CatalogEntry,
    Candidate,
    MatchResult,
    RecordOut,
    Tier,
)
from app.services.matching.interfaces import MatchingEngine
from app.services.matching.retrieval import LexicalRetriever
from app.services.matching.scoring import LexicalScorer
from app.services.matching.tiering import assign_tier

logger = logging.getLogger(__name__)


class LexicalMatchingEngine(MatchingEngine):
    """Retrieval + scoring over the ingested catalog.

    Constructor takes a database connection, loads the catalog, and
    initialises the retriever and scorer.  ``match_all()`` processes
    every ingested source record.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._settings = get_settings()
        self._retriever = LexicalRetriever()
        self._scorer = LexicalScorer()
        self._catalog = self._load_catalog()

    # ------------------------------------------------------------------
    # Data loading helpers
    # ------------------------------------------------------------------

    def _load_catalog(self) -> list[CatalogEntry]:
        """Read the full catalog from the database."""
        try:
            rows = self._conn.execute(
                "SELECT catalog_id, description, category, unit FROM catalog"
            ).fetchall()
        except sqlite3.Error as exc:
            log_event(
                logger,
                logging.ERROR,
                "dependency_failure",
                dependency="sqlite",
                operation="load_catalog",
                error=str(exc),
            )
            raise DependencyError("could not load catalog from database") from exc

        return [
            CatalogEntry(
                catalog_id=row["catalog_id"],
                description=row["description"],
                category=row["category"],
                unit=row["unit"],
            )
            for row in rows
        ]

    def _load_records(self) -> list[RecordOut]:
        """Read all source records from the database."""
        try:
            rows = self._conn.execute(
                "SELECT record_id, raw_text, category, unit, quantity, ingested_at"
                " FROM records ORDER BY id"
            ).fetchall()
        except sqlite3.Error as exc:
            log_event(
                logger,
                logging.ERROR,
                "dependency_failure",
                dependency="sqlite",
                operation="load_records",
                error=str(exc),
            )
            raise DependencyError("could not load records from database") from exc

        return [
            RecordOut(
                record_id=row["record_id"],
                raw_text=row["raw_text"],
                category=row["category"] or None,
                unit=row["unit"] or None,
                quantity=row["quantity"] or None,
                ingested_at=row["ingested_at"],
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist_match(self, result: MatchResult) -> None:
        """Write a MatchResult to the matches table."""
        payload = result.model_dump_json()
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO matches (record_id, payload, tier, matched_at)"
                " VALUES (?, ?, ?, ?)",
                (
                    result.record_id,
                    payload,
                    result.tier.value,
                    result.matched_at.isoformat(),
                ),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            log_event(
                logger,
                logging.ERROR,
                "dependency_failure",
                dependency="sqlite",
                operation="persist_match",
                record_id=result.record_id,
                error=str(exc),
            )
            raise DependencyError(
                f"could not persist match for {result.record_id}"
            ) from exc

    # ------------------------------------------------------------------
    # Core matching logic
    # ------------------------------------------------------------------

    def match_record(self, record: RecordOut) -> MatchResult:
        """Produce (and persist) the MatchResult for one source record."""
        top_k = self._settings.matching.top_k
        thresholds = self._settings.tiers

        # 1. Retrieve plausible candidates (full catalog at fixture scale).
        entries = self._retriever.retrieve(record, self._catalog, limit=0)

        # 2. Score every candidate.
        scored: list[Candidate] = [
            self._scorer.score(record, entry) for entry in entries
        ]

        # 3. Sort by score descending, take top-k.
        scored.sort(key=lambda c: c.score, reverse=True)
        top_candidates = scored[:top_k]

        # 4. Assign tier based on best candidate's score.
        best_score = top_candidates[0].score if top_candidates else 0.0
        tier = assign_tier(best_score, thresholds)

        # 5. Auto-select top candidate for green matches.
        selected_id = (
            top_candidates[0].catalog_id
            if tier == Tier.green and top_candidates
            else None
        )

        # 6. Build MatchResult.
        now = datetime.now(timezone.utc)
        result = MatchResult(
            record_id=record.record_id,
            source_text=record.raw_text,
            tier=tier,
            candidates=top_candidates,
            selected_catalog_id=selected_id,
            review=None,
            matched_at=now,
        )

        # 7. Persist.
        self._persist_match(result)

        return result

    def match_all(self) -> list[MatchResult]:
        """Match every ingested source record."""
        records = self._load_records()
        results: list[MatchResult] = []

        for record in records:
            result = self.match_record(record)
            results.append(result)

        # Log tier distribution.
        tier_counts = {t.value: 0 for t in Tier}
        for r in results:
            tier_counts[r.tier.value] += 1

        log_event(
            logger,
            logging.INFO,
            "matching_completed",
            total_records=len(results),
            green=tier_counts["green"],
            yellow=tier_counts["yellow"],
            red=tier_counts["red"],
        )

        return results


def run_matching(conn: sqlite3.Connection | None = None) -> list[MatchResult]:
    """Run the matching engine against all ingested records.

    Called from ``main.py`` lifespan after ingest.  Manages connection
    lifecycle if none is provided.
    """
    owned = conn is None
    if conn is None:
        conn = get_conn()
    try:
        engine = LexicalMatchingEngine(conn)
        return engine.match_all()
    finally:
        if owned:
            conn.close()
