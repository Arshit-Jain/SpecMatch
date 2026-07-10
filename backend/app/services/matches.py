"""Read side of the matches table, plus review persistence.

The matching engine (``services/matching/``) *writes* the initial
``MatchResult`` for every record. This module is the complementary
read/decide layer the API sits on top of: querying persisted matches
(``/matches``) and applying an auditable human review decision
(``/matches/{record_id}/review``).

Routers stay thin — the query filtering, review semantics, and persistence
all live here, behind the same ``DependencyError`` error-handling contract
the rest of the services follow.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from app.core.errors import DependencyError, InvalidReviewError, NotFoundError
from app.core.logging import log_event
from app.models.schemas import (
    MatchResult,
    Review,
    ReviewAction,
    ReviewRequest,
    Tier,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def list_matches(
    conn: sqlite3.Connection,
    tier: Tier | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[MatchResult]]:
    """Return ``(total, page)`` of persisted matches, optionally by tier.

    ``total`` is the full count under the same filter (so a client can
    paginate); ``page`` is the ``limit``/``offset`` window. Ordering is by
    ``record_id``, which — because ids are zero-padded and unique — is the
    ingestion order and is deterministic without joining the non-unique
    ``records.record_id`` column.
    """
    try:
        if tier is None:
            total = conn.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"]
            rows = conn.execute(
                "SELECT payload FROM matches ORDER BY record_id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        else:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM matches WHERE tier = ?",
                (tier.value,),
            ).fetchone()["n"]
            rows = conn.execute(
                "SELECT payload FROM matches WHERE tier = ?"
                " ORDER BY record_id LIMIT ? OFFSET ?",
                (tier.value, limit, offset),
            ).fetchall()
    except sqlite3.Error as exc:
        log_event(
            logger,
            logging.ERROR,
            "dependency_failure",
            dependency="sqlite",
            operation="list_matches",
            tier=tier.value if tier else None,
            error=str(exc),
        )
        raise DependencyError("could not query matches from database") from exc

    items = [MatchResult.model_validate_json(row["payload"]) for row in rows]
    return total, items


def get_match(conn: sqlite3.Connection, record_id: str) -> MatchResult | None:
    """Return the persisted ``MatchResult`` for ``record_id``, or ``None``."""
    try:
        row = conn.execute(
            "SELECT payload FROM matches WHERE record_id = ?",
            (record_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        log_event(
            logger,
            logging.ERROR,
            "dependency_failure",
            dependency="sqlite",
            operation="get_match",
            record_id=record_id,
            error=str(exc),
        )
        raise DependencyError("could not read match from database") from exc

    if row is None:
        return None
    return MatchResult.model_validate_json(row["payload"])


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


def apply_review(
    conn: sqlite3.Connection,
    record_id: str,
    request: ReviewRequest,
) -> MatchResult:
    """Apply and persist a human review decision, returning the updated result.

    Decision semantics (see README / CLAUDE.md):

    * ``accept``   — accept the top candidate; ``selected_catalog_id`` becomes
      ``candidates[0]``. Invalid if the record has no candidates.
    * ``override`` — accept a specific listed candidate; ``catalog_id`` is
      required and must be one of the record's candidates.
    * ``reject``   — no acceptable candidate; ``selected_catalog_id`` cleared.

    The ``Review`` (action, resolved catalog_id, note, ``reviewed_at``) is
    stored on the ``MatchResult`` so the decision is persisted and auditable;
    ``tier`` and ``matched_at`` (the engine's assessment) are left untouched.
    """
    result = get_match(conn, record_id)
    if result is None:
        raise NotFoundError(f"no match found for record_id={record_id!r}")

    selected = _resolve_selection(result, request)

    result.review = Review(
        action=request.action,
        catalog_id=selected,
        note=request.note,
        reviewed_at=datetime.now(timezone.utc),
    )
    result.selected_catalog_id = selected

    _persist_review(conn, result)

    log_event(
        logger,
        logging.INFO,
        "review_persisted",
        record_id=record_id,
        action=request.action.value,
        catalog_id=selected,
        tier=result.tier.value,
    )
    return result


def _resolve_selection(result: MatchResult, request: ReviewRequest) -> str | None:
    """Map a review action to the catalog id it selects (or ``None``).

    Raises ``InvalidReviewError`` for a semantically invalid request.
    """
    if request.action is ReviewAction.accept:
        if not result.candidates:
            raise InvalidReviewError(
                "cannot accept: record has no candidate to accept"
            )
        return result.candidates[0].catalog_id

    if request.action is ReviewAction.override:
        if not request.catalog_id:
            raise InvalidReviewError("override requires a catalog_id")
        candidate_ids = {c.catalog_id for c in result.candidates}
        if request.catalog_id not in candidate_ids:
            raise InvalidReviewError(
                f"override catalog_id={request.catalog_id!r} is not one of the"
                " record's candidates"
            )
        return request.catalog_id

    # reject: no acceptable candidate.
    return None


def _persist_review(conn: sqlite3.Connection, result: MatchResult) -> None:
    """Write the reviewed ``MatchResult`` back to the matches table.

    Only ``payload`` changes; ``tier`` and ``matched_at`` stay as the engine
    recorded them, so tier-filtered queries keep reflecting the match, not the
    review.
    """
    try:
        conn.execute(
            "UPDATE matches SET payload = ? WHERE record_id = ?",
            (result.model_dump_json(), result.record_id),
        )
        conn.commit()
    except sqlite3.Error as exc:
        log_event(
            logger,
            logging.ERROR,
            "dependency_failure",
            dependency="sqlite",
            operation="persist_review",
            record_id=result.record_id,
            error=str(exc),
        )
        raise DependencyError(
            f"could not persist review for {result.record_id}"
        ) from exc
