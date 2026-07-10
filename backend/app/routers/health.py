import logging
import sqlite3

from fastapi import APIRouter

from app.core.db import get_conn
from app.core.errors import DependencyError
from app.core.logging import log_event
from app.models.schemas import HealthResponse, TierCounts

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    conn = get_conn()
    try:
        try:
            records = conn.execute("SELECT COUNT(*) AS n FROM records").fetchone()["n"]
            matched = conn.execute("SELECT COUNT(*) AS n FROM matches").fetchone()["n"]
            tier_rows = conn.execute(
                "SELECT tier, COUNT(*) AS n FROM matches GROUP BY tier"
            ).fetchall()
        except sqlite3.Error as exc:
            log_event(
                logger,
                logging.ERROR,
                "dependency_failure",
                dependency="sqlite",
                operation="health_check",
                error=str(exc),
            )
            raise DependencyError("could not query database for health check") from exc
    finally:
        conn.close()
    tiers = TierCounts(**{row["tier"]: row["n"] for row in tier_rows})
    return HealthResponse(status="ok", records=records, matched=matched, tiers=tiers)
