"""Real Firestore + signed webhook identity and persistent conversation regressions."""
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4
import pytest

if not os.getenv("FIRESTORE_EMULATOR_HOST"):
    pytest.skip("Firestore emulator required", allow_module_level=True)

from tests.test_application_emulator import app_client, text_callback, callback
from tests.test_ledger_emulator import db, mid, sale, count, col, stock, seed_party
from app.schemas.transaction import TransactionCandidate, ExtractionResponse
from app.pipeline import PipelineService
from app.services.input_processor import InputProcessor
from app.services.validator import BusinessValidator
from app.services import ledger
from app.services.confirmation import AuthenticatedMerchant, get_confirmation_service, button_id
from app.services.whatsapp_conversation import ConversationStore, ConversationBusy, process_whatsapp_message
from app.services.whatsapp_identity import resolve_phone, normalize_phone
from models.canonical import InboundMessage, MessageType
from seed.seed_textile_msme import seed_demo_data
import agent_orchestrator.orchestrator as orchestrator
import webhook_service.main as application

A = "+918001111111"
B = "+919002222222"


def pipeline(monkeypatch, candidates):
    values = iter(candidates)
    p = PipelineService.__new__(PipelineService)
    p.input_processor = InputProcessor()
    p.validator = BusinessValidator()
    p.interpreter = SimpleNamespace(interpret=lambda **kw: ExtractionResponse(transactions=[next(values)]))
    monkeypatch.setattr(orchestrator, "pipeline", p)
    return p


def conversation(db, merchant):
    return db.collection("merchants").document(merchant).collection("conversations").document("whatsapp")


@pytest.fixture
def phones(app_client, db, mid, monkeypatch):
    second = "phone_919002222222_" + uuid4().hex
    seed_demo_data(merchant_id=second, shirt_stock=200, ramesh_opening=0, db=db)
    for merchant, phone in [(mid, A), (second, B)]:
        db.collection("merchants").document(merchant).update({"authorized_phones": [phone]})
        seed_party(db, merchant, "raju", "Raju", ["Raju"], 0)
    monkeypatch.setattr(application.settings, "WHATSAPP_DEMO_PHONE_MAP", json.dumps({A: mid, B: second}))
    return app_client, mid, second


def test_two_phones_two_replies_restart_and_confirm(phones, db, monkeypatch):
    (client, sender, _), first, second = phones
    incomplete = TransactionCandidate(transaction_type="sale", buyer_name="Raju",
        items=[{"item_name": "Cotton Shirt", "quantity": 10}], needs_clarification=True,
        missing_fields=["unit_price", "payment_details"], raw_transcript="Nenu Raju ki 10 shirts ammnu")
    price_only = TransactionCandidate(transaction_type="sale", buyer_name="Raju",
        items=[{"item_name": "Cotton Shirt", "quantity": 10, "unit_price": 200, "line_total": 2000}],
        total_amount=2000, needs_clarification=True, missing_fields=["payment_details"],
        raw_transcript="Nenu Raju ki 10 shirts ammnu. 200 each.")
    complete = sale(qty=10, price=200, cash=0, upi=500, credit=1500, buyer="Raju")
    pipeline(monkeypatch, [incomplete, incomplete.model_copy(deep=True)])
    text_callback(client, A, "Nenu Raju ki 10 shirts ammnu", "a.original")
    state_a = conversation(db, first).get().to_dict()
    cid = state_a["active_candidate_id"]
    assert state_a["detected_language"] == "te" and state_a["script_preference"] == "Latin"
    assert "price entha" in sender.sent[-1].text_content
    text_callback(client, B, "आज मैंने राजू को 10 शर्ट बेचीं।", "b.original")
    state_b = conversation(db, second).get().to_dict()
    assert "कीमत" in sender.sent[-1].text_content
    assert state_b["detected_language"] == "hi" and state_b["script_preference"] == "Devanagari"
    assert state_b["active_candidate_id"] != cid
    callback(client, A, state_b["active_candidate_id"], "a.foreign")
    assert count(db, first, "transactions") == count(db, second, "transactions") == 0
    # A new service/input processor reads the same Firestore conversation after restart.
    pipeline(monkeypatch, [price_only])
    text_callback(client, A, "200 each", "a.price")
    revised = conversation(db, first).get().to_dict()
    assert revised["active_candidate_id"] == cid
    assert revised["candidate_revision"] != state_a["candidate_revision"]
    assert "payment" in sender.sent[-1].text_content
    assert not sender.sent[-1].buttons
    restarted_state = subprocess.check_output([sys.executable, "-c",
        "from google.cloud import firestore; from app.services.whatsapp_conversation import ConversationStore; "
        "import sys; print(ConversationStore(firestore.Client(project='demo-clandexis'), sys.argv[1]).ref.get().to_dict()['active_candidate_id'])",
        first], text=True).strip()
    assert restarted_state == cid
    pipeline(monkeypatch, [complete])
    text_callback(client, A, "500 UPI, 1500 udhaar", "a.payment")
    final = conversation(db, first).get().to_dict()
    assert final["active_candidate_id"] == cid and final["missing_fields"] == []
    assert sender.sent[-1].buttons[0].id == button_id(cid, "confirm")
    assert count(db, first, "transaction_candidates") == 1 and count(db, first, "transactions") == 0
    text_callback(client, A, "YES", "a.unbound-yes")
    assert count(db, first, "transactions") == 0
    callback(client, A, cid, "a.confirm")
    callback(client, A, cid, "a.confirm")
    callback(client, A, cid, "a.confirm-again")
    assert count(db, first, "transactions") == 1 and stock(db, first) == 290
    assert stock(db, second) == 200 and count(db, second, "transactions") == 0
    assert ledger.get_party_balance(first, "raju", db=db).outstanding == 1500
    assert conversation(db, first).get().to_dict()["active_candidate_id"] is None
    assert conversation(db, second).get().to_dict()["active_candidate_id"] == state_b["active_candidate_id"]
    doc = col(db, first, "transaction_candidates").document(cid).get().to_dict()
    assert doc["original_extraction"]["items"][0]["unit_price"] is None
    assert len(doc["review_history"]) == 2
    events = [s.to_dict() for s in conversation(db, first).collection("messages").stream()]
    assert all(e["inbound"]["tenant_id"] == first and e["inbound"]["sender_phone"] == A for e in events)
    assert all(e["received_at"] for e in events)
    assert len(list(conversation(db, second).collection("messages").stream())) == 1
    text_callback(client, A, "PDF ledgers kavali", "a.report")
    assert count(db, first, "transactions") == 1
    print(json.dumps({"phone_A": A, "merchant_A": first, "A_transactions": 1,
        "A_stock": str(stock(db, first)), "A_credit": "1500.00", "phone_B": B,
        "merchant_B": second, "B_transactions": count(db, second, "transactions"),
        "B_stock": str(stock(db, second)), "A_history_path": conversation(db, first).path,
        "B_history_path": conversation(db, second).path}))


def test_unknown_and_misconfigured_identity_cannot_read_or_process(phones, db, monkeypatch):
    (client, sender, _), first, second = phones
    pipeline(monkeypatch, [])
    assert text_callback(client, "+919999999999", "sale", "unknown").json()["messages_received"] == 0
    assert not sender.sent
    assert resolve_phone(A) == first and resolve_phone(B) == second
    assert normalize_phone("+91 800-111-1111") == A
    assert normalize_phone("00918001111111") == A
    forged = InboundMessage(message_id="forged", tenant_id=second, sender_phone=A,
        message_type=MessageType.TEXT, text_content="read")
    with pytest.raises(ValueError, match="not authorized"):
        process_whatsapp_message(forged)
    assert not conversation(db, second).get().exists
    monkeypatch.setattr(application.settings, "WHATSAPP_DEMO_PHONE_MAP", json.dumps({A: first, B: first}))
    assert resolve_phone(A) is None and resolve_phone(B) is None


def test_pending_candidate_is_not_replaced_by_unrelated_history(phones, db, monkeypatch):
    (client, sender, _), first, second = phones
    tx = TransactionCandidate(transaction_type="sale", buyer_name="Raju",
        items=[{"item_name": "Cotton Shirt", "quantity": 10}], needs_clarification=True)
    pipeline(monkeypatch, [tx])
    text_callback(client, A, "Nenu Raju ki 10 shirts ammnu", "pending")
    cid = conversation(db, first).get().to_dict()["active_candidate_id"]
    unrelated = get_confirmation_service().stage(AuthenticatedMerchant(first), "dashboard.sale", 0, sale())
    text_callback(client, A, "Sold 5 kurtas to Suresh", "new-sale")
    assert "cancel" in sender.sent[-1].interactive_body
    assert conversation(db, first).get().to_dict()["active_candidate_id"] == cid
    callback(client, A, unrelated.candidate_id, "other-confirm")
    assert count(db, first, "transactions") == 0
    assert count(db, first, "transaction_candidates") == 2
    assert not conversation(db, second).get().exists


def test_concurrent_message_claim_is_persistent_and_serialized(phones, db):
    _, first, _ = phones
    m = InboundMessage(message_id="lease1", tenant_id=first, sender_phone=A,
                       message_type=MessageType.TEXT, text_content="sale")
    s = ConversationStore(db, first)
    state, cached, token, event = s.claim(m)
    restarted = ConversationStore(db, first)
    with pytest.raises(ConversationBusy):
        restarted.claim(m.model_copy(update={"message_id": "lease2"}))
    s.release(token)
    _, _, token2, _ = restarted.claim(m)
    assert token2 != token
    restarted.release(token2)


def test_concurrent_meta_confirm_posts_once(phones, db, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    (client, _, _), first, _ = phones
    pipeline(monkeypatch, [sale(qty=10, price=200, cash=0, upi=500, credit=1500, buyer="Raju")])
    text_callback(client, A, "Sold 10 shirts to Raju for 200 each, 500 UPI, 1500 credit", "start")
    cid = conversation(db, first).get().to_dict()["active_candidate_id"]
    message = InboundMessage(message_id="parallel-confirm", tenant_id=first, sender_phone=A,
        message_type=MessageType.INTERACTIVE_BUTTON, interactive_id=button_id(cid, "confirm"))
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: process_whatsapp_message(message), range(2)))
    assert count(db, first, "transactions") == 1 and stock(db, first) == 290
    process_whatsapp_message(message)
    assert count(db, first, "transactions") == 1


def test_batched_payload_histories_do_not_leak_other_sender(phones, db):
    from tests.test_application_emulator import signed_callback
    (client, _, _), first, second = phones
    messages = [{"from": p[1:], "id": "batch-" + p, "timestamp": "1795132800",
                 "type": "text", "text": {"body": "PDF ledgers kavali"}} for p in [A, B]]
    payload = {"object": "whatsapp_business_account", "entry": [{"id": "waba", "changes": [{
        "field": "messages", "value": {"messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15551737641", "phone_number_id": "phone"},
        "messages": messages}}]}]}
    assert signed_callback(client, payload).json()["messages_received"] == 2
    for merchant, phone, other in [(first, A, B), (second, B, A)]:
        event, = conversation(db, merchant).collection("messages").stream()
        source = json.dumps(event.to_dict()["inbound"]["raw_payload"])
        assert phone[1:] in source and other[1:] not in source
        assert count(db, merchant, "transactions") == count(db, merchant, "transaction_candidates") == 0
