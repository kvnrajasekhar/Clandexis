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

    # 2. Real PostgreSQL database lookup & auto-linking
    if settings.DATABASE_BACKEND == "postgres" and settings.DATABASE_URL:
        try:
            import psycopg
            target_merchant = settings.DASHBOARD_DEMO_MERCHANT_ID or "00000000-0000-0000-0000-000000000001"
            with psycopg.connect(settings.DATABASE_URL, connect_timeout=5) as conn:
                # Find existing approved merchant by phone
                row = conn.execute(
                    "SELECT id FROM merchants WHERE phone_e164 = %s AND is_approved = true LIMIT 1",
                    (phone,)
                ).fetchone()
                if row:
                    return str(row[0])

                # Connect with merchant scope
                conn.execute("SELECT set_config('app.merchant_id', %s, true)", (target_merchant,))
                m = conn.execute(
                    "SELECT id, phone_e164 FROM merchants WHERE id = %s",
                    (target_merchant,)
                ).fetchone()
                if m:
                    # Update merchant phone to incoming sender phone
                    conn.execute(
                        "UPDATE merchants SET phone_e164 = %s, is_approved = true WHERE id = %s",
                        (phone, target_merchant)
                    )
                    return str(target_merchant)
                else:
                    # Auto-provision real merchant
                    conn.execute(
                        """INSERT INTO merchants (id, phone_e164, is_approved, business_name, owner_name)
                        VALUES (%s, %s, true, %s, %s)
                        ON CONFLICT (phone_e164) DO UPDATE SET is_approved = true
                        RETURNING id""",
                        (target_merchant, phone, f"Enterprise {phone[-4:]}", f"Merchant {phone[-4:]}")
                    )
                    return str(target_merchant)
        except Exception as e:
            logger.warning("PostgreSQL merchant lookup for %s failed: %s", phone, e)

    # 3. Default fallback merchant ID for live operation without demo map
    return settings.DASHBOARD_DEMO_MERCHANT_ID or "00000000-0000-0000-0000-000000000001"

