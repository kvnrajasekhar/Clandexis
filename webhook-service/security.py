"""
Security utilities for Meta WhatsApp Business Cloud API.
Validates HMAC-SHA256 signatures from X-Hub-Signature-256 header to guarantee authenticity.
"""

import hashlib
import hmac
import logging
from typing import Optional

logger = logging.getLogger("webhook-security")


def verify_meta_signature(payload_bytes: bytes, signature_header: Optional[str], app_secret: str) -> bool:
    """
    Verifies the X-Hub-Signature-256 header sent by Meta WhatsApp Cloud API webhooks.
    Format: 'sha256=<hex_digest>'
    """
    if not app_secret:
        logger.error("META_APP_SECRET is not configured; rejecting webhook.")
        return False

    if not signature_header:
        logger.error("Missing X-Hub-Signature-256 header in incoming Meta webhook.")
        return False

    if not signature_header.startswith("sha256="):
        logger.error("Malformed signature header format")
        return False

    expected_signature = signature_header.split("sha256=")[-1]
    calculated_signature = hmac.new(
        key=app_secret.encode("utf-8"),
        msg=payload_bytes,
        digestmod=hashlib.sha256,
    ).hexdigest()

    is_valid = hmac.compare_digest(calculated_signature, expected_signature)
    if not is_valid:
        logger.error("Meta webhook HMAC-SHA256 signature verification failed!")
    return is_valid
