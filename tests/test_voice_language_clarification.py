"""Integration & regression tests for WhatsApp voice language detection and in-chat clarification."""

import pytest
from types import SimpleNamespace
from decimal import Decimal

from models.canonical import InboundMessage, MessageType, CanonicalButton
from app.schemas.transaction import (
    ExtractionResponse,
    ProcessedInputPayload,
    ProcessingResult,
    TransactionCandidate,
    LineItem,
    PaymentRecord,
)
from app.services.conversation_language import resolve_language_and_script, question
import agent_orchestrator.orchestrator as orchestrator
import app.services.confirmation as confirmation
from tests.confirmation_stub import LedgerStub


def _run_orchestrator(monkeypatch, inbound: InboundMessage, tx: TransactionCandidate, conversation=None, ledger=None):
    if ledger is None:
        ledger = LedgerStub()
    monkeypatch.setattr(
        confirmation, "_service", confirmation.ConfirmationService(ledger)
    )
    result = ProcessingResult(
        input_metadata=ProcessedInputPayload(
            merchant_id=inbound.tenant_id,
            message_id=inbound.message_id,
            idempotency_key=inbound.message_id,
        ),
        extraction=ExtractionResponse(transactions=[tx]),
    )
    monkeypatch.setattr(
        orchestrator,
        "pipeline",
        SimpleNamespace(process_and_interpret=lambda **kwargs: result),
    )
    conv = conversation if conversation is not None else {}
    res, outbound = orchestrator.orchestrate_inbound_message(
        inbound,
        media_bytes=b"fake-audio-bytes" if inbound.message_type == MessageType.AUDIO else None,
        conversation=conv,
    )
    return ledger, res, outbound, conv


def test_hindi_voice_missing_price_asks_hindi_devanagari(monkeypatch):
    """TASK 5.A: Hindi voice note with missing price receives Hindi Devanagari question."""
    hindi_audio_tx = TransactionCandidate(
        schema_version="1.1",
        detected_language="Hindi",
        raw_transcript="मैंने राजू को 10 शर्ट बेची हैं",
        transaction_type="sale",
        buyer_name="राजू",
        items=[LineItem(item_name="Cotton Shirt", quantity=10.0)],
        needs_clarification=True,
        missing_fields=["unit_price", "payment_details"],
        validation_status="NEEDS_REVIEW",
    )
    inbound = InboundMessage(
        message_id="msg_hindi_voice",
        tenant_id="merchant_hindi",
        sender_phone="+919800000001",
        message_type=MessageType.AUDIO,
        media_url="https://example.com/hindi_audio.ogg",
        text_content=None,  # Audio has no text_content upfront
    )
    conversation = {"detected_language": "en", "script_preference": "Latin"}  # stale chat pref

    ledger, res, outbound, conv = _run_orchestrator(monkeypatch, inbound, hindi_audio_tx, conversation)

    # 1. Spoken language overrides stale English preference
    assert conv["detected_language"] == "hi"
    assert conv["script_preference"] == "Devanagari"

    # 2. Asks for price in Hindi Devanagari
    assert "कीमत" in outbound.text_content
    assert "लेन-देन का विवरण" in outbound.text_content
    assert "ग्राहक: राजू" in outbound.text_content
    assert not outbound.buttons  # No confirm buttons exposed during clarification


def test_telugu_voice_missing_payment_asks_telugu_script(monkeypatch):
    """TASK 5.B: Telugu voice note with missing payment receives Telugu-script question."""
    telugu_audio_tx = TransactionCandidate(
        schema_version="1.1",
        detected_language="Telugu",
        raw_transcript="రాజుకి 10 షర్టులు 2000 కి అమ్మాను",
        transaction_type="sale",
        buyer_name="రాజు",
        items=[LineItem(item_name="Cotton Shirt", quantity=10.0, unit_price=200.0, line_total=2000.0)],
        total_amount=2000.0,
        needs_clarification=True,
        missing_fields=["payment_details"],
        validation_status="NEEDS_REVIEW",
    )
    inbound = InboundMessage(
        message_id="msg_telugu_voice",
        tenant_id="merchant_telugu",
        sender_phone="+919800000002",
        message_type=MessageType.AUDIO,
        media_url="https://example.com/telugu_audio.ogg",
        text_content=None,
    )
    conversation = {}

    ledger, res, outbound, conv = _run_orchestrator(monkeypatch, inbound, telugu_audio_tx, conversation)

    assert conv["detected_language"] == "te"
    assert conv["script_preference"] == "Telugu"

    # 3. Asks for payment in Telugu script without generic dashboard redirection
    assert "Please review the unresolved details in the dashboard" not in outbound.text_content
    assert "ఎంత" in outbound.text_content or "నగదా" in outbound.text_content
    assert "లావాదేవీ వివరాలు" in outbound.text_content
    assert not outbound.buttons


def test_english_voice_receives_english_response(monkeypatch):
    """TASK 5.C: English voice note receives English response."""
    english_audio_tx = TransactionCandidate(
        schema_version="1.1",
        detected_language="English",
        raw_transcript="Sold 10 shirts to Ramesh for 2000 cash",
        transaction_type="sale",
        buyer_name="Ramesh",
        items=[LineItem(item_name="Cotton Shirt", quantity=10.0, unit_price=200.0, line_total=2000.0)],
        total_amount=2000.0,
        amount_paid=2000.0,
        payments=[PaymentRecord(method="cash", amount=2000.0)],
        validation_status="ACCEPTED",
    )
    inbound = InboundMessage(
        message_id="msg_en_voice",
        tenant_id="merchant_en",
        sender_phone="+919800000003",
        message_type=MessageType.AUDIO,
        text_content=None,
    )
    conversation = {}

    ledger, res, outbound, conv = _run_orchestrator(monkeypatch, inbound, english_audio_tx, conversation)

    assert conv["detected_language"] == "en"
    assert conv["script_preference"] == "Latin"
    assert outbound.message_type == MessageType.INTERACTIVE_BUTTON
    assert "Transaction details" in outbound.interactive_body
    assert [b.title for b in outbound.buttons] == ["✅ Confirm", "❌ Cancel"]


def test_voice_clarification_flow_through_confirm(monkeypatch):
    """TASK 5.F & 5.G: Partial clarification updates SAME candidate and leads to localized Confirm buttons."""
    step1_tx = TransactionCandidate(
        schema_version="1.1",
        detected_language="Telugu",
        raw_transcript="రాజుకి 10 షర్టులు అమ్మాను",
        transaction_type="sale",
        buyer_name="రాజు",
        items=[LineItem(item_name="Cotton Shirt", quantity=10.0)],
        needs_clarification=True,
        missing_fields=["unit_price", "payment_details"],
        validation_status="NEEDS_REVIEW",
    )
    inbound1 = InboundMessage(
        message_id="msg_flow_01",
        tenant_id="merchant_flow",
        sender_phone="+919800000004",
        message_type=MessageType.AUDIO,
        text_content=None,
    )
    conversation = {}

    ledger, res1, out1, conv = _run_orchestrator(monkeypatch, inbound1, step1_tx, conversation)
    cand_id = conv["active_candidate_id"]
    assert conv["detected_language"] == "te"
    assert conv["script_preference"] == "Telugu"
    assert not out1.buttons

    # Step 2: Merchant clarifies price with short text "200 each"
    # Even though "200 each" is English-like, it MUST preserve Telugu script candidate language
    step2_tx = TransactionCandidate(
        schema_version="1.1",
        detected_language="Telugu",
        transaction_type="sale",
        buyer_name="రాజు",
        items=[LineItem(item_name="Cotton Shirt", quantity=10.0, unit_price=200.0, line_total=2000.0)],
        total_amount=2000.0,
        needs_clarification=True,
        missing_fields=["payment_details"],
        validation_status="NEEDS_REVIEW",
    )
    inbound2 = InboundMessage(
        message_id="msg_flow_02",
        tenant_id="merchant_flow",
        sender_phone="+919800000004",
        message_type=MessageType.TEXT,
        text_content="200 each",
    )
    _, res2, out2, conv = _run_orchestrator(monkeypatch, inbound2, step2_tx, conv, ledger=ledger)
    assert conv["active_candidate_id"] == cand_id  # Same candidate
    assert conv["detected_language"] == "te"
    assert conv["script_preference"] == "Telugu"
    assert not out2.buttons
    assert "మొత్తం" in out2.text_content

    # Step 3: Merchant completes payment: "500 UPI, 1500 baaki"
    step3_tx = TransactionCandidate(
        schema_version="1.1",
        detected_language="Telugu",
        transaction_type="sale",
        buyer_name="రాజు",
        items=[LineItem(item_name="Cotton Shirt", quantity=10.0, unit_price=200.0, line_total=2000.0)],
        total_amount=2000.0,
        amount_paid=500.0,
        credit_amount=1500.0,
        payments=[PaymentRecord(method="upi", amount=500.0)],
        validation_status="ACCEPTED",
    )
    inbound3 = InboundMessage(
        message_id="msg_flow_03",
        tenant_id="merchant_flow",
        sender_phone="+919800000004",
        message_type=MessageType.TEXT,
        text_content="500 UPI, 1500 baaki",
    )
    _, res3, out3, conv = _run_orchestrator(monkeypatch, inbound3, step3_tx, conv, ledger=ledger)

    # 4. Complete and valid -> native Telugu buttons served!
    assert out3.message_type == MessageType.INTERACTIVE_BUTTON
    assert [b.title for b in out3.buttons] == ["✅ నిర్ధారించు", "❌ రద్దు"]
    assert out3.buttons[0].id.startswith(f"clx:v1:confirm:{cand_id}")


def test_unclear_customer_asks_localized_customer_question(monkeypatch):
    """TASK 5.E: Unclear customer triggers localized customer clarification."""
    no_customer_tx = TransactionCandidate(
        schema_version="1.1",
        detected_language="Hindi",
        raw_transcript="10 शर्ट बेची ₹2000 की",
        transaction_type="sale",
        buyer_name=None,  # No customer
        items=[LineItem(item_name="Cotton Shirt", quantity=10.0, unit_price=200.0, line_total=2000.0)],
        total_amount=2000.0,
        needs_clarification=True,
        missing_fields=["buyer_name"],
        validation_status="NEEDS_REVIEW",
    )
    inbound = InboundMessage(
        message_id="msg_no_cust",
        tenant_id="merchant_nocust",
        sender_phone="+919800000005",
        message_type=MessageType.AUDIO,
        text_content=None,
    )
    conversation = {}

    ledger, res, outbound, conv = _run_orchestrator(monkeypatch, inbound, no_customer_tx, conversation)

    assert conv["detected_language"] == "hi"
    assert conv["script_preference"] == "Devanagari"
    assert "ग्राहक" in outbound.text_content
    assert not outbound.buttons
