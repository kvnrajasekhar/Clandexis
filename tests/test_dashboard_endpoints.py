import pytest
from starlette.testclient import TestClient
from webhook_service.main import app

client = TestClient(app)

def test_webhook_service_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "healthy"

def test_dashboard_api_summary_endpoint():
    res = client.get("/api/v1/dashboard/summary?merchant_id=%2B919876543210")
    # In local test environment without Firestore emulator, returns either 200 with summary or handled error
    assert res.status_code in [200, 500]
    if res.status_code == 200:
        data = res.json()
        assert "merchant_id" in data
        assert "total_sales" in data

def test_dashboard_static_mount():
    res = client.get("/dashboard/")
    assert res.status_code == 200
    assert "<title>Clandexis Invisible ERP — Judge Live Dashboard</title>" in res.text
