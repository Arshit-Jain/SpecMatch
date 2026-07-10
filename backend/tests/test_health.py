def test_health_shape(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["records"] == 150
    assert body["matched"] == 150
    tiers = body["tiers"]
    assert tiers["green"] + tiers["yellow"] + tiers["red"] == 150
