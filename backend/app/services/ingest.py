"""Ingest fixture CSVs into the database.

Runs at application startup, and can be run manually:

    python -m app.services.ingest
"""

import csv
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.core.db import get_conn, init_schema
from app.core.errors import DependencyError
from app.core.logging import configure_logging, log_event

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_CSV = REPO_ROOT / "data" / "source_records.csv"
CATALOG_CSV = REPO_ROOT / "data" / "catalog.csv"


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError as exc:
        log_event(
            logger,
            logging.ERROR,
            "dependency_failure",
            dependency="filesystem",
            path=str(path),
            error=str(exc),
        )
        raise DependencyError(f"could not read fixture file: {path}") from exc


def ingest_catalog(conn: sqlite3.Connection) -> int:
    rows = _read_csv(CATALOG_CSV)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO catalog (catalog_id, description, category, unit)"
            " VALUES (:catalog_id, :description, :category, :unit)",
            rows,
        )
        conn.commit()
    except sqlite3.Error as exc:
        log_event(
            logger,
            logging.ERROR,
            "dependency_failure",
            dependency="sqlite",
            operation="ingest_catalog",
            error=str(exc),
        )
        raise DependencyError("could not ingest catalog into database") from exc
    return len(rows)


def ingest_records(conn: sqlite3.Connection) -> int:
    rows = _read_csv(SOURCE_CSV)
    now = datetime.now(timezone.utc).isoformat()
    try:
        conn.executemany(
            "INSERT INTO records (record_id, raw_text, category, unit, quantity, ingested_at)"
            " SELECT :record_id, :raw_text, :category, :unit, :quantity, :ingested_at"
            " WHERE NOT EXISTS (SELECT 1 FROM records WHERE record_id = :record_id)",
            [{**row, "ingested_at": now} for row in rows],
        )
        conn.commit()
    except sqlite3.Error as exc:
        log_event(
            logger,
            logging.ERROR,
            "dependency_failure",
            dependency="sqlite",
            operation="ingest_records",
            error=str(exc),
        )
        raise DependencyError("could not ingest records into database") from exc
    return len(rows)


def run_ingest(conn: sqlite3.Connection | None = None) -> None:
    owned = conn is None
    if conn is None:
        conn = get_conn()
    try:
        try:
            init_schema(conn)
        except sqlite3.Error as exc:
            log_event(
                logger,
                logging.ERROR,
                "dependency_failure",
                dependency="sqlite",
                operation="init_schema",
                error=str(exc),
            )
            raise DependencyError("could not initialize database schema") from exc
        n_catalog = ingest_catalog(conn)
        n_records = ingest_records(conn)
        log_event(
            logger,
            logging.INFO,
            "ingest_completed",
            catalog_rows=n_catalog,
            record_rows=n_records,
        )
    finally:
        if owned:
            conn.close()


if __name__ == "__main__":
    configure_logging()
    run_ingest()
