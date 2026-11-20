"""
End-to-end integration tests for WhatsApp Inbound message handling,
application processing via Agent Orchestrator, and Outbound dispatching.
"""

import asyncio
import pytest
from datetime import datetime, timezone

from agent_orchestrator import AgentOrchestrator
from models.canonical import CanonicalButton, ChannelType, InboundMessage, MessageType, OutboundMessage
from models.firestore_schema import TransactionStatus
from webhook_service.main import process_inbound_message
from webhook_service.meta_whatsapp_handler import MetaWhatsAppService


def test_inbound_query_stock_to_outbound_response():
    """
    Test merchant sending a stock query text, processed by orchestrator,
    returning an OutboundMessage with inventory stock status.
    """
    async def _run():
        orchestrator = AgentOrchestrator()

        inbound = InboundMessage(
            message_id="msg_stock_001",
            tenant_id="+919876543210",
            sender_phone="+919876543210",
            sender_name="Rajesh Kumar",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Stock kitna bacha hai?",
        )

        outbounds = await orchestrator.handle_inbound_message(inbound)
        assert len(outbounds) == 1
        outbound = outbounds[0]

        assert outbound.recipient_phone == "+919876543210"
        assert outbound.channel == ChannelType.META_WHATSAPP
        assert outbound.message_type == MessageType.TEXT
        assert "Stock Status" in outbound.text_content
        assert "Cotton Shirt (L)" in outbound.text_content

    asyncio.run(_run())


def test_inbound_query_udhaar_to_outbound_response():
    """
    Test merchant asking for a party's udhaar balance, returning party details.
    """
    async def _run():
        orchestrator = AgentOrchestrator()

        inbound = InboundMessage(
            message_id="msg_udhaar_001",
            tenant_id="+919876543210",
            sender_phone="+919876543210",
            sender_name="Rajesh Kumar",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Ramesh ka udhaar kitna hai?",
        )

        outbounds = await orchestrator.handle_inbound_message(inbound)
        assert len(outbounds) == 1
        outbound = outbounds[0]

        assert outbound.recipient_phone == "+919876543210"
        assert "Udhaar Ledger" in outbound.text_content
        assert "Ramesh Textiles" in outbound.text_content
        assert "2,500.00" in outbound.text_content

    asyncio.run(_run())


def test_inbound_sale_low_credit_auto_commits():
    """
    Test merchant recording a sale under the high credit threshold (e.g. ₹1,500 udhaar).
    Invariants pass, safety gate does not trigger -> Auto-commits to ledger.
    """
    async def _run():
        orchestrator = AgentOrchestrator()

        inbound = InboundMessage(
            message_id="msg_sale_001",
            tenant_id="+919876543210",
            sender_phone="+919876543210",
            sender_name="Rajesh Kumar",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Ramesh ko 10 cotton shirt bechi ₹2000 ki, ₹500 cash diya 1500 udhaar",
        )

        outbounds = await orchestrator.handle_inbound_message(inbound)
        assert len(outbounds) == 1
        outbound = outbounds[0]

        assert outbound.message_type == MessageType.TEXT
        assert "Transaction Recorded" in outbound.text_content
        assert "Txn ID" in outbound.text_content
        assert "2,000.00" in outbound.text_content

        # Verify inventory was decremented
        inv_item = orchestrator.inventory["+919876543210"]["shirt-cotton-l"]
        assert inv_item.current_stock == 290.0  # 300 - 10

        # Verify party udhaar increased
        party = orchestrator.parties["+919876543210"]["ramesh-textiles"]
        assert party.current_udhaar_balance == 4000.0  # 2500 + 1500

    asyncio.run(_run())


def test_inbound_sale_high_credit_triggers_safety_gate_buttons():
    """
    Test high udhaar sale (> ₹5,000). Must trigger Human-in-the-Loop Safety Gate
    and return interactive confirmation buttons.
    """
    async def _run():
        orchestrator = AgentOrchestrator()

        # Sale of ₹12,000 with ₹8,000 udhaar (exceeds ₹5,000 threshold)
        inbound = InboundMessage(
            message_id="msg_high_credit_001",
            tenant_id="+919876543210",
            sender_phone="+919876543210",
            sender_name="Rajesh Kumar",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Ramesh ko 60 cotton shirts bechi ₹12000 ki, 4000 cash diya 8000 udhaar",
        )

        outbounds = await orchestrator.handle_inbound_message(inbound)
        assert len(outbounds) == 1
        outbound = outbounds[0]

        # Verify Outbound is interactive buttons
        assert outbound.message_type == MessageType.INTERACTIVE_BUTTON
        assert outbound.buttons is not None
        assert len(outbound.buttons) == 2
        assert "Confirm" in outbound.buttons[0].title
        assert "Cancel" in outbound.buttons[1].title
        assert outbound.buttons[0].id.startswith("CONFIRM_TXN_")

        txn_id = outbound.buttons[0].id.replace("CONFIRM_TXN_", "")
        staged_txn = orchestrator.transactions[txn_id]
        assert staged_txn.status == TransactionStatus.AWAITING_CONFIRMATION
        assert staged_txn.safety_gate_triggered is True

        # Stock should NOT be mutated yet while awaiting confirmation
        inv_item = orchestrator.inventory["+919876543210"]["shirt-cotton-l"]
        assert inv_item.current_stock == 300.0

    asyncio.run(_run())


def test_interactive_button_confirm_and_cancel_flow():
    """
    Test clicking the interactive 1-tap confirmation button, which commits
    the staged transaction and returns final ledger confirmation.
    """
    async def _run():
        orchestrator = AgentOrchestrator()

        # 1. Trigger safety gate
        inbound_sale = InboundMessage(
            message_id="msg_gate_test",
            tenant_id="+919876543210",
            sender_phone="+919876543210",
            sender_name="Rajesh Kumar",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Ramesh ko 50 cotton shirts bechi ₹10000 ki, 2000 cash 8000 udhaar",
        )
        gate_outbounds = await orchestrator.handle_inbound_message(inbound_sale)
        confirm_btn_id = gate_outbounds[0].buttons[0].id

        # 2. Simulate merchant clicking [✅ Confirm (हाँ)] button
        inbound_click = InboundMessage(
            message_id="msg_click_confirm",
            tenant_id="+919876543210",
            sender_phone="+919876543210",
            sender_name="Rajesh Kumar",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.INTERACTIVE_BUTTON,
            interactive_id=confirm_btn_id,
            interactive_title="✅ Confirm (हाँ)",
        )

        commit_outbounds = await orchestrator.handle_inbound_message(inbound_click)
        assert len(commit_outbounds) == 1
        commit_out = commit_outbounds[0]

        assert commit_out.message_type == MessageType.TEXT
        assert "Transaction Committed" in commit_out.text_content
        assert "Ramesh Textiles" in commit_out.text_content

        # Verify inventory was decremented upon approval
        inv_item = orchestrator.inventory["+919876543210"]["shirt-cotton-l"]
        assert inv_item.current_stock == 250.0  # 300 - 50

    asyncio.run(_run())


def test_webhook_pipeline_process_inbound_message_end_to_end():
    """
    Test the full process_inbound_message pipeline from webhook service
    down to outbound dispatch simulation.
    """
    async def _run():
        inbound = InboundMessage(
            message_id="wamid.pipeline_test_123",
            tenant_id="+919876543210",
            sender_phone="+919876543210",
            sender_name="Rajesh Kumar",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Stock kitna hai?",
        )

        # Process through pipeline
        outbound_messages = await process_inbound_message(inbound)

        assert len(outbound_messages) >= 1
        assert outbound_messages[0].recipient_phone == "+919876543210"
        assert "Stock Status" in outbound_messages[0].text_content

    asyncio.run(_run())


def test_interactive_button_cancel_flow():
    """
    Test clicking the interactive 1-tap [❌ Cancel] button:
    Staged transaction is rejected and no stock is deducted.
    """
    async def _run():
        orchestrator = AgentOrchestrator()

        inbound_sale = InboundMessage(
            message_id="msg_gate_cancel",
            tenant_id="+919876543210",
            sender_phone="+919876543210",
            sender_name="Rajesh Kumar",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Ramesh ko 50 cotton shirts bechi ₹10000 ki, 2000 cash 8000 udhaar",
        )
        gate_outbounds = await orchestrator.handle_inbound_message(inbound_sale)
        cancel_btn_id = gate_outbounds[0].buttons[1].id
        assert cancel_btn_id.startswith("CANCEL_TXN_")

        # Click Cancel
        inbound_cancel = InboundMessage(
            message_id="msg_click_cancel",
            tenant_id="+919876543210",
            sender_phone="+919876543210",
            sender_name="Rajesh Kumar",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.INTERACTIVE_BUTTON,
            interactive_id=cancel_btn_id,
            interactive_title="❌ Cancel (रद्द)",
        )

        cancel_outbounds = await orchestrator.handle_inbound_message(inbound_cancel)
        assert len(cancel_outbounds) == 1
        assert "Cancelled" in cancel_outbounds[0].text_content or "रद्द" in cancel_outbounds[0].text_content

        # Stock must remain unchanged
        inv_item = orchestrator.inventory["+919876543210"]["shirt-cotton-l"]
        assert inv_item.current_stock == 300.0

        txn_id = cancel_btn_id.replace("CANCEL_TXN_", "")
        assert orchestrator.transactions[txn_id].status == TransactionStatus.REJECTED

    asyncio.run(_run())


def test_inbound_production_log_increments_inventory():
    """
    Test recording internal workshop production ("Aaj 50 cotton shirts banayi").
    Increments inventory atomically.
    """
    async def _run():
        orchestrator = AgentOrchestrator()

        inbound = InboundMessage(
            message_id="msg_prod_001",
            tenant_id="+919876543210",
            sender_phone="+919876543210",
            sender_name="Rajesh Kumar",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Aaj 50 cotton shirts banayi hain workshop me",
        )

        outbounds = await orchestrator.handle_inbound_message(inbound)
        assert len(outbounds) == 1
        assert "Transaction Recorded" in outbounds[0].text_content

        # Verify stock incremented by 50 (300 -> 350)
        inv_item = orchestrator.inventory["+919876543210"]["shirt-cotton-l"]
        assert inv_item.current_stock == 350.0

    asyncio.run(_run())


def test_fastapi_webhook_post_endpoint_integration():
    """
    Test real HTTP POST /webhook invocation through FastAPI TestClient.
    Verifies HMAC validation, fast HTTP 200 response, and background execution.
    """
    import hashlib
    import hmac
    import json
    from fastapi.testclient import TestClient
    from config import settings
    from webhook_service.main import app

    client = TestClient(app)

    payload_dict = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_ID_12345",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550269999",
                                "phone_number_id": "1378627142011047",
                            },
                            "contacts": [
                                {
                                    "profile": {"name": "Rajesh Kumar"},
                                    "wa_id": "919876543210",
                                }
                            ],
                            "messages": [
                                {
                                    "from": "919876543210",
                                    "id": "wamid.HTTP_TEST_999",
                                    "timestamp": "1732080000",
                                    "type": "text",
                                    "text": {"body": "Stock kitna hai?"},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }

    raw_body = json.dumps(payload_dict).encode("utf-8")
    app_secret = settings.META_APP_SECRET or ""
    digest = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    signature_header = f"sha256={digest}"

    response = client.post(
        "/webhook",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": signature_header,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["messages_received"] == 1

