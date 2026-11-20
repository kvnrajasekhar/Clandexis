"""
Targeted test for non-hardcoded multimodal transaction processing.
Ensures that arbitrary items (e.g. "Sold 3 cycles for 25000"):
1. Never fall back to hardcoded Cotton Shirts ('shirt-cotton-l').
2. Trigger the Safety Gate with 1-tap confirmation buttons due to uncataloged SKU and unconfirmed payment.
3. Only commit to the ledger upon merchant confirmation.
"""

import asyncio
import pytest

from agent_orchestrator.orchestrator import AgentOrchestrator
from models.canonical import ChannelType, InboundMessage, MessageType
from models.firestore_schema import TransactionStatus


def test_cycles_does_not_substitute_cotton_shirt():
    async def _run():
        orchestrator = AgentOrchestrator()
        tenant_id = "+919876543210"

        # Initial cotton shirt stock
        initial_shirt_stock = orchestrator.inventory[tenant_id]["shirt-cotton-l"].current_stock

        inbound = InboundMessage(
            message_id="msg_cycles_01",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            sender_name="Rajesh Kumar",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Sold 3 cycles for 25000",
        )

        outbounds = await orchestrator.handle_inbound_message(inbound)
        assert len(outbounds) == 1
        out = outbounds[0]

        # 1. Must NOT auto-commit
        assert out.text_content is None or "Transaction Recorded" not in out.text_content

        # 2. Must trigger Safety Gate with 1-tap confirmation buttons
        assert out.message_type == MessageType.INTERACTIVE_BUTTON
        assert out.buttons is not None
        assert len(out.buttons) == 2
        assert any(b.id.startswith("CONFIRM_TXN_") or b.id.startswith("clx:v1:confirm:") for b in out.buttons)

        # 3. Must NOT contain Cotton Shirt
        assert "Cotton Shirt" not in out.interactive_body
        assert "shirt-cotton-l" not in out.interactive_body
        assert "Cycles" in out.interactive_body or "cycles" in out.interactive_body.lower()

        # 4. Cotton shirt stock must NOT be altered
        assert orchestrator.inventory[tenant_id]["shirt-cotton-l"].current_stock == initial_shirt_stock

        # 5. Simulate merchant confirming
        confirm_btn = out.buttons[0]
        confirm_inbound = InboundMessage(
            message_id="msg_cycles_confirm",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            sender_name="Rajesh Kumar",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.INTERACTIVE_BUTTON,
            interactive_id=confirm_btn.id,
            interactive_title=confirm_btn.title,
        )

        commit_outbounds = await orchestrator.handle_inbound_message(confirm_inbound)
        assert len(commit_outbounds) == 1
        commit_out = commit_outbounds[0]

        assert "Transaction Committed" in commit_out.text_content
        assert "25,000" in commit_out.text_content

        # 6. Cotton shirt stock remains unaffected
        assert orchestrator.inventory[tenant_id]["shirt-cotton-l"].current_stock == initial_shirt_stock

        # 7. Cycles SKU was dynamically created in inventory
        assert "cycles" in orchestrator.inventory[tenant_id]

    asyncio.run(_run())
