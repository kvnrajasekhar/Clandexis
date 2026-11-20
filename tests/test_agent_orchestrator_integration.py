import pytest
from models.canonical import InboundMessage, MessageType, ChannelType
from agent_orchestrator import orchestrate_inbound_message

def test_orchestrator_repayment():
    msg = InboundMessage(
        message_id="test_orch_001",
        tenant_id="+919876543210",
        sender_phone="+919876543210",
        message_type=MessageType.TEXT,
        text_content="Ravi gave 2000 rupees today for his previous pending balance.",
    )
    result, outbound = orchestrate_inbound_message(msg)
    
    assert len(result.extraction.transactions) > 0
    tx = result.extraction.transactions[0]
    assert tx.transaction_type == "payment_received"
    assert tx.buyer_name == "Ravi"
    assert tx.amount_paid == 2000.0
    assert outbound.recipient_phone == "+919876543210"
    assert "Recorded" in outbound.text_content or "Clarification" in outbound.text_content

def test_orchestrator_unstated_payment_triggers_buttons():
    """Unstated payment triggers 1-tap clarification buttons on WhatsApp"""
    msg = InboundMessage(
        message_id="test_orch_002",
        tenant_id="+919876543210",
        sender_phone="+919876543210",
        message_type=MessageType.TEXT,
        text_content="Sold 10 shirts to Raju for 5000 rupees.",
    )
    result, outbound = orchestrate_inbound_message(msg)
    
    tx = result.extraction.transactions[0]
    assert tx.needs_clarification is True
    # Dispatches interactive quick-reply buttons
    assert outbound.buttons is not None
    assert len(outbound.buttons) == 2
    assert outbound.buttons[0].title == "✅ Confirm (हाँ)"
    assert outbound.buttons[1].title == "❌ Cancel (रद्द)"

def test_orchestrator_inconsistent_payment_rejection():
    """Overpayment triggers transaction rejection"""
    msg = InboundMessage(
        message_id="test_orch_003",
        tenant_id="+919876543210",
        sender_phone="+919876543210",
        message_type=MessageType.TEXT,
        text_content="Sold 5 bags to Suresh for 10000. Suresh paid 7000 cash and 4000 UPI.",
    )
    result, outbound = orchestrate_inbound_message(msg)
    
    tx = result.extraction.transactions[0]
    assert tx.validation_status == "REJECTED"
    assert "❌ *Transaction Rejected*" in outbound.text_content
