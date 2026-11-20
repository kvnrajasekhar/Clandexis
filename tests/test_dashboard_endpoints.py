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
    assert res.status_code == 401


def test_dashboard_static_mount():
    res = client.get("/dashboard/")
    assert res.status_code == 200
    assert "<title>Clandexis Invisible ERP — Judge Live Dashboard</title>" in res.text
