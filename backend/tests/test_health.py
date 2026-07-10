def test_health_shape(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    # Exact HealthResponse shape — no added, renamed, or dropped fields.
    assert set(body) == {"status", "records", "matched", "tiers"}
    assert body["status"] == "ok"
    assert body["records"] == 150
    assert body["matched"] == 150
    tiers = body["tiers"]
    assert set(tiers) == {"green", "yellow", "red"}
    assert tiers["green"] + tiers["yellow"] + tiers["red"] == 150
