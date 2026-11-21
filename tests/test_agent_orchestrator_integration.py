"""Deterministic orchestration tests; provider calls are outside this boundary."""
from types import SimpleNamespace
import pytest
from app.schemas.transaction import ProcessingResult, ProcessedInputPayload, ExtractionResponse
from models.canonical import InboundMessage, MessageType
from tests.confirmation_stub import LedgerStub
from tests.test_confirmation import candidate


@pytest.mark.parametrize("status", ["ACCEPTED", "NEEDS_REVIEW", "REJECTED"])
def test_orchestrator_stages_without_claiming_commit(monkeypatch, status):
    import agent_orchestrator.orchestrator as orchestrator
    import app.services.confirmation as confirmation
    ledger = LedgerStub()
    monkeypatch.setattr(confirmation, "_service", confirmation.ConfirmationService(ledger))
    tx = candidate(validation_status=status, needs_clarification=status == "NEEDS_REVIEW")
    result = ProcessingResult(input_metadata=ProcessedInputPayload(
        merchant_id="merchant", message_id="original", idempotency_key="original"),
        extraction=ExtractionResponse(transactions=[tx]))
    monkeypatch.setattr(orchestrator, "pipeline", SimpleNamespace(process_and_interpret=lambda **kw: result))
    message = InboundMessage(message_id="original", tenant_id="merchant", sender_phone="merchant",
                             message_type=MessageType.TEXT, text_content="A sale")
    _, out = orchestrator.orchestrate_inbound_message(message)
    assert ledger.post_count == 0
    if status == "REJECTED":
        assert not ledger.rows
        assert "Transaction Rejected" in out.text_content
    else:
        assert len(ledger.rows) == 1
        if status == "NEEDS_REVIEW":
            assert not out.buttons and "Nothing posted" in out.text_content
            return
        persisted = next(iter(ledger.rows))
        assert out.buttons[0].id == f"clx:v1:confirm:{persisted}"
        assert out.buttons[1].id == f"clx:v1:cancel:{persisted}"
        assert out.message_type == MessageType.INTERACTIVE_BUTTON
        assert "Nothing posted yet" in out.interactive_body
        assert "Recorded" not in out.interactive_body


@pytest.mark.parametrize("intent", ["unknown", "query"])
def test_non_financial_intents_never_stage(monkeypatch, intent):
    import agent_orchestrator.orchestrator as orchestrator
    import app.services.confirmation as confirmation
    ledger = LedgerStub()
    monkeypatch.setattr(confirmation, "_service", confirmation.ConfirmationService(ledger))
    result = ProcessingResult(input_metadata=ProcessedInputPayload(
        merchant_id="merchant", message_id="report", idempotency_key="report"),
        extraction=ExtractionResponse(transactions=[candidate(transaction_type=intent)]))
    monkeypatch.setattr(orchestrator, "pipeline", SimpleNamespace(process_and_interpret=lambda **kw: result))
    _, out = orchestrator.orchestrate_inbound_message(InboundMessage(
        message_id="report", tenant_id="merchant", sender_phone="merchant",
        message_type=MessageType.TEXT, text_content="Unsupported request"))
    assert not ledger.rows and ledger.post_count == 0
    assert "Nothing posted" in out.text_content


def test_stale_onboarding_button_never_reaches_transaction_pipeline(monkeypatch):
    import agent_orchestrator.orchestrator as orchestrator
    monkeypatch.setattr(
        orchestrator,
        "pipeline",
        SimpleNamespace(process_and_interpret=lambda **kw: pytest.fail("Onboarding button reached extraction")),
    )

    _, outbound = orchestrator.orchestrate_inbound_message(InboundMessage(
        message_id="stale-onboarding-edit",
        tenant_id="merchant",
        sender_phone="+919876543210",
        message_type=MessageType.INTERACTIVE_BUTTON,
        text_content="Edit Details",
        interactive_id="MSME_ONBOARD_EDIT_0123456789abcdef",
    ))

    assert "no longer available" in outbound.text_content
