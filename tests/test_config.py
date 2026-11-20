"""
Unit tests for Clandexis Invisible ERP centralized configuration and .env loading.
"""

import os
import pytest
from config import Settings, get_settings


def test_settings_loaded_from_env():
    settings = get_settings()
    assert settings.GCP_PROJECT_ID == "clandexis"
    assert settings.GCP_REGION == "asia-south1"
    assert settings.ROUTER_MODEL == "gemini-3.5-flash-lite"
    assert settings.EXTRACTOR_MODEL == "gemini-3.8-flash"
    assert settings.TTS_MODEL == "gemini-3.8-flash-tts"
    assert settings.ANALYST_MODEL == "gemini-4-carbon"
    assert settings.META_API_VERSION == "v21.0"
    assert settings.META_VERIFY_TOKEN == os.environ.get("META_VERIFY_TOKEN", os.getenv("WHATSAPP_VERIFY_TOKEN", ""))
    assert isinstance(settings.PORT, int)
    assert isinstance(settings.CONFIDENCE_THRESHOLD, float)
    assert settings.CONFIDENCE_THRESHOLD == 0.85
    assert isinstance(settings.HIGH_CREDIT_THRESHOLD, float)
    assert settings.HIGH_CREDIT_THRESHOLD == 5000.00


def test_meta_endpoints():
    settings = get_settings()
    assert "/messages" in settings.meta_messages_endpoint
    assert f"/{settings.META_API_VERSION}/" in settings.meta_messages_endpoint
    
    media_id = "test_media_999"
    media_url = settings.meta_media_endpoint(media_id)
    assert media_url.endswith(f"/{settings.META_API_VERSION}/{media_id}")


def test_environment_override(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT_ID", "clandexis-prod-2026")
    monkeypatch.setenv("HIGH_CREDIT_THRESHOLD", "10000.00")
    monkeypatch.setenv("ENVIRONMENT", "production")

    custom_settings = Settings()
    assert custom_settings.GCP_PROJECT_ID == "clandexis-prod-2026"
    assert custom_settings.HIGH_CREDIT_THRESHOLD == 10000.00
    assert custom_settings.is_production is True


def test_whatsapp_aliases_and_explicit_disable(monkeypatch):
    pairs = {
        'META_VERIFY_TOKEN': 'WHATSAPP_VERIFY_TOKEN',
        'META_WHATSAPP_TOKEN': 'WHATSAPP_ACCESS_TOKEN',
        'META_PHONE_NUMBER_ID': 'WHATSAPP_PHONE_NUMBER_ID',
        'META_APP_SECRET': 'META-WHATSAPP_APP_SECRET',
    }
    for primary, alias in pairs.items():
        monkeypatch.delenv(primary, raising=False)
        monkeypatch.setenv(alias, 'test-value')
    assert all(getattr(Settings(), key) == 'test-value' for key in pairs)
    for primary in pairs:
        monkeypatch.setenv(primary, '')
    assert all(getattr(Settings(), key) == '' for key in pairs)
