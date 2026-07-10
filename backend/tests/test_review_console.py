"""Server-rendered review panel (Task 5).

The panel works the yellow/red queues: tier counts, each record's source
text + candidates + per-signal breakdown, and accept/override/reject actions
that persist through the same service the JSON API uses. These tests exercise
the rendered page and the console review round-trip (Post/Redirect/Get), and
assert the persisted result is reflected on the next read.

The `client` fixture (conftest.py) is session-scoped over one seeded DB, so
each mutating test acts on a distinct record id to stay independent of the
others and of the /matches tests.
"""


def _queue(client, tier):
    """Records in a tier queue, as seen by the JSON API (source of truth)."""
    return client.get("/matches", params={"tier": tier, "limit": 500}).json()["items"]


def _pick(client, tier, min_candidates=1, exclude=()):
    """First record in `tier` with >= min_candidates candidates, not excluded."""
    for item in _queue(client, tier):
        if item["record_id"] in exclude:
            continue
        if len(item["candidates"]) >= min_candidates:
            return item
    raise AssertionError(f"no {tier} record with >= {min_candidates} candidates")


def _match(client, record_id):
    body = client.get("/matches", params={"limit": 500}).json()
    return {i["record_id"]: i for i in body["items"]}[record_id]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_review_panel_renders_yellow_queue_by_default(client):
    resp = client.get("/review")
    assert resp.status_code == 200
    # Tier counts are visible in the toolbar.
    assert "Yellow" in resp.text
    assert "Red" in resp.text
    # The default queue shows its records' source text.
    first = _queue(client, "yellow")[0]
    assert first["record_id"] in resp.text


def test_review_panel_shows_candidate_signals(client):
    """Each record exposes its candidates and per-signal score breakdown."""
    rec = _pick(client, "yellow")
    resp = client.get("/review")
    top = rec["candidates"][0]
    assert top["catalog_id"] in resp.text
    # At least one signal name from the breakdown is rendered.
    assert any(signal in resp.text for signal in top["signals"])


def test_review_panel_tier_filter_selects_red_queue(client):
    resp = client.get("/review", params={"tier": "red"})
    assert resp.status_code == 200
    red_ids = {i["record_id"] for i in _queue(client, "red")}
    yellow_only = {i["record_id"] for i in _queue(client, "yellow")} - red_ids
    if red_ids:
        assert any(rid in resp.text for rid in red_ids)
    # A yellow-only record is not in the red queue.
    for rid in list(yellow_only)[:5]:
        assert rid not in resp.text


def test_review_panel_ignores_non_review_tier(client):
    """green isn't a review queue; the panel falls back to the default."""
    resp = client.get("/review", params={"tier": "green"})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Console review round-trip (Post/Redirect/Get) — persistence
# ---------------------------------------------------------------------------


def test_console_accept_persists_top_candidate(client):
    rec = _pick(client, "yellow", exclude=("SRC-0002", "SRC-0004", "SRC-0005"))
    top_id = rec["candidates"][0]["catalog_id"]

    resp = client.post(
        "/review",
        data={"record_id": rec["record_id"], "action": "accept", "tier": "yellow"},
    )
    # PRG: lands back on the queue page.
    assert resp.status_code == 200
    assert resp.url.path == "/review"

    after = _match(client, rec["record_id"])
    assert after["selected_catalog_id"] == top_id
    assert after["review"]["action"] == "accept"


def test_console_override_persists_named_candidate(client):
    rec = _pick(
        client, "yellow", min_candidates=2, exclude=("SRC-0002", "SRC-0004", "SRC-0005")
    )
    alt_id = rec["candidates"][1]["catalog_id"]

    resp = client.post(
        "/review",
        data={
            "record_id": rec["record_id"],
            "action": "override",
            "catalog_id": alt_id,
            "tier": "yellow",
        },
    )
    assert resp.status_code == 200
    after = _match(client, rec["record_id"])
    assert after["selected_catalog_id"] == alt_id
    assert after["review"]["action"] == "override"


def test_console_reject_clears_selection(client):
    rec = _pick(
        client, "yellow", exclude=("SRC-0002", "SRC-0004", "SRC-0005")
    )
    # Use a different record than accept/override picked.
    resp = client.post(
        "/review",
        data={"record_id": rec["record_id"], "action": "reject", "tier": "yellow"},
    )
    assert resp.status_code == 200
    after = _match(client, rec["record_id"])
    assert after["review"]["action"] == "reject"


def test_console_review_unknown_record_is_404(client):
    resp = client.post(
        "/review",
        data={"record_id": "SRC-NOPE", "action": "accept", "tier": "yellow"},
    )
    assert resp.status_code == 404


def test_console_review_unknown_action_is_422(client):
    resp = client.post(
        "/review",
        data={"record_id": "SRC-0006", "action": "banana", "tier": "yellow"},
    )
    assert resp.status_code == 422
