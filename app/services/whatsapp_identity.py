"""Explicit buildathon phone allowlist. No implicit merchant provisioning."""
import json
import re


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
    from config import settings
    try:
        phone = normalize_phone(value)
        raw = json.loads(settings.WHATSAPP_DEMO_PHONE_MAP)
        mapping = {normalize_phone(k): v for k, v in raw.items()}
        if len(mapping) != len(raw) or len(set(mapping.values())) != len(mapping):
            return None
        if any(not isinstance(v, str) or not v or "/" in v for v in mapping.values()):
            return None
        return mapping.get(phone)
    except (ValueError, TypeError, AttributeError):
        return None
