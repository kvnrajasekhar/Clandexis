"""Persistent per-phone WhatsApp history and serialized application processing.

Ledger posting stays in the canonical confirmation service. Network/model calls run
outside Firestore callbacks. Events cache replies across process restarts.
"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4
from google.cloud import firestore
from app.services.confirmation import ConfirmationError, get_confirmation_service
from app.services.whatsapp_identity import normalize_phone, resolve_phone
from models.canonical import OutboundMessage


class ConversationBusy(ConfirmationError):
    pass


class ConversationStore:
    def __init__(self, db, merchant_id):
        self.db = db
        self.ref = db.collection("merchants").document(merchant_id).collection("conversations").document("whatsapp")

    def claim(self, message):
        now = datetime.now(timezone.utc)
        token = uuid4().hex
        event = self.ref.collection("messages").document(sha256(message.message_id.encode()).hexdigest())

        @firestore.transactional
        def claim(transaction):
            state = self.ref.get(transaction=transaction).to_dict() or {}
            previous = event.get(transaction=transaction).to_dict() or {}
            if state.get("sender_phone") not in (None, message.sender_phone):
                raise ConfirmationError("Conversation belongs to another phone")
            if previous.get("status") == "DONE":
                return state, previous["outbound"], None
            if state.get("lease_until", now) > now:
                raise ConversationBusy("Previous message is processing. Please retry shortly.")
            transaction.set(self.ref, {"sender_phone": message.sender_phone,
                "merchant_id": message.tenant_id, "lease_token": token,
                "lease_until": now + timedelta(minutes=5)}, merge=True)
            transaction.set(event, {"inbound": message.model_dump(mode="json"),
                "received_at": message.timestamp, "status": "PROCESSING", "lease_token": token})
            return state, None, token

        state, cached, token = claim(self.db.transaction())
        return state, cached, token, event

    def record_deferred(self, message):
        event = self.ref.collection("messages").document(sha256(message.message_id.encode()).hexdigest())

        @firestore.transactional
        def record(transaction):
            if not event.get(transaction=transaction).exists:
                transaction.set(event, {"inbound": message.model_dump(mode="json"),
                    "received_at": message.timestamp, "status": "DEFERRED"})
        record(self.db.transaction())

    def finish(self, state, event, token, outbound):
        @firestore.transactional
        def finish(transaction):
            current = self.ref.get(transaction=transaction).to_dict() or {}
            if current.get("lease_token") != token:
                raise ConversationBusy("Conversation changed. Please retry.")
            state.update(lease_until=datetime.now(timezone.utc), lease_token=None, updated_at=datetime.now(timezone.utc))
            transaction.set(self.ref, state, merge=True)
            transaction.update(event, {"status": "DONE", "outbound": outbound.model_dump(mode="json"),
                "candidate_id": state.get("active_candidate_id") or state.get("last_candidate_id"), "completed_at": datetime.now(timezone.utc)})
        finish(self.db.transaction())

    def release(self, token):
        @firestore.transactional
        def release(transaction):
            current = self.ref.get(transaction=transaction).to_dict() or {}
            if current.get("lease_token") == token:
                transaction.update(self.ref, {"lease_token": None, "lease_until": datetime.now(timezone.utc)})
        release(self.db.transaction())


def process_whatsapp_message(message, *, media_bytes=None):
    from agent_orchestrator import orchestrate_inbound_message
    phone = normalize_phone(message.sender_phone)
    if resolve_phone(phone) != message.tenant_id:
        raise ConfirmationError("Sender is not authorized for this merchant")
    message = message.model_copy(update={"sender_phone": phone})
    service = get_confirmation_service()
    store = ConversationStore(service.ledger.db, message.tenant_id)
    try:
        state, cached, token, event = store.claim(message)
    except ConversationBusy as exc:
        store.record_deferred(message)
        return None, OutboundMessage(recipient_phone=phone, text_content=str(exc))
    if cached:
        return None, OutboundMessage.model_validate(cached)
    try:
        result, outbound = orchestrate_inbound_message(message, media_bytes=media_bytes, conversation=state)
        store.finish(state, event, token, outbound)
        return result, outbound
    except Exception:
        store.release(token)
        raise
