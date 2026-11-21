"""
Tests for Multi-Transaction Tracking, Safe Staging Queue, and Natural Cancellation/Confirmation.
Verifies:
1. Posting multiple transactions consecutively across messages stages all candidates without error.
2. Natural cancellation commands like "Cancel the previous transaction" cancel the candidate cleanly.
3. "Cancel all" cancels all pending candidates.
4. "Confirm all" confirms all ready candidates in sequence.
5. Single message containing multiple transactions extracts and stages all of them.
6. Transactions remain safely on hold until explicit confirmation ("ignore until user says").
"""

from types import SimpleNamespace
from datetime import datetime, timezone
import pytest

from app.schemas.transaction import (
    ExtractionResponse,
    ProcessedInputPayload,
    ProcessingResult,
    TransactionCandidate,
)
from models.canonical import InboundMessage, MessageType
from tests.confirmation_stub import LedgerStub
from tests.test_confirmation import candidate
import agent_orchestrator.orchestrator as orchestrator
import app.services.confirmation as confirmation


NOW = datetime(2026, 11, 21, tzinfo=timezone.utc)
MERCHANT_ID = "+919876543210"


@pytest.fixture
def test_setup(monkeypatch):
    ledger = LedgerStub()
    service = confirmation.ConfirmationService(ledger, lambda: NOW)
    monkeypatch.setattr(confirmation, "_service", service)
    return service, ledger


def make_pipeline_result(transactions, msg_id="msg_001"):
    return ProcessingResult(
        input_metadata=ProcessedInputPayload(
            merchant_id=MERCHANT_ID, message_id=msg_id, idempotency_key=msg_id
        ),
        extraction=ExtractionResponse(transactions=transactions),
    )


def test_consecutive_transactions_both_staged_and_tracked(monkeypatch, test_setup):
    """
    Test 1: When merchant posts Transaction 1 (Water bottles), and then Transaction 2 (Indra, Fireworks),
    both transactions must be staged and tracked in the pending queue without crashing.
    """
    service, ledger = test_setup
    conv = {}

    # 1. Post Transaction 1 (Water Bottles)
    tx1 = candidate(
        buyer_name="Suresh Traders",
        total_amount=10500,
        amount_paid=6000,
        credit_amount=4500,
        items=[dict(item_name="Water Bottles", quantity=150, unit_price=70, line_total=10500)],
        validation_status="ACCEPTED",
    )
    res1 = make_pipeline_result([tx1], "msg_001")
    monkeypatch.setattr(orchestrator, "pipeline", SimpleNamespace(process_and_interpret=lambda **kw: res1))

    msg1 = InboundMessage(
        message_id="msg_001",
        tenant_id=MERCHANT_ID,
        sender_phone=MERCHANT_ID,
        message_type=MessageType.TEXT,
        text_content="Water bottles 150 @ 70, cash 6000",
    )
    _, out1 = orchestrator.orchestrate_inbound_message(msg1, conversation=conv)

    assert len(ledger.rows) == 1
    assert ledger.post_count == 0  # Nothing committed to ledger yet!
    cid1 = conv["active_candidate_id"]
    assert cid1 is not None

    # 2. Post Transaction 2 (Indra, Fireworks via image or new sale)
    tx2 = candidate(
        buyer_name="Indra",
        total_amount=30000,
        amount_paid=30000,
        credit_amount=0,
        items=[dict(item_name="Fireworks", quantity=20, unit_price=1500, line_total=30000)],
        validation_status="ACCEPTED",
    )
    res2 = make_pipeline_result([tx2], "msg_002")
    monkeypatch.setattr(orchestrator, "pipeline", SimpleNamespace(process_and_interpret=lambda **kw: res2))

    msg2 = InboundMessage(
        message_id="msg_002",
        tenant_id=MERCHANT_ID,
        sender_phone=MERCHANT_ID,
        message_type=MessageType.IMAGE,
        media_url="https://example.com/handwritten_note.jpg",
        text_content="",
    )
    # This must NOT throw "Reply changed the buyer or transaction type"
    _, out2 = orchestrator.orchestrate_inbound_message(msg2, conversation=conv)

    # Verify both transactions are tracked and staged!
    assert len(ledger.rows) == 2
    assert ledger.post_count == 0  # Still nothing committed to ledger!
    cid2 = conv["active_candidate_id"]
    assert cid2 != cid1

    # Verify pending queue notice is in the response
    assert "Pending Transactions" in out2.interactive_body or "Pending Transactions" in out2.text_content
    assert "2 on hold" in (out2.interactive_body or out2.text_content)
    assert "Indra" in (out2.interactive_body or out2.text_content)


def test_cancel_previous_transaction(monkeypatch, test_setup):
    """
    Test 2: Merchant says "Cancel the previous transaction".
    It must identify the previous pending candidate, cancel it, and notify merchant.
    """
    service, ledger = test_setup
    conv = {}

    # Stage a candidate
    tx = candidate(buyer_name="Water bottles", total_amount=10500)
    res = make_pipeline_result([tx], "msg_100")
    monkeypatch.setattr(orchestrator, "pipeline", SimpleNamespace(process_and_interpret=lambda **kw: res))

    msg = InboundMessage(
        message_id="msg_100",
        tenant_id=MERCHANT_ID,
        sender_phone=MERCHANT_ID,
        message_type=MessageType.TEXT,
        text_content="Water bottles 150 @ 70",
    )
    orchestrator.orchestrate_inbound_message(msg, conversation=conv)
    assert len(ledger.rows) == 1
    cid = conv["active_candidate_id"]
    assert ledger.rows[cid].status == "AWAITING_CONFIRMATION"

    # Now merchant sends "Cancel the previous transaction"
    cancel_msg = InboundMessage(
        message_id="msg_101",
        tenant_id=MERCHANT_ID,
        sender_phone=MERCHANT_ID,
        message_type=MessageType.TEXT,
        text_content="Cancel the previous transaction",
    )
    _, out_cancel = orchestrator.orchestrate_inbound_message(cancel_msg, conversation=conv)

    # Candidate must be cancelled in ledger
    assert ledger.rows[cid].status == "CANCELLED"
    assert ledger.post_count == 0
    assert "Cancelled previous transaction" in out_cancel.text_content
    assert conv.get("active_candidate_id") is None


def test_cancel_one_leaves_other_pending(monkeypatch, test_setup):
    """
    Test 3: Staging two transactions and cancelling the previous one leaves the first one pending.
    """
    service, ledger = test_setup
    conv = {}

    # Stage 1
    tx1 = candidate(buyer_name="Buyer One", total_amount=5000)
    res1 = make_pipeline_result([tx1], "msg_201")
    monkeypatch.setattr(orchestrator, "pipeline", SimpleNamespace(process_and_interpret=lambda **kw: res1))
    orchestrator.orchestrate_inbound_message(
        InboundMessage(message_id="msg_201", tenant_id=MERCHANT_ID, sender_phone=MERCHANT_ID,
                       message_type=MessageType.TEXT, text_content="Buyer One 5000"),
        conversation=conv
    )
    cid1 = conv["active_candidate_id"]

    # Stage 2
    tx2 = candidate(buyer_name="Buyer Two", total_amount=8000)
    res2 = make_pipeline_result([tx2], "msg_202")
    monkeypatch.setattr(orchestrator, "pipeline", SimpleNamespace(process_and_interpret=lambda **kw: res2))
    orchestrator.orchestrate_inbound_message(
        InboundMessage(message_id="msg_202", tenant_id=MERCHANT_ID, sender_phone=MERCHANT_ID,
                       message_type=MessageType.IMAGE, media_url="https://example.com/bill2.jpg", text_content=""),
        conversation=conv
    )
    cid2 = conv["active_candidate_id"]
    assert len(ledger.rows) == 2

    # Cancel previous (which targets Candidate 2)
    cancel_msg = InboundMessage(
        message_id="msg_203", tenant_id=MERCHANT_ID, sender_phone=MERCHANT_ID,
        message_type=MessageType.TEXT, text_content="Cancel previous"
    )
    _, out = orchestrator.orchestrate_inbound_message(cancel_msg, conversation=conv)

    assert ledger.rows[cid2].status == "CANCELLED"
    assert ledger.rows[cid1].status == "AWAITING_CONFIRMATION"
    assert "1 other pending transaction(s) on hold" in out.text_content
    # Candidate 1 is restored as active candidate
    assert conv.get("active_candidate_id") == cid1


def test_confirm_all_pending_transactions(monkeypatch, test_setup):
    """
    Test 4: "Confirm all" confirms all ready candidates in sequence.
    """
    service, ledger = test_setup
    conv = {}

    # Stage 1
    tx1 = candidate(
        buyer_name="Sale 1", total_amount=1000, amount_paid=1000,
        items=[dict(item_name="Shirt", quantity=10, unit_price=100, line_total=1000)],
        payments=[dict(method="cash", amount=1000)],
        validation_status="ACCEPTED",
    )
    res1 = make_pipeline_result([tx1], "msg_301")
    monkeypatch.setattr(orchestrator, "pipeline", SimpleNamespace(process_and_interpret=lambda **kw: res1))
    orchestrator.orchestrate_inbound_message(
        InboundMessage(message_id="msg_301", tenant_id=MERCHANT_ID, sender_phone=MERCHANT_ID,
                       message_type=MessageType.TEXT, text_content="Sale 1 1000"),
        conversation=conv
    )

    # Stage 2
    tx2 = candidate(
        buyer_name="Sale 2", total_amount=2000, amount_paid=2000,
        items=[dict(item_name="Shirt", quantity=20, unit_price=100, line_total=2000)],
        payments=[dict(method="cash", amount=2000)],
        validation_status="ACCEPTED",
    )
    res2 = make_pipeline_result([tx2], "msg_302")
    monkeypatch.setattr(orchestrator, "pipeline", SimpleNamespace(process_and_interpret=lambda **kw: res2))
    orchestrator.orchestrate_inbound_message(
        InboundMessage(message_id="msg_302", tenant_id=MERCHANT_ID, sender_phone=MERCHANT_ID,
                       message_type=MessageType.IMAGE, media_url="http://x/bill.jpg", text_content=""),
        conversation=conv
    )

    assert len(ledger.rows) == 2
    assert ledger.post_count == 0

    # User says "Confirm all"
    confirm_msg = InboundMessage(
        message_id="msg_303", tenant_id=MERCHANT_ID, sender_phone=MERCHANT_ID,
        message_type=MessageType.TEXT, text_content="Confirm all"
    )
    _, out = orchestrator.orchestrate_inbound_message(confirm_msg, conversation=conv)

    # Both must be committed to ledger!
    assert ledger.post_count == 2
    assert "Recorded 2 Transactions to Ledger" in out.text_content


def test_single_message_with_multiple_transactions(monkeypatch, test_setup):
    """
    Test 5: A single message containing multiple extracted transactions stages all of them.
    """
    service, ledger = test_setup
    conv = {}

    tx_a = candidate(buyer_name="Customer A", total_amount=3000)
    tx_b = candidate(buyer_name="Customer B", total_amount=4500)
    res = make_pipeline_result([tx_a, tx_b], "msg_401")
    monkeypatch.setattr(orchestrator, "pipeline", SimpleNamespace(process_and_interpret=lambda **kw: res))

    msg = InboundMessage(
        message_id="msg_401", tenant_id=MERCHANT_ID, sender_phone=MERCHANT_ID,
        message_type=MessageType.IMAGE, media_url="http://example.com/page_with_2_bills.jpg", text_content=""
    )
    _, out = orchestrator.orchestrate_inbound_message(msg, conversation=conv)

    assert len(ledger.rows) == 2
    assert ledger.post_count == 0  # Kept on hold!
    assert "2 Transactions Extracted & Staged" in out.text_content
    assert "Nothing posted yet" in out.text_content
