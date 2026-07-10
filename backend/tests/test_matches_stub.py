"""Smoke check that the Task 4 endpoints are no longer stubbed.

This file started life asserting the /matches endpoints returned 501. Task 4
implements them, so those expectations are replaced here with the inverse
check — they must now respond per contract, never 501. Deeper contract and
behaviour coverage lives in test_matches.py.
"""


def test_matches_endpoints_are_implemented(client):
    """The stubs are gone: both endpoints respond, and never with 501."""
    listing = client.get("/matches")
    assert listing.status_code == 200
    assert listing.json()["total"] >= 0

    # A real record id from the seeded fixture — accept should succeed.
    review = client.post("/matches/SRC-0001/review", json={"action": "accept"})
    assert review.status_code == 200
    assert review.status_code != 501
