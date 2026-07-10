import logging
import sqlite3

from fastapi import APIRouter, Query

from app.core.db import get_conn
from app.core.errors import DependencyError
from app.core.logging import log_event
from app.models.schemas import RecordOut, RecordsResponse

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/records", response_model=RecordsResponse)
def list_records(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RecordsResponse:
    conn = get_conn()
    try:
        try:
            total = conn.execute("SELECT COUNT(*) AS n FROM records").fetchone()["n"]
            rows = conn.execute(
                "SELECT record_id, raw_text, category, unit, quantity, ingested_at"
                " FROM records ORDER BY id LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        except sqlite3.Error as exc:
            log_event(
                logger,
                logging.ERROR,
                "dependency_failure",
                dependency="sqlite",
                operation="list_records",
                error=str(exc),
            )
            raise DependencyError("could not query records from database") from exc
    finally:
        conn.close()
    items = [
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
    return RecordsResponse(total=total, items=items)
