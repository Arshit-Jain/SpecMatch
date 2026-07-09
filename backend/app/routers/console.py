"""Server-rendered review console (Jinja2).

The record table is implemented. The review panel is stubbed — completing
it is Task 5.
"""

from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.core.db import get_conn

router = APIRouter()

templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")


@router.get("/", response_class=HTMLResponse)
def record_table(request: Request, category: str | None = Query(default=None)):
    if category == "All":
        category = None

    conn = get_conn()
    try:
        categories = [
            row["category"]
            for row in conn.execute(
                "SELECT DISTINCT category FROM records"
                " WHERE category IS NOT NULL AND category != '' ORDER BY category"
            ).fetchall()
        ]
        if category is not None:
            rows = conn.execute(
                "SELECT record_id, raw_text, category, unit, quantity FROM records"
                " WHERE category = ? ORDER BY id",
                (category,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT record_id, raw_text, category, unit, quantity FROM records"
                " ORDER BY id"
            ).fetchall()
    finally:
        conn.close()
    return templates.TemplateResponse(
        request,
        "records.html",
        {
            "records": rows,
            "categories": categories,
            "selected_category": category,
        },
    )


@router.get("/review", response_class=HTMLResponse)
def review_panel(request: Request):
    # TODO(Task 5): implement the review panel — yellow/red queues with
    # counts, candidate scores with per-signal breakdowns, and
    # accept/override/reject actions that persist through the API.
    return templates.TemplateResponse(request, "review.html", {})
