"""Synthetic credentials used only in tests."""
from config import settings

PASSWORD = "test-password-not-for-production"
SESSION_SECRET = "test-session-key-" * 4
SEND_TOKEN = "test-internal-send-token-" * 3
META_SECRET = "test-meta-hmac-secret"


def configure_demo(monkeypatch, merchant_id):
    for name, value in dict(DASHBOARD_SESSION_SECRET=SESSION_SECRET,
        DASHBOARD_DEMO_USERNAME="demo", DASHBOARD_DEMO_PASSWORD=PASSWORD,
        DASHBOARD_DEMO_MERCHANT_ID=merchant_id, INTERNAL_SEND_TOKEN=SEND_TOKEN,
        META_APP_SECRET=META_SECRET, TRUSTED_ORIGINS=["http://testserver"],
        ENVIRONMENT="development").items():
        monkeypatch.setattr(settings, name, value)


def sign_in(client):
    response = client.post("/api/v1/auth/login", json={"username": "demo", "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response
