"""HTTP auth, cookie, CSRF, CORS and response redaction tests without cloud calls."""
import logging
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core.security import install_security, router, NoQueryAccessLog
from app.api.v1.dashboard_endpoints import router as dashboard
from config import settings
from tests.auth_helpers import configure_demo, sign_in, PASSWORD


@pytest.fixture
def secure_client(monkeypatch):
    configure_demo(monkeypatch, "+919876543210")
    app = FastAPI()
    install_security(app)
    app.include_router(router, prefix="/api/v1")
    app.include_router(dashboard, prefix="/api/v1")
    @app.get("/error")
    def error():
        raise RuntimeError("credential-secret-do-not-expose")
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("path", ["summary", "inventory", "parties", "transactions", "candidates"])
def test_all_dashboard_reads_require_verified_session(secure_client, path):
    assert secure_client.get("/api/v1/dashboard/" + path, params={"merchant_id": "+919876543210"}).status_code == 401


@pytest.mark.parametrize("action", ["confirm", "cancel"])
def test_dashboard_mutations_require_session_and_csrf(secure_client, action):
    url = "/api/v1/dashboard/candidates/" + action
    payload = dict(merchant_id="+919876543210", confirmed_by="+919876543210", candidate_id="cand_test")
    assert secure_client.post(url, json=payload).status_code == 401
    login = sign_in(secure_client)
    assert secure_client.post(url, json=payload, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert secure_client.post(url, json=payload, headers={"Origin": "https://evil.invalid"}).status_code == 403
    assert secure_client.post(url, json=payload | {"merchant_id": "other"}).status_code == 403
    assert secure_client.post(url, json=payload | {"confirmed_by": "other"}).status_code == 403


def test_tampered_cookie_and_restricted_merchant(secure_client):
    sign_in(secure_client)
    assert secure_client.get("/api/v1/auth/session").status_code == 200
    assert secure_client.get("/api/v1/dashboard/summary?merchant_id=other").status_code == 403
    secure_client.cookies.clear()
    secure_client.cookies.set("clandexis_session", "forged-merchant-session")
    assert secure_client.get("/api/v1/auth/session").status_code == 401


def test_cookie_flags_logout_and_disabled_credentials(secure_client, monkeypatch):
    response = sign_in(secure_client)
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "max-age=3600" in cookie
    assert secure_client.post("/api/v1/auth/logout").status_code == 200
    assert secure_client.get("/api/v1/auth/session").status_code == 401
    monkeypatch.setattr(settings, "DASHBOARD_DEMO_PASSWORD", "")
    assert secure_client.post("/api/v1/auth/login", json={"username": "demo", "password": PASSWORD}).status_code == 401


def test_production_session_is_secure(monkeypatch):
    configure_demo(monkeypatch, "+919876543210")
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    app = FastAPI()
    install_security(app)
    app.include_router(router, prefix="/api/v1")
    client = TestClient(app, base_url="https://testserver")
    assert "secure" in sign_in(client).headers["set-cookie"].lower()


def test_cors_exact_allowlist(secure_client):
    headers = {"Origin": "http://testserver", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "X-CSRF-Token"}
    response = secure_client.options("/api/v1/auth/login", headers=headers)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://testserver"
    response = secure_client.options("/api/v1/auth/login", headers=headers | {"Origin": "https://evil.invalid"})
    assert response.status_code == 400 and "access-control-allow-origin" not in response.headers


def test_errors_do_not_echo_passwords_or_internal_exceptions(secure_client):
    response = secure_client.post("/api/v1/auth/login", json={"username": "demo", "password": {"value": PASSWORD}})
    assert response.status_code == 422 and PASSWORD not in response.text
    response = secure_client.get("/error")
    assert response.status_code == 500 and "credential-secret" not in response.text


def test_access_log_query_redaction():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 1, '%s - "%s %s HTTP/%s" %d',
        ("client", "GET", "/webhook?hub.verify_token=never-log-me", "1.1", 403), None)
    assert NoQueryAccessLog().filter(record)
    assert "never-log-me" not in record.getMessage()
