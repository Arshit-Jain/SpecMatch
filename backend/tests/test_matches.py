"""Contract and behaviour tests for the Task 4 match endpoints.

Covered:
  * GET  /matches               — response shape, tier filter, pagination.
  * POST /matches/{id}/review   — accept / override / reject semantics,
                                  persistence (auditability), and validation.

The `client` fixture (conftest.py) is session-scoped over one seeded DB, so
the review tests below deliberately act on distinct record ids to stay
independent of one another and of the smoke test in test_matches_stub.py
(which reviews SRC-0001).
"""

MATCH_RESULT_FIELDS = {
    "record_id",
    "source_text",
    "tier",
    "candidates",
    "selected_catalog_id",
    "review",
    "matched_at",
}
CANDIDATE_FIELDS = {"catalog_id", "description", "score", "signals"}
TIERS = ("green", "yellow", "red")


def _all_matches(client):
    """Every persisted match in one page (fixture is 150 < max limit 500)."""
    body = client.get("/matches", params={"limit": 500}).json()
    return {item["record_id"]: item for item in body["items"]}


# ---------------------------------------------------------------------------
# GET /matches — shape
# ---------------------------------------------------------------------------


def test_matches_default_shape(client):
    resp = client.get("/matches")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"total", "items"}
    assert body["total"] == 150  # every seeded record is matched at startup
    assert len(body["items"]) == 50  # default page size


def test_matches_item_shape(client):
    item = client.get("/matches").json()["items"][0]
    assert set(item) == MATCH_RESULT_FIELDS
    assert item["tier"] in TIERS
    assert item["candidates"], "a matched record should carry candidates"
    for candidate in item["candidates"]:
        assert set(candidate) == CANDIDATE_FIELDS
        assert 0.0 <= candidate["score"] <= 1.0
    # Scores are persisted best-first.
    scores = [c["score"] for c in item["candidates"]]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# GET /matches — tier filter
# ---------------------------------------------------------------------------


def test_matches_tier_filter_is_honoured(client):
    for tier in TIERS:
        body = client.get("/matches", params={"tier": tier, "limit": 500}).json()
        assert body["total"] == len(body["items"])
        assert all(item["tier"] == tier for item in body["items"])


def test_matches_tier_totals_sum_to_all(client):
    per_tier = {
        tier: client.get("/matches", params={"tier": tier, "limit": 500}).json()["total"]
        for tier in TIERS
    }
    assert sum(per_tier.values()) == 150
    # Cross-check against the health endpoint's denormalised counts.
    health_tiers = client.get("/health").json()["tiers"]
    assert per_tier == {t: health_tiers[t] for t in TIERS}


def test_matches_rejects_unknown_tier(client):
    assert client.get("/matches", params={"tier": "purple"}).status_code == 422


# ---------------------------------------------------------------------------
# GET /matches — pagination
# ---------------------------------------------------------------------------


def test_matches_pagination_is_disjoint(client):
    page1 = client.get("/matches", params={"limit": 10, "offset": 0}).json()
    page2 = client.get("/matches", params={"limit": 10, "offset": 10}).json()
    assert page1["total"] == page2["total"] == 150
    ids1 = {i["record_id"] for i in page1["items"]}
    ids2 = {i["record_id"] for i in page2["items"]}
    assert len(ids1) == 10
    assert ids1.isdisjoint(ids2)


def test_matches_offset_past_end_is_empty(client):
    body = client.get("/matches", params={"offset": 1000}).json()
    assert body["total"] == 150
    assert body["items"] == []


# ---------------------------------------------------------------------------
# POST /matches/{id}/review — decision semantics + persistence
# ---------------------------------------------------------------------------


def test_review_accept_selects_top_candidate_and_persists(client):
    record_id = "SRC-0002"
    before = _all_matches(client)[record_id]
    top_id = before["candidates"][0]["catalog_id"]

    resp = client.post(f"/matches/{record_id}/review", json={"action": "accept"})
    assert resp.status_code == 200
    result = resp.json()
    assert result["selected_catalog_id"] == top_id
    assert result["review"]["action"] == "accept"
    assert result["review"]["catalog_id"] == top_id
    assert result["review"]["reviewed_at"]  # audit timestamp recorded

    # Persisted and auditable: a fresh read reflects the decision.
    after = _all_matches(client)[record_id]
    assert after["selected_catalog_id"] == top_id
    assert after["review"]["action"] == "accept"


def test_review_override_selects_named_candidate(client):
    record_id = "SRC-0004"
    before = _all_matches(client)[record_id]
    assert len(before["candidates"]) >= 2, "need an alternative to override to"
    alt_id = before["candidates"][1]["catalog_id"]

    resp = client.post(
        f"/matches/{record_id}/review",
        json={"action": "override", "catalog_id": alt_id, "note": "spec says B"},
    )
    assert resp.status_code == 200
    result = resp.json()
    assert result["selected_catalog_id"] == alt_id
    assert result["review"]["action"] == "override"
    assert result["review"]["catalog_id"] == alt_id
    assert result["review"]["note"] == "spec says B"

    assert _all_matches(client)[record_id]["selected_catalog_id"] == alt_id


def test_review_reject_clears_selection(client):
    record_id = "SRC-0005"
    resp = client.post(
        f"/matches/{record_id}/review",
        json={"action": "reject", "note": "no acceptable match"},
    )
    assert resp.status_code == 200
    result = resp.json()
    assert result["selected_catalog_id"] is None
    assert result["review"]["action"] == "reject"
    assert result["review"]["catalog_id"] is None

    assert _all_matches(client)[record_id]["review"]["action"] == "reject"


# ---------------------------------------------------------------------------
# POST /matches/{id}/review — validation
# ---------------------------------------------------------------------------


def test_review_override_without_catalog_id_is_400(client):
    resp = client.post("/matches/SRC-0006/review", json={"action": "override"})
    assert resp.status_code == 400


def test_review_override_with_non_candidate_is_400(client):
    resp = client.post(
        "/matches/SRC-0006/review",
        json={"action": "override", "catalog_id": "CAT-DOES-NOT-EXIST"},
    )
    assert resp.status_code == 400


def test_review_unknown_record_is_404(client):
    resp = client.post("/matches/SRC-NOPE/review", json={"action": "accept"})
    assert resp.status_code == 404


def test_review_unknown_action_is_422(client):
    resp = client.post("/matches/SRC-0006/review", json={"action": "banana"})
    assert resp.status_code == 422
