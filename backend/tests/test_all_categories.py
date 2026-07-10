def test_all_categories_filter_shows_all_records(client):
    """
    Issue #3: Selecting "All categories" passes ?category=All,
    which should show all records instead of "No records."
    """
    resp = client.get("/", params={"category": "All"})
    assert resp.status_code == 200
    assert "No records." not in resp.text
    assert "SRC-" in resp.text  # At least one record should be shown
