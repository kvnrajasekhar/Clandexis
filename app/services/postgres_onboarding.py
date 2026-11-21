"""Durable, tenant-scoped state for WhatsApp merchant onboarding."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from models.canonical import InboundMessage


class OnboardingStoreError(RuntimeError):
    """Raised when durable onboarding state cannot be safely updated."""


class OnboardingLeaseLost(OnboardingStoreError):
    """Raised when another request owns or has replaced an onboarding lease."""


class OnboardingMerchantConflict(OnboardingStoreError):
    """Raised if the phone becomes registered before onboarding commits."""


@dataclass(frozen=True)
class OnboardingClaim:
    state: dict[str, Any]
    token: str | None = None
    duplicate: bool = False
    busy: bool = False


class PostgresOnboardingStore:
    """Persists active onboarding state and short-lived message idempotency records."""

    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("A PostgreSQL DSN is required for onboarding persistence")
        self.dsn = dsn

    def claim(self, message: InboundMessage) -> OnboardingClaim:
        phone = message.sender_phone
        external_id = message.message_id
        action_id = message.interactive_id
        token = str(uuid4())

        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as conn:
            conn.execute("SELECT set_config('app.onboarding_phone', %s, true)", (phone,))
            conn.execute(
                "DELETE FROM merchant_onboarding_events "
                "WHERE sender_phone=%s AND expires_at <= now()",
                (phone,),
            )
            conn.execute(
                """
                INSERT INTO merchant_onboarding_sessions (sender_phone, session_state, step, expires_at)
                VALUES (%s, '{}'::jsonb, 'INIT', now() + interval '30 days')
                ON CONFLICT (sender_phone) DO NOTHING
                """,
                (phone,),
            )
            session = conn.execute(
                """
                SELECT session_state, step, revision, expires_at, lease_token, lease_until
                FROM merchant_onboarding_sessions
                WHERE sender_phone=%s
                FOR UPDATE
                """,
                (phone,),
            ).fetchone()
            if session is None:
                raise OnboardingStoreError("Unable to load onboarding session")

            state = session["session_state"] or {}
            if (
                session["step"] != "COMPLETED"
                and session["expires_at"] <= datetime.now(timezone.utc)
            ):
                state = {}
                conn.execute(
                    """
                    UPDATE merchant_onboarding_sessions
                    SET session_state='{}'::jsonb, step='INIT', revision=revision+1,
                        lease_token=NULL, lease_until=NULL, updated_at=now()
                    WHERE sender_phone=%s
                    """,
                    (phone,),
                )
                session["lease_token"] = None
                session["lease_until"] = None

            event = conn.execute(
                """
                SELECT message_type, interactive_id, processing_status
                FROM merchant_onboarding_events
                WHERE sender_phone=%s AND external_message_id=%s
                FOR UPDATE
                """,
                (phone, external_id),
            ).fetchone()
            if event and (
                event["message_type"] != message.message_type.value
                or event["interactive_id"] != action_id
            ):
                raise OnboardingStoreError("A message ID was reused with different onboarding content")
            if event and event["processing_status"] == "DONE":
                return OnboardingClaim(state=state, duplicate=True)

            lease_until = session["lease_until"]
            if lease_until and lease_until > datetime.now(timezone.utc):
                if event is None:
                    conn.execute(
                        """
                        INSERT INTO merchant_onboarding_events
                            (sender_phone, external_message_id, message_type, interactive_id, processing_status)
                        VALUES (%s, %s, %s, %s, 'DEFERRED')
                        """,
                        (phone, external_id, message.message_type.value, action_id),
                    )
                return OnboardingClaim(state=state, busy=True)

            if event is None:
                conn.execute(
                    """
                    INSERT INTO merchant_onboarding_events
                        (sender_phone, external_message_id, message_type, interactive_id, processing_status)
                    VALUES (%s, %s, %s, %s, 'PROCESSING')
                    """,
                    (phone, external_id, message.message_type.value, action_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE merchant_onboarding_events
                    SET processing_status='PROCESSING', processed_at=NULL
                    WHERE sender_phone=%s AND external_message_id=%s
                    """,
                    (phone, external_id),
                )
            conn.execute(
                """
                UPDATE merchant_onboarding_sessions
                SET lease_token=%s, lease_until=now() + interval '5 minutes', updated_at=now()
                WHERE sender_phone=%s
                """,
                (token, phone),
            )
            state["revision"] = session["revision"]
            return OnboardingClaim(state=state, token=token)

    def finish(
        self,
        message: InboundMessage,
        token: str,
        session_state: dict[str, Any],
        *,
        activate_merchant: bool = False,
    ) -> str | None:
        phone = message.sender_phone
        state = dict(session_state)
        merchant_id: str | None = None
        if state.get("step") == "COMPLETED":
            state = {
                "phone": phone,
                "step": "COMPLETED",
                "preferred_language": state.get("preferred_language", "en"),
                "script_preference": state.get("script_preference", "Latin"),
            }

        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as conn:
            conn.execute("SELECT set_config('app.onboarding_phone', %s, true)", (phone,))
            current = conn.execute(
                """
                SELECT lease_token, revision
                FROM merchant_onboarding_sessions
                WHERE sender_phone=%s
                FOR UPDATE
                """,
                (phone,),
            ).fetchone()
            if current is None or current["lease_token"] != token:
                raise OnboardingLeaseLost("The onboarding session lease is no longer current")

            if activate_merchant:
                merchant_id = str(uuid4())
                conn.execute("SELECT set_config('app.merchant_id', %s, true)", (merchant_id,))
                inserted = conn.execute(
                    """
                    INSERT INTO merchants (
                        id, phone_e164, is_approved, business_name, owner_name,
                        preferred_language, script_preference, tax_registration_number, tax_address
                    ) VALUES (%s, %s, true, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (phone_e164) DO NOTHING
                    RETURNING id
                    """,
                    (
                        merchant_id,
                        phone,
                        session_state.get("business_name") or f"Shop {phone}",
                        session_state.get("owner_name") or "Merchant",
                        session_state.get("preferred_language", "en"),
                        session_state.get("script_preference", "Latin"),
                        session_state.get("gstin"),
                        Jsonb(
                            {
                                "gstin": session_state.get("gstin"),
                                "udyam": session_state.get("udyam_reg_no"),
                                "address": session_state.get("business_address"),
                                "pincode": session_state.get("pincode"),
                                "upi_vpa": session_state.get("upi_vpa"),
                            }
                        ),
                    ),
                ).fetchone()
                if inserted is None:
                    raise OnboardingMerchantConflict(
                        "A merchant account was created for this phone before onboarding completed"
                    )

            conn.execute(
                """
                UPDATE merchant_onboarding_sessions
                SET session_state=%s, step=%s, revision=revision+1,
                    expires_at=now() + interval '30 days',
                    lease_token=NULL, lease_until=NULL, updated_at=now()
                WHERE sender_phone=%s
                """,
                (Jsonb(state), state.get("step", "INIT"), phone),
            )
            updated = conn.execute(
                """
                UPDATE merchant_onboarding_events
                SET processing_status='DONE', processed_at=now()
                WHERE sender_phone=%s AND external_message_id=%s
                    AND processing_status='PROCESSING'
                """,
                (phone, message.message_id),
            )
            if updated.rowcount != 1:
                raise OnboardingStoreError("The onboarding event is no longer processable")
        return merchant_id

    def release(self, message: InboundMessage, token: str) -> None:
        phone = message.sender_phone
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=5) as conn:
            conn.execute("SELECT set_config('app.onboarding_phone', %s, true)", (phone,))
            conn.execute(
                """
                UPDATE merchant_onboarding_events
                SET processing_status='FAILED'
                WHERE sender_phone=%s AND external_message_id=%s
                    AND processing_status='PROCESSING'
                    AND EXISTS (
                        SELECT 1 FROM merchant_onboarding_sessions
                        WHERE sender_phone=%s AND lease_token=%s
                    )
                """,
                (phone, message.message_id, phone, token),
            )
            conn.execute(
                """
                UPDATE merchant_onboarding_sessions
                SET lease_token=NULL, lease_until=NULL, updated_at=now()
                WHERE sender_phone=%s AND lease_token=%s
                """,
                (phone, token),
            )
