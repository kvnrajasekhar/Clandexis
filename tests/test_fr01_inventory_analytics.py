"""
Targeted tests for FR-01: Daily Inventory Logging & Analytics over WhatsApp.
Tests end-to-end catalog setup, fuzzy SKU matching, production logs, stock wastage/adjustments,
conversational analytics, and the strict read/write boundary (FR-01.5).
"""

import asyncio
import copy
import pytest
from datetime import datetime, timezone

from tests.legacy_in_memory_orchestrator import AgentOrchestrator
from models.canonical import ChannelType, InboundMessage, MessageType, OutboundMessage
from models.firestore_schema import MovementType, TransactionIntent, TransactionStatus


def test_whatsapp_catalog_setup_new_sku():
    """
    FR-01: Merchant registers a new product SKU via conversational WhatsApp message.
    E.g., "Naya item add karo Silk Saree 50 pieces @ 1200 reorder 10"
    """
    async def _run():
        orchestrator = AgentOrchestrator()
        tenant_id = "+919876543210"

        inbound = InboundMessage(
            message_id="msg_setup_001",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            sender_name="Rajesh Kumar",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Naya item add karo Silk Saree 50 pieces @ 1200 reorder 10",
        )

        outbounds = await orchestrator.handle_inbound_message(inbound)
        assert len(outbounds) == 1
        out = outbounds[0]

        assert "Transaction Recorded" in out.text_content
        assert "Product Catalog Updated" in out.text_content
        assert "Silk Saree" in out.text_content

        # Verify item registered in merchant inventory
        inv = orchestrator.inventory[tenant_id]
        assert "silk-saree" in inv
        item = inv["silk-saree"]
        assert item.item_name == "Silk Saree"
        assert item.current_stock == 50.0
        assert item.default_unit_price == 1200.0

        # Verify initial stock movement logged
        movements = [m for m in orchestrator.movements[tenant_id] if m.sku_id == "silk-saree"]
        assert len(movements) >= 1
        assert movements[0].movement_type == MovementType.PRODUCTION_ADD
        assert movements[0].quantity == 50.0

    asyncio.run(_run())


def test_fuzzy_sku_resolution_multilingual():
    """
    FR-01.2: Fuzzy matching vernacular queries (Hindi/Hinglish/English) to catalog SKUs.
    """
    orchestrator = AgentOrchestrator()
    tenant_id = "+919876543210"

    # Exact alias
    sku, item, conf = orchestrator.inventory_service.resolve_sku(tenant_id, "sooti shirt")
    assert sku == "shirt-cotton-l"
    assert conf >= 0.90

    # Substring / variant
    sku, item, conf = orchestrator.inventory_service.resolve_sku(tenant_id, "50 cotton shirts")
    assert sku == "shirt-cotton-l"
    assert conf >= 0.85

    # Kurta resolution
    sku, item, conf = orchestrator.inventory_service.resolve_sku(tenant_id, "khadi kurta")
    assert sku == "kurta-linen-m"
    assert conf >= 0.90

    # Non-existent item
    sku, item, conf = orchestrator.inventory_service.resolve_sku(tenant_id, "leather jacket")
    assert sku is None or conf < 0.70


def test_production_logging_and_stock_increment():
    """
    FR-01.3: Merchant logs daily production via voice/text note.
    Increments inventory balance and logs append-only movement.
    """
    async def _run():
        orchestrator = AgentOrchestrator()
        tenant_id = "+919876543210"

        # Baseline stock is 300
        initial_stock = orchestrator.inventory[tenant_id]["shirt-cotton-l"].current_stock
        assert initial_stock == 300.0

        inbound = InboundMessage(
            message_id="msg_prod_100",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            sender_name="Rajesh Kumar",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Aaj 100 cotton shirts banayi workshop me batch B14",
        )

        outbounds = await orchestrator.handle_inbound_message(inbound)
        assert len(outbounds) == 1
        out = outbounds[0]

        assert "Transaction Recorded" in out.text_content
        assert "Daily Production Logged" in out.text_content
        assert "+100" in out.text_content

        # Verify stock incremented to 400
        updated_stock = orchestrator.inventory[tenant_id]["shirt-cotton-l"].current_stock
        assert updated_stock == 400.0

        # Verify audit movement
        last_move = orchestrator.movements[tenant_id][-1]
        assert last_move.movement_type == MovementType.PRODUCTION_ADD
        assert last_move.quantity == 100.0
        assert last_move.balance_after == 400.0

    asyncio.run(_run())


def test_unknown_sku_in_production_triggers_safety_gate():
    """
    FR-01.2 & FR-04.4: When production mentions an unknown/uncataloged item,
    the system must NOT silently invent or commit without confirmation.
    It triggers the Safety Gate with 1-tap confirmation buttons.
    """
    async def _run():
        orchestrator = AgentOrchestrator()
        tenant_id = "+919876543210"

        inbound = InboundMessage(
            message_id="msg_prod_unknown",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            sender_name="Rajesh Kumar",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Aaj 40 denim jackets banayi workshop me",
        )

        outbounds = await orchestrator.handle_inbound_message(inbound)
        assert len(outbounds) == 1
        out = outbounds[0]

        # Must trigger interactive buttons for merchant confirmation
        assert out.message_type == MessageType.INTERACTIVE_BUTTON
        assert out.buttons is not None
        assert len(out.buttons) == 2
        assert out.buttons[0].id.startswith("CONFIRM_TXN_")

        # Simulate merchant confirming the new SKU production
        confirm_btn_id = out.buttons[0].id
        inbound_click = InboundMessage(
            message_id="msg_confirm_denim",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            sender_name="Rajesh Kumar",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.INTERACTIVE_BUTTON,
            interactive_id=confirm_btn_id,
            interactive_title="✅ Confirm (हाँ)",
        )

        commit_outbounds = await orchestrator.handle_inbound_message(inbound_click)
        assert len(commit_outbounds) == 1
        assert "Transaction Committed" in commit_outbounds[0].text_content

        # Verify new SKU was registered and stock incremented
        assert "denim-jackets" in orchestrator.inventory[tenant_id]
        item = orchestrator.inventory[tenant_id]["denim-jackets"]
        assert item.current_stock == 40.0

    asyncio.run(_run())


def test_stock_damage_wastage_adjustment():
    """
    FR-01.3: Merchant records damaged stock / wastage (e.g., "10 cotton shirts kharab ho gayi").
    Deducts stock and records damage movement.
    """
    async def _run():
        orchestrator = AgentOrchestrator()
        tenant_id = "+919876543210"

        initial_stock = orchestrator.inventory[tenant_id]["shirt-cotton-l"].current_stock
        assert initial_stock == 300.0

        inbound = InboundMessage(
            message_id="msg_damage_001",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            sender_name="Rajesh Kumar",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="10 cotton shirts kharab ho gayi wastage likh lo",
        )

        outbounds = await orchestrator.handle_inbound_message(inbound)
        assert len(outbounds) == 1
        out = outbounds[0]

        assert "Transaction Recorded" in out.text_content
        assert "Stock Adjustment Recorded" in out.text_content

        # Verify stock decreased by 10 (300 -> 290)
        updated_stock = orchestrator.inventory[tenant_id]["shirt-cotton-l"].current_stock
        assert updated_stock == 290.0

        # Verify movement
        last_move = orchestrator.movements[tenant_id][-1]
        assert last_move.movement_type == MovementType.DAMAGE_WASTAGE
        assert last_move.quantity == -10.0
        assert last_move.balance_after == 290.0

    asyncio.run(_run())


def test_conversational_low_stock_alert():
    """
    FR-01.4: Merchant queries low stock or reorder warnings.
    "Kaun sa maal khatam hone wala hai?"
    """
    async def _run():
        orchestrator = AgentOrchestrator()
        tenant_id = "+919876543210"

        # Kurta is at stock=150, reorder=30 (safe)
        # Force Kurta stock to 15 (below reorder level of 30)
        orchestrator.inventory[tenant_id]["kurta-linen-m"].current_stock = 15.0

        inbound = InboundMessage(
            message_id="msg_low_stock_query",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            sender_name="Rajesh Kumar",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Kaun sa maal khatam hone wala hai? Low stock alert do",
        )

        outbounds = await orchestrator.handle_inbound_message(inbound)
        assert len(outbounds) == 1
        out = outbounds[0]

        assert "Low Stock Alert" in out.text_content
        assert "Linen Kurta (M)" in out.text_content
        assert "15 pieces remaining" in out.text_content

    asyncio.run(_run())


def test_conversational_production_vs_sales_analytics():
    """
    FR-01.4: Merchant asks: "Aaj kitna maal bana aur kitna bika?"
    Returns comparative analytics.
    """
    async def _run():
        orchestrator = AgentOrchestrator()
        tenant_id = "+919876543210"

        # Log production of 100 shirts
        inbound_prod = InboundMessage(
            message_id="msg_prod_test",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            sender_name="Rajesh Kumar",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Aaj 100 cotton shirts banayi workshop me",
        )
        await orchestrator.handle_inbound_message(inbound_prod)

        # Log sale of 20 shirts
        inbound_sale = InboundMessage(
            message_id="msg_sale_test",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            sender_name="Rajesh Kumar",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Ramesh ko 20 cotton shirt bechi ₹4000 cash me",
        )
        await orchestrator.handle_inbound_message(inbound_sale)

        # Query comparison
        inbound_query = InboundMessage(
            message_id="msg_compare_query",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            sender_name="Rajesh Kumar",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Aaj kitna bana aur kitna bika? Production vs sales",
        )

        outbounds = await orchestrator.handle_inbound_message(inbound_query)
        assert len(outbounds) == 1
        out = outbounds[0]

        assert "Production vs. Sales" in out.text_content
        assert "Made: +100 pieces" in out.text_content
        assert "Sold: -20 pieces" in out.text_content
        assert "+100 units" in out.text_content
        assert "-20 units" in out.text_content

    asyncio.run(_run())


def test_strict_readonly_boundary_guarantee():
    """
    FR-01.5: Read-only analytics queries must NEVER mutate the database or ledger state.
    """
    async def _run():
        orchestrator = AgentOrchestrator()
        tenant_id = "+919876543210"

        # Take deep snapshots of state
        inv_before = copy.deepcopy(orchestrator.inventory)
        movements_before = copy.deepcopy(orchestrator.movements)
        parties_before = copy.deepcopy(orchestrator.parties)
        txns_before = copy.deepcopy(orchestrator.transactions)

        queries = [
            "Stock kitna hai?",
            "Kaun sa maal khatam hone wala hai?",
            "Aaj kitna maal bana?",
            "Aaj kitna bana aur kitna bika?",
            "Cotton shirt ki movement history dikhao",
            "Ramesh ka udhaar kitna hai?",
            "Aaj ki bikri ka summary do",
        ]

        for q in queries:
            msg = InboundMessage(
                message_id=f"msg_readonly_{hash(q)}",
                tenant_id=tenant_id,
                sender_phone=tenant_id,
                sender_name="Rajesh Kumar",
                channel=ChannelType.META_WHATSAPP,
                message_type=MessageType.TEXT,
                text_content=q,
            )
            outbounds = await orchestrator.handle_inbound_message(msg)
            assert len(outbounds) == 1
            assert outbounds[0].message_type == MessageType.TEXT

        # Assert zero mutations occurred
        assert orchestrator.inventory == inv_before
        assert orchestrator.movements == movements_before
        assert orchestrator.parties == parties_before
        assert orchestrator.transactions == txns_before

    asyncio.run(_run())
