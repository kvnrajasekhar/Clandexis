"""SQL implementation of the existing persistent conversation-store protocol."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from psycopg.types.json import Jsonb
from app.services.whatsapp_conversation import ConversationBusy
from app.services import postgres_ledger as ledger


class PostgresConversationStore:
    def __init__(self, db, merchant_id):
        self.db, self.merchant_id = db, merchant_id

    def claim(self, message):
        now = datetime.now(timezone.utc)
        token = str(uuid4())
        with self.db.transaction(self.merchant_id) as conn:
            merchant = ledger._merchant(conn, self.merchant_id, message.sender_phone)
            conn.execute(
                "INSERT INTO conversations(merchant_id,sender_phone) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                (self.merchant_id, message.sender_phone),
            )
            state = conn.execute(
                "SELECT * FROM conversations WHERE merchant_id=%s FOR UPDATE",
                (self.merchant_id,),
            ).fetchone()
            previous = conn.execute(
                "SELECT * FROM workflow_messages WHERE merchant_id=%s AND source_channel='WHATSAPP' AND external_message_id=%s",
                (self.merchant_id, message.message_id),
            ).fetchone()
            if previous and previous["processing_status"] == "DONE":
                return (
                    dict(state),
                    previous["response_payload"]["outbound"],
                    None,
                    str(previous["id"]),
                )
            if state["lease_until"] and state["lease_until"] > now:
                raise ConversationBusy(
                    "Previous message is processing. Please retry shortly."
                )
            event = ledger._source(
                conn,
                merchant,
                message.message_id,
                payload=message.model_dump(mode="json"),
            )
            conn.execute(
                "UPDATE workflow_messages SET processing_status='PROCESSING',message_text=%s,source_payload=%s,received_at=%s WHERE merchant_id=%s AND id=%s",
                (
                    message.text_content,
                    Jsonb(message.model_dump(mode="json")),
                    message.timestamp,
                    self.merchant_id,
                    event,
                ),
            )
            conn.execute(
                "UPDATE conversations SET lease_token=%s,lease_until=%s WHERE merchant_id=%s",
                (token, now + timedelta(minutes=5), self.merchant_id),
            )
            state = dict(state)
            if state["active_candidate_id"]:
                row = ledger._candidate(
                    conn, self.merchant_id, state["active_candidate_id"]
                )
                state["active_candidate_id"] = str(row["id"])
                state["candidate_revision"] = (
                    row["snapshot_sha256"]
                    if state["candidate_revision"] == row["revision"]
                    else "stale"
                )
            return state, None, token, str(event)

    def record_deferred(self, message):
        with self.db.transaction(self.merchant_id) as conn:
            m = ledger._merchant(conn, self.merchant_id, message.sender_phone)
            event = ledger._source(
                conn, m, message.message_id, payload=message.model_dump(mode="json")
            )
            conn.execute(
                "UPDATE workflow_messages SET processing_status='DEFERRED' WHERE merchant_id=%s AND id=%s AND processing_status='RECEIVED'",
                (self.merchant_id, event),
            )

    def finish(self, state, event, token, outbound):
        with self.db.transaction(self.merchant_id) as conn:
            current = conn.execute(
                "SELECT * FROM conversations WHERE merchant_id=%s FOR UPDATE",
                (self.merchant_id,),
            ).fetchone()
            if str(current["lease_token"]) != token:
                raise ConversationBusy("Conversation changed. Please retry.")
            revision = None
            if state.get("active_candidate_id"):
                candidate = ledger._candidate(
                    conn, self.merchant_id, state["active_candidate_id"]
                )
                if state["candidate_revision"] != candidate["snapshot_sha256"]:
                    raise ConversationBusy(
                        "Candidate revision changed. Please review again."
                    )
                revision = candidate["revision"]
            conn.execute(
                """UPDATE conversations SET active_candidate_id=%s,candidate_revision=%s,latest_question=%s,
                missing_fields=%s,detected_language=%s,script_preference=%s,expires_at=%s,lease_token=NULL,lease_until=now(),updated_at=now()
                WHERE merchant_id=%s""",
                (
                    state.get("active_candidate_id"),
                    revision,
                    state.get("latest_question"),
                    Jsonb(state.get("missing_fields", [])),
                    state.get("detected_language", "en"),
                    state.get("script_preference", "Latin"),
                    state.get("expires_at"),
                    self.merchant_id,
                ),
            )
            conn.execute(
                "UPDATE workflow_messages SET processing_status='DONE',response_payload=%s,processed_at=now() WHERE merchant_id=%s AND id=%s",
                (
                    Jsonb(
                        {
                            "outbound": outbound.model_dump(mode="json"),
                            "candidate_id": state.get("active_candidate_id")
                            or state.get("last_candidate_id"),
                        }
                    ),
                    self.merchant_id,
                    event,
                ),
            )

    def release(self, token):
        with self.db.transaction(self.merchant_id) as conn:
            conn.execute(
                "UPDATE conversations SET lease_token=NULL,lease_until=now() WHERE merchant_id=%s AND lease_token=%s",
                (self.merchant_id, token),
            )
