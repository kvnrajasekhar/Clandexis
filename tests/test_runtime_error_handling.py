import logging
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.observability import unavailable


def test_operational_error_has_correlated_safe_response(caplog):
    logger = logging.getLogger("tests.runtime-errors")
    secret = "database-password-must-not-leak"

    try:
        raise RuntimeError(secret)
    except RuntimeError as exc:
        error = unavailable(logger, "dashboard.candidate_staging", exc)

    assert error.status_code == 503
    error_id = error.headers["X-Clandexis-Error-ID"]
    assert error_id in error.detail
    assert secret not in error.detail
    assert error_id in caplog.text
    assert "dashboard.candidate_staging" in caplog.text
    assert "RuntimeError" in caplog.text
    assert secret not in caplog.text


def test_dashboard_distinguishes_extraction_failure(monkeypatch):
    import asyncio
    import app.api.v1.endpoints as endpoints

    monkeypatch.setattr(
        endpoints,
        "pipeline",
        SimpleNamespace(
            process_and_interpret=lambda **kwargs: (_ for _ in ()).throw(
                RuntimeError("provider-secret")
            )
        ),
    )
    payload = endpoints.TextExtractionRequest(
        merchant_id="00000000-0000-0000-0000-000000000001",
        raw_text="sale",
    )
    identity = SimpleNamespace(merchant_id=payload.merchant_id)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(endpoints.extract_from_text(payload, identity))

    assert caught.value.status_code == 503
    assert "provider-secret" not in caught.value.detail
    assert caught.value.headers["X-Clandexis-Error-ID"] in caught.value.detail


def test_dashboard_distinguishes_candidate_staging_failure(monkeypatch, caplog):
    import asyncio
    import app.api.v1.endpoints as endpoints

    monkeypatch.setattr(
        endpoints,
        "pipeline",
        SimpleNamespace(process_and_interpret=lambda **kwargs: object()),
    )
    monkeypatch.setattr(
        endpoints,
        "dashboard_result",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db-secret")),
    )
    payload = endpoints.TextExtractionRequest(
        merchant_id="00000000-0000-0000-0000-000000000001",
        raw_text="sale",
        stage=True,
    )
    identity = SimpleNamespace(merchant_id=payload.merchant_id)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(endpoints.extract_from_text(payload, identity))

    assert caught.value.status_code == 503
    assert "dashboard.candidate_staging" in caplog.text
    assert "db-secret" not in caplog.text


def test_onboarding_database_failure_is_not_reported_as_success(monkeypatch):
    import app.core.security as security

    monkeypatch.setattr(security.settings, "DATABASE_BACKEND", "postgres")
    monkeypatch.setattr(security.settings, "DATABASE_URL", "postgresql://configured")
    monkeypatch.setattr(
        security.psycopg,
        "connect",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("db-secret")),
    )
    session = {
        "user_email": "owner@example.com",
        "user_name": "Owner",
        "csrf_token": "csrf",
        "needs_onboarding": True,
    }
    request = SimpleNamespace(session=session, headers={"x-csrf-token": "csrf"})
    payload = security.OnboardRequest(
        name="Owner", phone_number="+918008731789", business_name="Asthra Labs"
    )

    with pytest.raises(HTTPException) as caught:
        security.onboard(payload, request)

    assert caught.value.status_code == 503
    assert "db-secret" not in caught.value.detail
    assert session["needs_onboarding"] is True
    assert "merchant_id" not in session


def test_onboarding_missing_postgres_url_is_not_reported_as_success(monkeypatch):
    import app.core.security as security

    monkeypatch.setattr(security.settings, "DATABASE_BACKEND", "postgres")
    monkeypatch.setattr(security.settings, "DATABASE_URL", None)
    session = {
        "user_email": "owner@example.com",
        "csrf_token": "csrf",
        "needs_onboarding": True,
    }
    request = SimpleNamespace(session=session, headers={"x-csrf-token": "csrf"})
    payload = security.OnboardRequest(
        name="Owner", phone_number="+918008731789", business_name="Asthra Labs"
    )

    with pytest.raises(HTTPException) as caught:
        security.onboard(payload, request)

    assert caught.value.status_code == 503
    assert session["needs_onboarding"] is True
    assert "merchant_id" not in session
