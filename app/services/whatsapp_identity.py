"""Real phone resolution with database lookup, explicit allowlist, and in-memory TTL caching."""
import json
import logging
import re
import time
from typing import Dict, Optional, Tuple
from config import settings

logger = logging.getLogger(__name__)

# Thread-safe in-memory sliding cache: phone -> (merchant_id, expires_at_timestamp)
# 30-minute sliding window shields the merchant from remote DB connection timeouts and network glitches.
_PHONE_CACHE: Dict[str, Tuple[str, float]] = {}
CACHE_TTL_SECONDS = 1800  # 30 minutes


def normalize_phone(value: str) -> str:
    phone = re.sub(r"[\s().-]", "", value)
    if phone.startswith("00"):
        phone = "+" + phone[2:]
    if not phone.startswith("+"):
        phone = "+" + phone
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", phone):
        raise ValueError("Invalid international phone number")
    return phone


def resolve_phone(value: str) -> str | None:
    """Resolves an incoming phone to its authorized merchant_id.

    Uses an in-memory 30-minute sliding cache to guarantee sub-millisecond
    session persistence and resilience against Cloud SQL connection latency.
    Unknown or unapproved senders MUST return None without any ledger access.
    Never falls back to DASHBOARD_DEMO_MERCHANT_ID.
    """
    try:
        phone = normalize_phone(value)
    except (ValueError, TypeError, AttributeError):
        return None

    now = time.time()

    # 0. Check active in-memory session cache first (sub-millisecond, zero network overhead)
    cached = _PHONE_CACHE.get(phone)
    if cached and cached[1] > now:
        # Extend sliding lease on active user messages
        _PHONE_CACHE[phone] = (cached[0], now + CACHE_TTL_SECONDS)
        return cached[0]

    # 1. Backward-compatible demo phone map (used by isolated unit tests and explicit env mapping)
    demo_map_raw = settings.WHATSAPP_DEMO_PHONE_MAP
    if demo_map_raw and demo_map_raw.strip() not in ("", "{}"):
        try:
            raw = json.loads(demo_map_raw)
            mapping = {normalize_phone(k): v for k, v in raw.items()}
            if len(mapping) != len(raw) or len(set(mapping.values())) != len(mapping):
                return None
            if any(not isinstance(v, str) or not v or "/" in v for v in mapping.values()):
                return None
            merchant_id = mapping.get(phone)
            if merchant_id:
                _PHONE_CACHE[phone] = (merchant_id, now + CACHE_TTL_SECONDS)
            return merchant_id
        except Exception:
            return None

    # 2. Real PostgreSQL database lookup: strictly approved merchants with matching phone_e164
    if settings.DATABASE_BACKEND == "postgres" and settings.DATABASE_URL:
        try:
            import psycopg
            with psycopg.connect(settings.DATABASE_URL, connect_timeout=5) as conn:
                row = conn.execute(
                    "SELECT id FROM merchants WHERE phone_e164 = %s AND is_approved = true LIMIT 1",
                    (phone,)
                ).fetchone()
                if row:
                    merchant_id = str(row[0])
                    _PHONE_CACHE[phone] = (merchant_id, now + CACHE_TTL_SECONDS)
                    return merchant_id
                return None
        except Exception as e:
            logger.warning("PostgreSQL merchant lookup for %s failed: %s", phone, e)
            # If the database connection drops or times out, fall back to cached session if available
            if cached:
                logger.info("Preserving active session from cache for %s after DB timeout", phone)
                return cached[0]
            return None

    # 3. Unknown or unapproved senders MUST return None without any ledger access
    return None
