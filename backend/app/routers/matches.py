"""Match endpoints (Task 4).

Thin request/response layer over ``services.matches``: query persisted
matches and record an auditable review decision. Logic and persistence live
in the service; the router only wires HTTP <-> service and maps the service's
domain errors to status codes.
"""

from fastapi import APIRouter, HTTPException, Query

from app.core.db import get_conn
from app.core.errors import InvalidReviewError, NotFoundError
from app.models.schemas import MatchesResponse, MatchResult, ReviewRequest, Tier
from app.services import matches as match_service

router = APIRouter()


@router.get("/matches", response_model=MatchesResponse)
def list_matches(
    tier: Tier | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> MatchesResponse:
    conn = get_conn()
    try:
        total, items = match_service.list_matches(
            conn, tier=tier, limit=limit, offset=offset
        )
    finally:
        conn.close()
    return MatchesResponse(total=total, items=items)


@router.post("/matches/{record_id}/review", response_model=MatchResult)
def review_match(record_id: str, body: ReviewRequest) -> MatchResult:
    conn = get_conn()
    try:
        return match_service.apply_review(conn, record_id, body)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidReviewError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        conn.close()
