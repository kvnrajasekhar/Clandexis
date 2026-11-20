"""
Centralized Configuration & Secrets Management for Clandexis Invisible ERP.
Loads and validates all environment variables from .env file.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Locate and load the root .env file automatically
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH, override=False)
else:
    load_dotenv(override=False)


def _meta_env(primary: str, *aliases: str):
    # An explicitly empty primary setting disables the integration in local demos.
    return next((os.environ[k] for k in (primary, *aliases) if k in os.environ), None)


def _meta_setting(primary: str, *aliases: str, default=None):
    """Honor an explicit empty primary value; otherwise use aliases then Secret Manager."""
    value = _meta_env(primary, *aliases)
    return value if value is not None else get_secret(primary, default)


def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
    """Fetch setting from env, or fallback to Google Cloud Secret Manager when deployed."""
    val = os.getenv(key)
    if val:
        return val
    alt = key.lower().replace("_", "-")
    val = os.getenv(alt)
    if val:
        return val
    project = os.getenv("GCP_PROJECT_ID", "clandexis")
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project}/secrets/{alt}/versions/latest"
        res = client.access_secret_version(request={"name": name})
        secret_str = res.payload.data.decode("utf-8").strip()
        if secret_str:
            os.environ[key] = secret_str
            return secret_str
    except Exception:
        pass
    return default


class Settings(BaseModel):
    # ==========================================================================
    # Google Cloud Platform (GCP)
    # ==========================================================================
    DATABASE_BACKEND: str = Field(default_factory=lambda: get_secret("DATABASE_BACKEND", "postgres"))
    DATABASE_URL: Optional[str] = Field(default_factory=lambda: get_secret("DATABASE_URL"))
    GCP_PROJECT_ID: str = Field(default_factory=lambda: os.getenv("GCP_PROJECT_ID", "clandexis"))
    GCP_REGION: str = Field(default_factory=lambda: os.getenv("GCP_REGION", "asia-south1"))
    FIRESTORE_DATABASE: str = Field(default_factory=lambda: os.getenv("FIRESTORE_DATABASE", "(default)"))
    BIGQUERY_DATASET: str = Field(default_factory=lambda: os.getenv("BIGQUERY_DATASET", "clandexis_analytics"))
    GOOGLE_APPLICATION_CREDENTIALS: Optional[str] = Field(
        default_factory=lambda: os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    )

    # ==========================================================================
    # Google AI & Gemini Models
    # ==========================================================================
    GEMINI_API_KEY: Optional[str] = Field(default_factory=lambda: get_secret("GEMINI_API_KEY"))
    ROUTER_MODEL: str = Field(default_factory=lambda: os.getenv("ROUTER_MODEL", "gemini-3.5-flash-lite"))
    EXTRACTOR_MODEL: str = Field(default_factory=lambda: os.getenv("EXTRACTOR_MODEL", "gemini-3.8-flash"))
    TTS_MODEL: str = Field(default_factory=lambda: os.getenv("TTS_MODEL", "gemini-3.8-flash-tts"))
    ANALYST_MODEL: str = Field(default_factory=lambda: os.getenv("ANALYST_MODEL", "gemini-4-carbon"))

    # ==========================================================================
    # Meta WhatsApp Business Cloud API (Graph API v21.0)
    # ==========================================================================
    META_WHATSAPP_TOKEN: Optional[str] = Field(
        default_factory=lambda: _meta_setting("META_WHATSAPP_TOKEN", "WHATSAPP_ACCESS_TOKEN")
    )
    META_PHONE_NUMBER_ID: Optional[str] = Field(
        default_factory=lambda: _meta_setting("META_PHONE_NUMBER_ID", "WHATSAPP_PHONE_NUMBER_ID")
    )
    WHATSAPP_DEMO_PHONE_MAP: str = Field(default_factory=lambda: os.getenv("WHATSAPP_DEMO_PHONE_MAP", "{}"))
    META_WABA_ID: Optional[str] = Field(default_factory=lambda: get_secret("META_WABA_ID"))
    META_VERIFY_TOKEN: str = Field(
        default_factory=lambda: _meta_setting("META_VERIFY_TOKEN", "WHATSAPP_VERIFY_TOKEN", default="")
    )
    META_APP_SECRET: Optional[str] = Field(
        default_factory=lambda: _meta_setting(
            "META_APP_SECRET", "WHATSAPP_APP_SECRET", "META-WHATSAPP_APP_SECRET"
        )
    )
    META_API_VERSION: str = Field(default_factory=lambda: os.getenv("META_API_VERSION", "v21.0"))
    META_GRAPH_BASE_URL: str = Field(default_factory=lambda: os.getenv("META_GRAPH_BASE_URL", "https://graph.facebook.com"))

    # ==========================================================================
    # Twilio WhatsApp Sandbox (Backup Channel Adapter)
    # ==========================================================================
    TWILIO_ACCOUNT_SID: Optional[str] = Field(default_factory=lambda: os.getenv("TWILIO_ACCOUNT_SID"))
    TWILIO_AUTH_TOKEN: Optional[str] = Field(default_factory=lambda: os.getenv("TWILIO_AUTH_TOKEN"))
    TWILIO_WHATSAPP_NUMBER: Optional[str] = Field(
        default_factory=lambda: os.getenv("TWILIO_WHATSAPP_NUMBER", "whatsapp:+14155238886")
    )

    # ==========================================================================
    # Google Cloud KMS Attestation & Cloud Storage
    # ==========================================================================
    KMS_KEY_RING: str = Field(default_factory=lambda: os.getenv("KMS_KEY_RING", "clandexis-attestation-ring"))
    KMS_KEY_NAME: str = Field(default_factory=lambda: os.getenv("KMS_KEY_NAME", "ledger-signing-key"))
    KMS_LOCATION: str = Field(default_factory=lambda: os.getenv("KMS_LOCATION", "asia-south1"))
    GCS_BUCKET_NAME: str = Field(default_factory=lambda: os.getenv("GCS_BUCKET_NAME", "clandexis-pdf-artifacts"))

    # ==========================================================================
    # Service Runtime & Safety Gate Parameters
    # ==========================================================================
    ENVIRONMENT: str = Field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    PORT: int = Field(default_factory=lambda: int(os.getenv("PORT", "8080")))
    HOST: str = Field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    LOG_LEVEL: str = Field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    CONFIDENCE_THRESHOLD: float = Field(
        default_factory=lambda: float(os.getenv("CONFIDENCE_THRESHOLD", "0.85"))
    )
    HIGH_CREDIT_THRESHOLD: float = Field(
        default_factory=lambda: float(os.getenv("HIGH_CREDIT_THRESHOLD", "5000.00"))
    )

    TRUSTED_ORIGINS: list[str] = Field(default_factory=lambda: [
        value.strip() for value in os.getenv("TRUSTED_ORIGINS", "").split(",")
        if value.strip() and value.strip() != "*"
    ])
    GOOGLE_CLIENT_ID: Optional[str] = Field(default_factory=lambda: get_secret("GOOGLE_CLIENT_ID"))
    DASHBOARD_SESSION_SECRET: str = Field(default_factory=lambda: get_secret("DASHBOARD_SESSION_SECRET", ""))
    DASHBOARD_DEMO_USERNAME: str = Field(default_factory=lambda: os.getenv("DASHBOARD_DEMO_USERNAME", "demo"))
    DASHBOARD_DEMO_PASSWORD: str = Field(default_factory=lambda: os.getenv("DASHBOARD_DEMO_PASSWORD", ""))
    DASHBOARD_DEMO_MERCHANT_ID: str = Field(default_factory=lambda: os.getenv("DASHBOARD_DEMO_MERCHANT_ID", ""))
    INTERNAL_SEND_TOKEN: str = Field(default_factory=lambda: get_secret("INTERNAL_SEND_TOKEN", ""))
    INVOICE_LOCAL_ROOT: Optional[str] = Field(default_factory=lambda: os.getenv("INVOICE_LOCAL_ROOT"))

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() in ("production", "prod")

    @property
    def meta_messages_endpoint(self) -> str:
        return f"{self.META_GRAPH_BASE_URL}/{self.META_API_VERSION}/{self.META_PHONE_NUMBER_ID}/messages"

    def meta_media_endpoint(self, media_id: str) -> str:
        return f"{self.META_GRAPH_BASE_URL}/{self.META_API_VERSION}/{media_id}"


@lru_cache()
def get_settings() -> Settings:
    """Returns singleton instance of validated settings."""
    return Settings()


# Default instance for convenient imports
settings = get_settings()
