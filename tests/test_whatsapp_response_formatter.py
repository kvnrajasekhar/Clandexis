import asyncio
from types import MethodType, SimpleNamespace

import pytest

from app.schemas.transaction import ExtractionResponse, ProcessedInputPayload, ProcessingResult
from app.services.conversation_language import preference
from app.services.whatsapp_response_formatter import candidate_summary
from models.canonical import InboundMessage, MessageType
from tests.confirmation_stub import LedgerStub
from tests.test_confirmation import candidate


def _outbound(monkeypatch, text, tx, conversation=None):
    import agent_orchestrator.orchestrator as orchestrator
    import app.services.confirmation as confirmation

    ledger = LedgerStub()
    monkeypatch.setattr(
        confirmation, "_service", confirmation.ConfirmationService(ledger)
    )
    result = ProcessingResult(
        input_metadata=ProcessedInputPayload(
            merchant_id="merchant", message_id="message", idempotency_key="message"
        ),
        extraction=ExtractionResponse(transactions=[tx]),
    )
    monkeypatch.setattr(
        orchestrator,
        "pipeline",
        SimpleNamespace(process_and_interpret=lambda **kwargs: result),
    )
    _, outbound = orchestrator.orchestrate_inbound_message(
        InboundMessage(
            message_id="message",
            tenant_id="merchant",
            sender_phone="+918000000001",
            message_type=MessageType.TEXT,
            text_content=text,
        ),
        conversation=conversation or {},
    )
    return ledger, outbound


def test_romanized_telugu_summary_and_native_buttons_are_consistent(monkeypatch):
    ledger, outbound = _outbound(
        monkeypatch,
        "Nenu Raju ki shirts ammanu",
        candidate(),
    )

    assert len(ledger.rows) == 1 and ledger.post_count == 0
    assert outbound.message_type == MessageType.INTERACTIVE_BUTTON
    assert "Transaction vivaralu" in outbound.interactive_body
    assert "Vachina payment" in outbound.interactive_body
    assert "Migatha baaki" in outbound.interactive_body
    assert "Party:" not in outbound.interactive_body
    assert [button.title for button in outbound.buttons] == ["✅ Confirm", "❌ Cancel"]
    assert outbound.buttons[0].id.startswith("clx:v1:confirm:")


def test_hindi_devanagari_summary_and_buttons_are_consistent(monkeypatch):
    _, outbound = _outbound(
        monkeypatch,
        "मैंने राजू को सामान बेचा",
        candidate(),
    )

    assert "लेन-देन का विवरण" in outbound.interactive_body
    assert "प्राप्त भुगतान" in outbound.interactive_body
    assert "बाकी उधार" in outbound.interactive_body
    assert [button.title for button in outbound.buttons] == ["✅ पुष्टि करें", "❌ रद्द करें"]


def test_short_clarification_keeps_active_candidate_language():
    previous = {"detected_language": "te", "script_preference": "Latin"}
    assert preference("500 UPI", previous) == ("te", "Latin")
    assert preference("नया लेन-देन", previous) == ("hi", "Devanagari")


def test_review_summary_never_exposes_confirm_buttons(monkeypatch):
    held = candidate(validation_status="NEEDS_REVIEW", needs_clarification=True)
    held.missing_fields = ["payment_details"]
    _, outbound = _outbound(monkeypatch, "Nenu sale chesanu", held)

    assert not outbound.buttons
    assert "payment" in outbound.text_content.lower()
    assert "Confirm" not in outbound.text_content


def test_formatter_does_not_render_none_as_financial_fact():
    held = candidate(validation_status="NEEDS_REVIEW", needs_clarification=True)
    held.items[0].unit_price = None
    held.items[0].line_total = None
    text = candidate_summary(held, "candidate-1", "te", "Latin", review=True)
    assert "None" not in text


def test_meta_adapter_builds_native_candidate_specific_buttons():
    from models.canonical import CanonicalButton
    from webhook_service.meta_whatsapp_handler import MetaWhatsAppService

    captured = []
    service = MetaWhatsAppService.__new__(MetaWhatsAppService)

    async def capture(self, payload):
        captured.append(payload)
        return None

    service._post_message = MethodType(capture, service)
    asyncio.run(
        service.send_interactive_buttons(
            "+918000000001",
            "Transaction vivaralu",
            [
                CanonicalButton(id="clx:v1:confirm:candidate-1", title="✅ Confirm"),
                CanonicalButton(id="clx:v1:cancel:candidate-1", title="❌ Cancel"),
            ],
        )
    )

    payload = captured[0].model_dump(mode="json", exclude_none=True)
    replies = payload["interactive"]["action"]["buttons"]
    assert payload["type"] == "interactive"
    assert replies[0]["reply"]["id"] == "clx:v1:confirm:candidate-1"
    assert replies[1]["reply"]["id"] == "clx:v1:cancel:candidate-1"
