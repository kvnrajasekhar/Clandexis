from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from app.schemas.transaction import TransactionCandidate
from app.services.confirmation import (
    AuthenticatedMerchant, ConfirmationError, ConfirmationService, button_id,
)
from models.canonical import InboundMessage, MessageType
from tests.confirmation_stub import LedgerStub

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)
MERCHANT = AuthenticatedMerchant("+919876543210")


def candidate(**changes):
    values = dict(transaction_type="sale", buyer_name="Ravi", total_amount=100,
                  amount_paid=100, credit_amount=0,
                  items=[dict(item_name="Shirt", quantity=1, unit_price=100, line_total=100)],
                  payments=[dict(method="cash", amount=100)], validation_status="ACCEPTED")
    return TransactionCandidate(**(values | changes))


@pytest.fixture
def bridge():
    ledger = LedgerStub()
    return ConfirmationService(ledger, lambda: NOW), ledger


def stage(bridge, tx=None, event="original"):
    return bridge[0].stage(MERCHANT, event, 0, tx or candidate())


def test_confirm_exactly_once_and_database_results(bridge):
    service, ledger = bridge
    row = stage(bridge)
    first = service.handle_confirmation(MERCHANT, row.candidate_id, "confirm", "callback1")
    assert first.transaction_id == "txn_db_1"
    assert first.amount_paid == 73
    assert first.credit_outstanding == 27
    assert first.inventory_balance == {"shirt": 18}
    assert first.posting_status == "COMMITTED"
    assert service.handle_confirmation(MERCHANT, row.candidate_id, "confirm", "callback1") == first
    assert service.handle_confirmation(MERCHANT, row.candidate_id, "confirm", "callback2") == first
    assert ledger.post_count == 1


def test_concurrent_callbacks_use_atomic_ledger_contract(bridge):
    service, ledger = bridge
    row = stage(bridge)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda n: service.handle_confirmation(
            MERCHANT, row.candidate_id, "confirm", f"event{n}"), range(8)))
    assert len({r.transaction_id for r in results}) == 1
    assert ledger.post_count == 1


def test_cancel_never_posts_and_cannot_later_confirm(bridge):
    service, ledger = bridge
    row = stage(bridge)
    for _ in range(2):
        assert service.handle_confirmation(MERCHANT, row.candidate_id, "cancel", "cancel1") == "CANCELLED"
    with pytest.raises(ConfirmationError):
        service.handle_confirmation(MERCHANT, row.candidate_id, "confirm", "confirm2")
    assert ledger.post_count == 0


def test_wrong_merchant(bridge):
    service, ledger = bridge
    row = stage(bridge)
    with pytest.raises(ConfirmationError):
        service.handle_confirmation(AuthenticatedMerchant("other"), row.candidate_id, "confirm", "evil")
    assert ledger.post_count == 0


@pytest.mark.parametrize("status", ["NEEDS_REVIEW", "REJECTED"])
def test_unsafe_candidate_never_posts(bridge, status):
    service, ledger = bridge
    row = stage(bridge, candidate(validation_status=status))
    with pytest.raises(ConfirmationError):
        service.handle_confirmation(MERCHANT, row.candidate_id, "confirm", "unsafe")
    assert ledger.post_count == 0


def test_review_requires_note_and_revalidation(bridge):
    service, ledger = bridge
    row = stage(bridge, candidate(validation_status="NEEDS_REVIEW", needs_clarification=True))
    with pytest.raises(ConfirmationError):
        service.resolve_review(MERCHANT, row.candidate_id, candidate(), "")
    with pytest.raises(ConfirmationError):
        service.resolve_review(MERCHANT, row.candidate_id, candidate(amount_paid=200), "Checked original")
    resolved = service.resolve_review(MERCHANT, row.candidate_id, candidate(), "Merchant verified payment")
    assert resolved.revision == 2 and resolved.review_resolved
    service.handle_confirmation(MERCHANT, row.candidate_id, "confirm", "reviewed")
    assert ledger.post_count == 1


def test_rejected_cannot_be_reviewed_into_posting(bridge):
    service, _ = bridge
    row = stage(bridge, candidate(validation_status="REJECTED"))
    with pytest.raises(ConfirmationError):
        service.resolve_review(MERCHANT, row.candidate_id, candidate(), "Please approve")


def test_expired_and_invalid_actions(bridge):
    service, ledger = bridge
    row = stage(bridge)
    ledger.rows[row.candidate_id] = replace(row, expires_at=NOW - timedelta(seconds=1))
    for action in ("confirm", "cancel", "bogus"):
        with pytest.raises(ConfirmationError):
            service.handle_confirmation(MERCHANT, row.candidate_id, action, "callback")
    assert ledger.post_count == 0


def test_stage_retry_returns_persisted_id(bridge):
    assert stage(bridge).candidate_id == stage(bridge).candidate_id


def test_safe_text_fallback_and_strict_button_ids(bridge):
    service, _ = bridge
    text = SimpleNamespace(message_type=MessageType.TEXT, text_content="YES")
    with pytest.raises(ConfirmationError):
        service.parse_reply(MERCHANT, text)
    row = stage(bridge)
    assert service.parse_reply(MERCHANT, text) == (row.candidate_id, "confirm")
    text.text_content = "NO"
    assert service.parse_reply(MERCHANT, text) == (row.candidate_id, "cancel")
    stage(bridge, event="second")
    with pytest.raises(ConfirmationError):
        service.parse_reply(MERCHANT, text)
    for invalid in ("CONFIRM_original", "clx:v1:confirm:", "clx:v1:confirm:../other", ""):
        with pytest.raises(ConfirmationError):
            service.parse_reply(MERCHANT, SimpleNamespace(
                message_type=MessageType.INTERACTIVE_BUTTON, interactive_id=invalid))
    assert service.parse_reply(MERCHANT, SimpleNamespace(message_type=MessageType.INTERACTIVE_BUTTON,
        interactive_id=button_id(row.candidate_id, "confirm"))) == (row.candidate_id, "confirm")


def test_existing_meta_adapter_real_reply_routes_without_extractor(bridge, monkeypatch):
    from webhook_service.meta_whatsapp_handler import MetaWhatsAppService
    from models.whatsapp_meta import MetaMessage
    import agent_orchestrator.orchestrator as orchestrator
    import app.services.confirmation as confirmation
    row = stage(bridge)
    monkeypatch.setattr(confirmation, "_service", bridge[0])
    monkeypatch.setattr(orchestrator, "pipeline", SimpleNamespace(
        process_and_interpret=lambda **kw: pytest.fail("Confirmation re-extracted input")))
    # Use the existing adapter's normalization of a real WhatsApp interactive shape.
    service = MetaWhatsAppService.__new__(MetaWhatsAppService)
    raw = dict(id="wamid.reply", **{"from": "919876543210"}, timestamp="1795132800", type="interactive",
               interactive=dict(type="button_reply", button_reply=dict(
                   id=button_id(row.candidate_id, "confirm"), title="Confirm")))
    message = service._normalize_message(MetaMessage.model_validate(raw), MERCHANT.merchant_id,
                                         MERCHANT.merchant_id, None, raw)
    result, outbound = orchestrator.orchestrate_inbound_message(message)
    assert result.transaction_id == "txn_db_1"
    assert "73.00" in outbound.text_content and "27.00" in outbound.text_content
    assert "shirt: 18" in outbound.text_content


def test_unconfigured_bridge_fails_closed(monkeypatch):
    import app.services.confirmation as confirmation
    import agent_orchestrator.orchestrator as orchestrator
    monkeypatch.setattr(confirmation, "_service", None)
    monkeypatch.setattr(orchestrator, "pipeline", SimpleNamespace(
        process_and_interpret=lambda **kw: pytest.fail("Unconfigured posting path")))
    _, out = orchestrator.orchestrate_inbound_message(InboundMessage(
        message_id="x", tenant_id=MERCHANT.merchant_id, sender_phone=MERCHANT.merchant_id,
        message_type=MessageType.TEXT, text_content="YES"))
    assert "Nothing has been posted" in out.text_content


def test_changing_validation_label_alone_cannot_bypass_review(bridge):
    service, ledger = bridge
    row = stage(bridge, candidate(validation_status="NEEDS_REVIEW"))
    ledger.rows[row.candidate_id] = replace(row, candidate=candidate())
    with pytest.raises(ConfirmationError, match="Explicit review"):
        service.handle_confirmation(MERCHANT, row.candidate_id, "confirm", "bypass")
    assert ledger.post_count == 0
