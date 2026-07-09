import os
import tempfile
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.ingest import run_ingest

@pytest.fixture
def fresh_client():
    """Provides a fresh database for a single test."""
    with tempfile.TemporaryDirectory() as tmp:
        old_data_dir = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = tmp
        
        with TestClient(app) as c:
            yield c
            
        if old_data_dir is not None:
            os.environ["DATA_DIR"] = old_data_dir
        else:
            del os.environ["DATA_DIR"]


def test_re_ingest_leaves_records_unchanged(fresh_client):
    # The `fresh_client` fixture runs run_ingest() on startup, so there are 150 records.
    resp = fresh_client.get("/records")
    assert resp.status_code == 200
    assert resp.json()["total"] == 150
    
    # Re-run the ingest process (this reproduces Issue #1)
    run_ingest()
    
    # The total should still be 150, but before the fix it will be 300
    resp = fresh_client.get("/records")
    assert resp.json()["total"] == 150, f"Expected 150 records, but got {resp.json()['total']}"
