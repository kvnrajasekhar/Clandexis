"""Real phone resolution with database lookup and backward-compatible allowlist."""
import json
import logging
import re
from config import settings

logger = logging.getLogger(__name__)


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
    try:
        phone = normalize_phone(value)
    except (ValueError, TypeError, AttributeError):
        return None

    # 1. Backward-compatible demo phone map (used by isolated unit tests)
    demo_map_raw = settings.WHATSAPP_DEMO_PHONE_MAP
    if demo_map_raw and demo_map_raw.strip() not in ("", "{}"):
        try:
            raw = json.loads(demo_map_raw)
            mapping = {normalize_phone(k): v for k, v in raw.items()}
            if len(mapping) != len(raw) or len(set(mapping.values())) != len(mapping):
                return None
            if any(not isinstance(v, str) or not v or "/" in v for v in mapping.values()):
                return None
            if phone in mapping:
                return mapping[phone]
            return None
        except Exception:
            return None

    # 2. Real PostgreSQL database lookup
    if settings.DATABASE_BACKEND == "postgres" and settings.DATABASE_URL:
        try:
            import psycopg
            with psycopg.connect(settings.DATABASE_URL, connect_timeout=5) as conn:
                row = conn.execute(
                    "SELECT id FROM merchants WHERE phone_e164 = %s AND is_approved = true LIMIT 1",
                    (phone,)
                ).fetchone()
                if row:
                    return str(row[0])
                return None
        except Exception as e:
            logger.warning("PostgreSQL merchant lookup for %s failed: %s", phone, e)
            return None

    # 3. Default fallback: not found / unregistered
    return None

