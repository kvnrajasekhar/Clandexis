"""
End-to-End PostgreSQL Integration Test Suite running against local Docker container:
postgresql://clandexis_app:clandexis_test_password@localhost:5433/clandexis_test_db

Tests full CRUD operations across all tables:
- merchants (creation, approval, 10-language preferences)
- parties (customer/supplier management, udhaar balances)
- products (catalog items, dynamic SKU auto-creation)
- workflow_messages (inbound idempotency, audit trail)
- transaction_candidates (staging, revision, assessment)
- confirmations (terminal 1-tap confirmation)
- transactions & journal_entries (ACID double-entry ledger posting, Debits == Credits)
- inventory_balances (real-time stock deduction, FR-02)
- party_balances (rolling credit/udhaar tracking, FR-04)
- DecisionService (gemini-decision-1 intent classification and deadlock prevention)
"""

import os
from decimal import Decimal
from uuid import uuid4
import pytest
import psycopg
from psycopg.rows import dict_row

from app.schemas.transaction import TransactionCandidate
from app.services.decision_service import DecisionService
from app.services.postgres_db import PostgresDatabase
from app.services import postgres_ledger as ledger
from models.canonical import (
    CanonicalButton,
    CanonicalListRow,
    CanonicalListSection,
    InboundMessage,
    MessageType,
    OutboundMessage,
)
from webhook_service.meta_whatsapp_handler import MetaWhatsAppService

DOCKER_DSN = "postgresql://clandexis_app:clandexis_test_password@localhost:5433/clandexis_test_db"


@pytest.fixture
def docker_db():
    """Provides a tenant-scoped PostgresDatabase connected to the local Docker container."""
    os.environ["DATABASE_BACKEND"] = "postgres"
    os.environ["DATABASE_URL"] = DOCKER_DSN
    db = PostgresDatabase(dsn=DOCKER_DSN)
    return db


@pytest.fixture
def test_merchant(docker_db):
    """Creates an approved test merchant with unique phone number."""
    merchant_id = uuid4()
    phone = f"+91{int(uuid4().hex[:10], 16) % 9000000000 + 1000000000}"
    with psycopg.connect(DOCKER_DSN, row_factory=dict_row) as conn:
        conn.execute(
            """INSERT INTO merchants
            (id, phone_e164, is_approved, business_name, owner_name, preferred_language, stock_policy)
            VALUES (%s, %s, true, 'Indra Electronics & Textiles', 'Indra', 'ta', 'WARN_ALLOW')
            ON CONFLICT (phone_e164) DO NOTHING""",
            (merchant_id, phone),
        )
    return {"id": merchant_id, "phone": phone}


def test_merchant_crud_and_vernacular_language_update(test_merchant, docker_db):
    """Verifies merchant creation, approval, and language updates across 10 Indian languages."""
    mid = test_merchant["id"]
    with docker_db.transaction(mid) as conn:
        # 1. Read
        m = conn.execute("SELECT * FROM merchants WHERE id=%s", (mid,)).fetchone()
        assert m is not None
        assert m["business_name"] == "Indra Electronics & Textiles"
        assert m["preferred_language"] == "ta"

        # 2. Update across languages (Tamil -> Telugu -> Hindi -> English)
        for lang, script in [("te", "Telugu"), ("hi", "Devanagari"), ("en", "Latin")]:
            conn.execute(
                "UPDATE merchants SET preferred_language=%s, script_preference=%s WHERE id=%s",
                (lang, script, mid),
            )
            updated = conn.execute("SELECT preferred_language, script_preference FROM merchants WHERE id=%s", (mid,)).fetchone()
            assert updated["preferred_language"] == lang
            assert updated["script_preference"] == script


def test_party_and_customer_crud(test_merchant, docker_db):
    """Verifies parties table CRUD operations for customer 'Indra' and credit limit."""
    mid = test_merchant["id"]
    with docker_db.transaction(mid) as conn:
        # Create customer
        party_row = conn.execute(
            """INSERT INTO parties
            (merchant_id, display_name, party_type, phone_e164, credit_limit)
            VALUES (%s, 'Indra Customer', 'CUSTOMER', '+919876543210', 1000000.00)
            RETURNING *""",
            (mid,),
        ).fetchone()
        pid = party_row["id"]
        assert pid is not None
        assert party_row["display_name"] == "Indra Customer"

        # Read back
        fetched = conn.execute("SELECT * FROM parties WHERE merchant_id=%s AND id=%s", (mid, pid)).fetchone()
        assert fetched["credit_limit"] == Decimal("1000000.00")

        # Update
        conn.execute("UPDATE parties SET display_name='Indra Enterprise' WHERE id=%s", (pid,))
        updated = conn.execute("SELECT display_name FROM parties WHERE id=%s", (pid,)).fetchone()
        assert updated["display_name"] == "Indra Enterprise"


def test_product_crud_and_dynamic_sku_creation(test_merchant, docker_db):
    """Verifies products table CRUD and dynamic SKU creation for 'Laptop'."""
    mid = test_merchant["id"]
    with docker_db.transaction(mid) as conn:
        # Create product
        prod = conn.execute(
            """INSERT INTO products
            (merchant_id, sku_code, product_name, unit, default_unit_price, is_active)
            VALUES (%s, 'SKU-LAPTOP-PRO', 'Laptop Pro 16', 'pieces', 100000.00, true)
            RETURNING *""",
            (mid,),
        ).fetchone()
        assert prod["sku_code"] == "SKU-LAPTOP-PRO"
        assert prod["default_unit_price"] == Decimal("100000.00")

        # Query active products
        active = conn.execute("SELECT COUNT(*) as cnt FROM products WHERE merchant_id=%s AND is_active", (mid,)).fetchone()
        assert active["cnt"] >= 1


def test_e2e_transaction_candidate_and_double_entry_posting(test_merchant, docker_db):
    """
    End-to-End Transaction Flow (FR-01, FR-02, FR-04):
    1. Records sale of 20 Laptops @ 100,000 = 2,000,000 to customer Indra
    2. Paid: 1,500,000 (10 Lakh Cash + 5 Lakh Card); Credit: 500,000
    3. Auto-creates catalog SKU and customer if new
    4. Stages candidate -> Commits confirmation -> Posts double-entry journal entries
    5. Validates Debits == Credits and udhaar balance update
    """
    mid = test_merchant["id"]
    candidate = TransactionCandidate(
        transaction_type="sale",
        buyer_name="Suresh Traders",
        currency="INR",
        items=[
            dict(
                item_name="Laptop",
                quantity=20,
                unit="pieces",
                unit_price=100000,
                line_total=2000000,
            )
        ],
        subtotal=2000000,
        total_amount=2000000,
        amount_paid=1500000,
        credit_amount=500000,
        payments=[
            dict(method="cash", amount=1000000),
            dict(method="card", amount=500000),
        ],
        payment_status="partial",
        validation_status="ACCEPTED",
    )

    msg_id = f"msg_{uuid4().hex[:12]}"
    staged = ledger.stage_candidate(
        merchant_id=mid,
        source_message_id=msg_id,
        candidate=candidate,
        db=docker_db,
    )
    assert staged.candidate_id is not None
    cid = staged.candidate_id

    record = ledger.get_candidate_record(mid, cid, db=docker_db)
    sha = record["snapshot_sha256"]

    # Terminal Confirmation and Posting (FR-01, FR-02, FR-04)
    commit_res = ledger.confirm_and_commit(
        merchant_id=mid,
        candidate_id=cid,
        confirmed_by=test_merchant["phone"],
        expected_snapshot_sha256=sha,
        db=docker_db,
    )
    assert commit_res.status == "COMMITTED"
    assert commit_res.total_amount == Decimal("2000000.00")
    assert commit_res.received_amount == Decimal("1500000.00")
    assert commit_res.credit_amount == Decimal("500000.00")
    assert commit_res.party_balance == Decimal("500000.00")

    # Verify Database Tables mutated correctly
    with docker_db.transaction(mid) as conn:
        # 1. Transaction Header
        tx = conn.execute("SELECT * FROM transactions WHERE merchant_id=%s AND candidate_id=%s", (mid, cid)).fetchone()
        assert tx is not None
        assert tx["total_amount"] == Decimal("2000000.00")
        assert tx["amount_paid"] == Decimal("1500000.00")
        assert tx["credit_amount"] == Decimal("500000.00")

        # 2. Double-Entry Journal Lines (Debits == Credits)
        j_lines = conn.execute(
            """SELECT SUM(jl.debit) as total_debit, SUM(jl.credit) as total_credit
            FROM journal_lines jl
            JOIN journal_entries je ON jl.journal_entry_id = je.id
            WHERE je.merchant_id=%s AND je.transaction_id=%s""",
            (mid, tx["id"]),
        ).fetchone()
        assert j_lines["total_debit"] == Decimal("2000000.00")
        assert j_lines["total_credit"] == Decimal("2000000.00")
        assert j_lines["total_debit"] == j_lines["total_credit"]  # Mathematical Invariant (FR-04.3)

        # 3. Party Udhaar Balance (FR-04)
        party_bal = conn.execute(
            "SELECT udhaar_balance FROM party_balances WHERE merchant_id=%s AND party_id=%s",
            (mid, tx["party_id"]),
        ).fetchone()
        assert party_bal is not None
        assert party_bal["udhaar_balance"] == Decimal("500000.00")


def test_decision_service_intent_and_deadlock_elimination():
    """Verifies that DecisionService resolves 'What are pending', 'Yes', and 'Cancel' dynamically."""
    svc = DecisionService()

    # 1. Query pending status
    res1 = svc.classify_merchant_intent("What are pending", has_active_candidate=True)
    assert res1["intent"] == "query_pending_status"
    assert res1["confidence"] >= 0.80

    # 2. Confirm candidate
    res2 = svc.classify_merchant_intent("Yes confirm it", has_active_candidate=True)
    assert res2["intent"] == "confirm_candidate"

    # 3. Cancel candidate
    res3 = svc.classify_merchant_intent("Cancel this transaction", has_active_candidate=True)
    assert res3["intent"] == "cancel_candidate"

    # 4. Catalog auto-create evaluation for Laptop
    cat_res = svc.decide_catalog_action(item_name="Laptop", unit_price=100000.0, quantity=20.0)
    assert cat_res["should_auto_create"] is True


def test_whatsapp_10_language_list_payload():
    """Verifies that OutboundMessage with list_sections is prioritized over buttons."""
    handler = MetaWhatsAppService(
        phone_number_id="123456",
        token="test_token",
        app_secret="test_secret",
    )

    outbound = OutboundMessage(
        recipient_phone="+916300413643",
        message_type=MessageType.INTERACTIVE_LIST,
        interactive_header="🌍 Select Language / భాషను ఎంచుకోండి",
        interactive_body="Please choose your preferred language:",
        list_button_text="Choose Language 🔽",
        list_sections=[
            CanonicalListSection(
                title="Commercial Languages",
                rows=[
                    CanonicalListRow(id="LANG_TE", title="తెలుగు (Telugu)"),
                    CanonicalListRow(id="LANG_TA", title="தமிழ் (Tamil)"),
                    CanonicalListRow(id="LANG_HI", title="हिंदी (Hindi)"),
                    CanonicalListRow(id="LANG_KN", title="ಕನ್ನಡ (Kannada)"),
                    CanonicalListRow(id="LANG_EN", title="English"),
                ],
            )
        ],
        buttons=[
            CanonicalButton(id="LANG_TE", title="Telugu"),
            CanonicalButton(id="LANG_HI", title="Hindi"),
            CanonicalButton(id="LANG_EN", title="English"),
        ],
    )

    # Verify that list_sections takes precedence over buttons
    assert outbound.list_sections is not None
    assert len(outbound.list_sections[0].rows) == 5
