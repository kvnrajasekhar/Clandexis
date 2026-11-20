"""
Test Suite for MSME Setup & Profile Configuration and Customer/Party Onboarding (Clandexis Invisible ERP).
Verifies:
- Conversational MSME setup, UPI configuration, and profile queries.
- Conversational MSME field updates (UPI, language, credit threshold).
- Explicit customer onboarding with credit limits and opening udhaar balances.
- Multilingual phonetic fuzzy matching across aliases.
- Transaction-driven uncataloged customer safety gating and 1-tap onboarding.
- Read-only Khata statements and all-customer receivables lists.
- Multi-tenant boundary isolation.
- Strict read-only query invariant (zero state mutation).
"""

import asyncio
from datetime import datetime, timezone
import pytest

from agent_orchestrator.orchestrator import AgentOrchestrator
from models.canonical import CanonicalButton, ChannelType, InboundMessage, MessageType, OutboundMessage
from models.firestore_schema import PaymentMethod, TransactionStatus


def test_merchant_setup_and_profile_retrieval():
    """
    Test registering an MSME merchant with business details and retrieving the profile via WhatsApp.
    """
    async def _run():
        orchestrator = AgentOrchestrator()
        tenant_id = "+919988776655"

        setup_msg = InboundMessage(
            message_id="msg_setup_01",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            sender_name="Mohan Lal",
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Setup business Mohan Handlooms, owner Mohan Lal, UPI mohan@upi, gstin 36AAAAA0000A1Z5",
        )

        outbounds = await orchestrator.handle_inbound_message(setup_msg)
        assert len(outbounds) == 1
        resp = outbounds[0]
        assert "Mohan Handlooms" in resp.text_content
        assert "Mohan Lal" in resp.text_content
        assert "mohan@upi" in resp.text_content
        assert "36AAAAA0000A1Z5" in resp.text_content

        merchant = orchestrator.merchants[tenant_id]
        assert merchant.business_name == "Mohan Handlooms"
        assert merchant.owner_name == "Mohan Lal"
        assert merchant.upi_vpa == "mohan@upi"
        assert merchant.gstin == "36AAAAA0000A1Z5"

        # Read-only query for business profile
        profile_query = InboundMessage(
            message_id="msg_prof_01",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Mera business profile dikhao",
        )
        p_outbounds = await orchestrator.handle_inbound_message(profile_query)
        assert len(p_outbounds) == 1
        p_text = p_outbounds[0].text_content
        assert "MSME Business Profile" in p_text
        assert "Mohan Handlooms" in p_text
        assert "mohan@upi" in p_text
        assert "Registered Parties/Customers" in p_text

    asyncio.run(_run())


def test_merchant_field_update_conversational():
    """
    Test updating individual MSME settings (UPI, language, credit safety limit) via WhatsApp.
    """
    async def _run():
        orchestrator = AgentOrchestrator()
        tenant_id = "+919876543210"

        # 1. Update UPI ID
        msg1 = InboundMessage(
            message_id="msg_upd_upi",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Update UPI to balaji@icici",
        )
        out1 = await orchestrator.handle_inbound_message(msg1)
        assert "Setting Updated" in out1[0].text_content
        assert "balaji@icici" in out1[0].text_content
        assert orchestrator.merchants[tenant_id].upi_vpa == "balaji@icici"

        # 2. Update Language to Telugu
        msg2 = InboundMessage(
            message_id="msg_upd_lang",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Set language to Telugu",
        )
        out2 = await orchestrator.handle_inbound_message(msg2)
        assert "Setting Updated" in out2[0].text_content
        assert orchestrator.merchants[tenant_id].preferred_lang == "te"

        # 3. Update Credit Threshold
        msg3 = InboundMessage(
            message_id="msg_upd_thresh",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Credit threshold 12000",
        )
        out3 = await orchestrator.handle_inbound_message(msg3)
        assert "Setting Updated" in out3[0].text_content
        assert orchestrator.merchants[tenant_id].high_credit_threshold == 12000.0

    asyncio.run(_run())


def test_customer_onboarding_explicit_command():
    """
    Test explicit customer onboarding via conversational command.
    """
    async def _run():
        orchestrator = AgentOrchestrator()
        tenant_id = "+919876543210"

        onboard_msg = InboundMessage(
            message_id="msg_cust_01",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Naya customer add karo: Gupta Stores, phone 9811223344, credit limit 25000, opening balance 3000 udhaar",
        )

        outbounds = await orchestrator.handle_inbound_message(onboard_msg)
        assert len(outbounds) == 1
        resp = outbounds[0].text_content

        assert "Customer Onboarded" in resp or "खोला गया" in resp
        assert "Gupta Stores" in resp
        assert "gupta-stores" in resp
        assert "25,000.00" in resp
        assert "3,000.00" in resp

        # Verify party store state
        parties = orchestrator.parties[tenant_id]
        assert "gupta-stores" in parties
        gupta = parties["gupta-stores"]
        assert gupta.display_name == "Gupta Stores"
        assert gupta.phone == "9811223344"
        assert gupta.credit_limit == 25000.0
        assert gupta.current_udhaar_balance == 3000.0
        assert "gupta" in gupta.aliases

    asyncio.run(_run())


def test_party_fuzzy_resolution_multilingual():
    """
    Test multilingual phonetic fuzzy matching across aliases and honorifics.
    """
    orchestrator = AgentOrchestrator()
    tenant_id = "+919876543210"

    # Add custom customer
    orchestrator.party_service.create_or_update_party(
        merchant_id=tenant_id,
        display_name="Sharma Cloth Emporium",
        phone="+919911223344",
        custom_aliases=["sharma", "sharma ji", "sharma cloth"],
    )

    # Test exact name
    p1, conf1 = orchestrator.party_service.resolve_party(tenant_id, "Sharma Cloth Emporium")
    assert p1 is not None
    assert p1.party_id == "sharma-cloth-emporium"
    assert conf1 >= 0.90

    # Test vernacular honorific "ji"
    p2, conf2 = orchestrator.party_service.resolve_party(tenant_id, "Sharma ji ko bill bhejo")
    assert p2 is not None
    assert p2.party_id == "sharma-cloth-emporium"
    assert conf2 >= 0.85

    # Test existing seeded party "Ramesh Bhai"
    p3, conf3 = orchestrator.party_service.resolve_party(tenant_id, "Ramesh bhai ka hisaab")
    assert p3 is not None
    assert p3.party_id == "ramesh-textiles"
    assert conf3 >= 0.85


def test_unknown_customer_in_sale_triggers_safety_gate():
    """
    Test that selling to an uncataloged customer triggers the Human-in-the-Loop Safety Gate.
    """
    async def _run():
        orchestrator = AgentOrchestrator()
        tenant_id = "+919876543210"

        # Anand Stores is NOT in parties
        assert "anand-stores" not in orchestrator.parties[tenant_id]

        sale_msg = InboundMessage(
            message_id="msg_unknown_party_01",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Sold 10 cotton shirts to Anand Stores for ₹2000 udhaar",
        )

        outbounds = await orchestrator.handle_inbound_message(sale_msg)
        assert len(outbounds) == 1
        resp = outbounds[0]

        # Must trigger interactive buttons
        assert resp.message_type == MessageType.INTERACTIVE_BUTTON
        assert "Safety Gate" in resp.interactive_header
        assert "Anand Stores" in resp.interactive_body
        assert "New Customer" in resp.interactive_body or "Notice" in resp.interactive_body
        assert any(b.id.startswith("CONFIRM_NEW_PARTY_TXN_") or b.id.startswith("CONFIRM_TXN_") for b in resp.buttons)

    asyncio.run(_run())


def test_interactive_button_confirms_new_customer_and_commits_sale():
    """
    Test that clicking the 1-tap confirmation button auto-creates the new customer and commits the sale.
    """
    async def _run():
        orchestrator = AgentOrchestrator()
        tenant_id = "+919876543210"
        initial_stock = orchestrator.inventory[tenant_id]["shirt-cotton-l"].current_stock

        # 1. Trigger sale to unknown party
        sale_msg = InboundMessage(
            message_id="msg_unknown_party_02",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Sold 10 cotton shirts to Anand Stores for ₹2000 udhaar",
        )
        outbounds = await orchestrator.handle_inbound_message(sale_msg)
        confirm_btn_id = outbounds[0].buttons[0].id

        # 2. Click confirm button
        btn_msg = InboundMessage(
            message_id="msg_click_01",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.INTERACTIVE_BUTTON,
            interactive_id=confirm_btn_id,
            interactive_title="✅ Confirm (हाँ)",
        )
        confirm_outbounds = await orchestrator.handle_inbound_message(btn_msg)
        assert len(confirm_outbounds) == 1
        commit_resp = confirm_outbounds[0].text_content

        assert "Transaction Committed" in commit_resp
        assert "Anand Stores" in commit_resp

        # Verify party was auto-created
        parties = orchestrator.parties[tenant_id]
        assert "anand-stores" in parties
        anand = parties["anand-stores"]
        assert anand.display_name == "Anand Stores"
        assert anand.current_udhaar_balance == 2000.0

        # Verify stock was deducted
        new_stock = orchestrator.inventory[tenant_id]["shirt-cotton-l"].current_stock
        assert new_stock == initial_stock - 10.0

    asyncio.run(_run())


def test_customer_khata_statement_and_list_queries():
    """
    Test read-only queries for customer statements and full customer lists.
    """
    async def _run():
        orchestrator = AgentOrchestrator()
        tenant_id = "+919876543210"

        # 1. Specific customer statement query
        stmt_msg = InboundMessage(
            message_id="msg_stmt_01",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Ramesh ka khata dikhao",
        )
        stmt_out = await orchestrator.handle_inbound_message(stmt_msg)
        assert len(stmt_out) == 1
        text = stmt_out[0].text_content
        assert "Customer Khata Statement" in text or "Udhaar Ledger" in text
        assert "Ramesh Textiles" in text
        assert "2,500.00" in text
        assert "Credit Limit" in text

        # 2. All customers list query
        list_msg = InboundMessage(
            message_id="msg_list_01",
            tenant_id=tenant_id,
            sender_phone=tenant_id,
            channel=ChannelType.META_WHATSAPP,
            message_type=MessageType.TEXT,
            text_content="Customer list dikhao",
        )
        list_out = await orchestrator.handle_inbound_message(list_msg)
        assert len(list_out) == 1
        list_text = list_out[0].text_content
        assert "Registered Customers" in list_text
        assert "Ramesh Textiles" in list_text
        assert "Total Outstanding Receivables" in list_text

    asyncio.run(_run())


def test_multitenant_customer_isolation():
    """
    Verify strict tenant isolation: Tenant B cannot view or mutate Tenant A's customer Khata.
    """
    async def _run():
        orchestrator = AgentOrchestrator()
        tenant_a = "+919876543210"
        tenant_b = "+919111222333"

        # Create customer in Tenant A
        orchestrator.party_service.create_or_update_party(
            merchant_id=tenant_a,
            display_name="Private Customer A",
            opening_udhaar_balance=10000.0,
        )

        # Query Tenant B for Private Customer A
        stmt_b = orchestrator.party_service.get_party_statement(tenant_b, "Private Customer A")
        assert stmt_b["found"] is False
        assert "Customer Not Found" in stmt_b["formatted_text"]

        # Tenant B customer list must be empty
        list_b = orchestrator.party_service.list_parties(tenant_b)
        assert list_b["total_parties"] == 0
        assert list_b["total_receivables"] == 0.0

    asyncio.run(_run())


def test_strict_readonly_query_invariant():
    """
    Verify FR-01.5 / FRS Invariant: Calling read queries never mutates customer or transaction state.
    """
    orchestrator = AgentOrchestrator()
    tenant_id = "+919876543210"

    initial_party_balances = {p.party_id: p.current_udhaar_balance for p in orchestrator.parties[tenant_id].values()}
    initial_txn_count = len(orchestrator.transactions)

    # Run queries multiple times
    orchestrator.party_service.get_party_statement(tenant_id, "Ramesh")
    orchestrator.party_service.list_parties(tenant_id)
    orchestrator.merchant_service.get_merchant_profile(tenant_id)

    # Assert 0 mutations
    for pid, bal in initial_party_balances.items():
        assert orchestrator.parties[tenant_id][pid].current_udhaar_balance == bal
    assert len(orchestrator.transactions) == initial_txn_count
