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
        persisted = next(iter(ledger.rows))
        assert out.buttons[0].id == f"clx:v1:confirm:{persisted}"
        assert out.buttons[1].id == f"clx:v1:cancel:{persisted}"
        assert out.message_type == MessageType.INTERACTIVE_BUTTON
        assert "Nothing posted yet" in out.interactive_body
        assert "Recorded" not in out.interactive_body
