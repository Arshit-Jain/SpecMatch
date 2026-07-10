"""Server-rendered review console (Jinja2).

The record table and the review panel are both implemented here. The panel
works the yellow/red queues; its accept/override/reject actions persist
through ``services.matches`` — the same service layer the JSON API sits on —
so the console and the API never diverge on review semantics.
"""

from pathlib import Path
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.db import get_conn
from app.core.errors import InvalidReviewError, NotFoundError
from app.models.schemas import ReviewAction, ReviewRequest, Tier
from app.services import matches as match_service

router = APIRouter()

templates = Jinja2Templates(directory=Path(__file__).resolve().parents[1] / "templates")

# Only these tiers reach the review panel — green matches auto-accept and
# never need a human. The first is the default queue.
REVIEW_TIERS: tuple[Tier, ...] = (Tier.yellow, Tier.red)


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
def review_panel(request: Request, tier: str = Query(default=REVIEW_TIERS[0].value)):
    # `tier` selects which queue to work; an unknown or non-review value
    # (e.g. "green") falls back to the default queue — mirror the records
    # page's sentinel handling rather than 422 a browsable URL.
    review_values = {t.value for t in REVIEW_TIERS}
    selected = Tier(tier) if tier in review_values else REVIEW_TIERS[0]

    conn = get_conn()
    try:
        counts = {
            t.value: match_service.list_matches(conn, tier=t, limit=1)[0]
            for t in REVIEW_TIERS
        }
        _, items = match_service.list_matches(conn, tier=selected, limit=500)
    finally:
        conn.close()

    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "items": items,
            "counts": counts,
            "review_tiers": [t.value for t in REVIEW_TIERS],
            "selected_tier": selected.value,
        },
    )


@router.post("/review")
async def submit_review(request: Request):
    """Apply a review decision, then redirect back to the queue (Post/Redirect/Get).

    The console posts a plain ``application/x-www-form-urlencoded`` form; it is
    parsed with the stdlib (``urllib.parse``) to keep the console dependency-free
    rather than pull in a form-parsing package. The decision is delegated to
    ``match_service.apply_review`` — the exact path the JSON endpoint takes — so
    the persisted result the queue re-renders is the one the API would return.
    Service errors map to the same status codes the matches router uses; an
    unknown action is a 422, mirroring the JSON endpoint's pydantic validation.
    """
    form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    record_id = _first(form, "record_id")
    catalog_id = _first(form, "catalog_id") or None
    tier = _first(form, "tier") or REVIEW_TIERS[0].value
    try:
        action = ReviewAction(_first(form, "action"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="unknown review action") from exc

    conn = get_conn()
    try:
        match_service.apply_review(
            conn,
            record_id,
            ReviewRequest(action=action, catalog_id=catalog_id),
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidReviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()

    return RedirectResponse(url=f"/review?tier={tier}", status_code=303)


def _first(form: dict[str, list[str]], key: str) -> str:
    """First value for ``key`` in a parsed urlencoded form, or ``""``."""
    return (form.get(key) or [""])[0]
