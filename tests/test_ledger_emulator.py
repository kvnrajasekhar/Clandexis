"""
Integration tests for the atomic ledger against the REAL Firestore emulator.

These tests do not mock Firestore. They require FIRESTORE_EMULATOR_HOST; run them with
    tests/run_ledger_tests.sh
which starts the emulator via `firebase emulators:exec`. Without the emulator the module is
skipped (visible as SKIPPED in pytest output - skipped is NOT a pass).
"""

import os
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

if not os.getenv("FIRESTORE_EMULATOR_HOST"):
    pytest.skip("FIRESTORE_EMULATOR_HOST not set: Firestore emulator required", allow_module_level=True)

from google.cloud import firestore

import app.services.ledger as ledger
from app.schemas.transaction import LineItem as XItem
from app.schemas.transaction import PaymentRecord, PaymentStatus as XPay, TransactionCandidate, TransactionType
from app.services.ledger import (
    CandidateNotFoundError, HeldForReviewError, review_candidate, post_opening_balance, post_opening_balances,
    get_receivables_reconciliation, IdempotencyConflictError, InvalidStateError,
    NotAuthorizedError, PostingStatus, StaleConfirmationError, TransactionNotFoundError,
    ValidationFailedError, cancel_candidate, confirm_and_commit, get_daily_summary, get_inventory,
    get_party_balance, get_transaction, list_transactions, stage_candidate,
)
from models.firestore_schema import InventoryItem, Merchant, Party, TransactionIntent


@pytest.fixture(scope="session")
def db():
    return firestore.Client(project="demo-clandexis")


@pytest.fixture
def mid(db):
    """A fresh merchant seeded exactly like seed/seed_textile_msme.py (JSON-mode documents)."""
    merchant_id = "+91" + "".join(random.choice("0123456789") for _ in range(10))
    m = Merchant(merchant_id=merchant_id, business_name="Sri Balaji Textiles", owner_name="Rajesh Kumar")
    ref = db.collection("merchants").document(merchant_id)
    ref.set(m.model_dump(mode="json"))
    seed_party(db, merchant_id, "ramesh-textiles", "Ramesh Textiles", ["Ramesh", "Ramesh Bhai"], 0.0)
    seed_party(db, merchant_id, "suresh-traders", "Suresh Traders", ["Suresh"], 0.0, limit=30000.0)
    seed_sku(db, merchant_id, "shirt-cotton-l", "Cotton Shirt (L)", ["cotton shirt", "shirt", "cotton shirts"], 300.0, 200.0)
    seed_sku(db, merchant_id, "kurta-linen-m", "Linen Kurta (M)", ["linen kurta", "kurta"], 150.0, 450.0)
    return merchant_id


def seed_party(db, mid, pid, name, aliases, balance, limit=50000.0):
    p = Party(party_id=pid, display_name=name, aliases=aliases, current_udhaar_balance=balance, credit_limit=limit,
              last_txn_at=datetime.now(timezone.utc))
    db.collection("merchants").document(mid).collection("parties").document(pid).set(p.model_dump(mode="json"))


def seed_sku(db, mid, sku, name, aliases, stock, price):
    i = InventoryItem(sku_id=sku, item_name=name, aliases=aliases, current_stock=stock, default_unit_price=price,
                      reorder_level=50.0)
    db.collection("merchants").document(mid).collection("inventory").document(sku).set(i.model_dump(mode="json"))


def col(db, mid, name):
    return db.collection("merchants").document(mid).collection(name)


def count(db, mid, name):
    return len(list(col(db, mid, name).stream()))


def sale(qty=50, price=200.0, cash=7000.0, upi=0.0, credit=3000.0, buyer="Ramesh", item="Cotton Shirt", conf=0.95):
    total = round(qty * price, 2)
    payments = []
    if cash:
        payments.append(PaymentRecord(method="cash", amount=cash))
    if upi:
        payments.append(PaymentRecord(method="upi", amount=upi))
    return TransactionCandidate(
        transaction_type=TransactionType.SALE, buyer_name=buyer,
        items=[XItem(item_name=item, quantity=qty, unit="pieces", unit_price=price, line_total=total)],
        total_amount=total, payments=payments, amount_paid=cash + upi,   # raw float sum (50.0+49.99) exercises the validator fix
        credit_amount=credit,
        payment_status=XPay.PARTIAL if credit else XPay.PAID, confidence=conf, validation_status="ACCEPTED",
        raw_transcript="test",
    )


def collection_cand(amount=2000.0, method="upi", buyer="Ramesh"):
    return TransactionCandidate(
        transaction_type=TransactionType.PAYMENT_RECEIVED, buyer_name=buyer, total_amount=amount,
        payments=[PaymentRecord(method=method, amount=amount)], amount_paid=amount, validation_status="ACCEPTED",
        payment_status=XPay.PAID,
    )


def production(qty=200.0, item="Cotton Shirt"):
    return TransactionCandidate(
        transaction_type=TransactionType.STOCK_ADDED, items=[XItem(item_name=item, quantity=qty, unit="pieces")],
        validation_status="ACCEPTED",
    )


def post(db, mid, cand, msg, by=None, req=None):
    st = stage_candidate(mid, msg, cand, db=db)
    assert st.posting_status == PostingStatus.PENDING_CONFIRMATION, st
    return st, confirm_and_commit(mid, st.candidate_id, confirmed_by=by or mid, confirmation_request_id=req, db=db)


def stock(db, mid, sku="shirt-cotton-l"):
    return Decimal(str(col(db, mid, "inventory").document(sku).get().to_dict()["current_stock"]))


def acct(db, mid, code):
    s = col(db, mid, "accounts").document(code).get()
    return s.to_dict()["balance_minor"] if s.exists else 0


# ------------------------------------------------------------------ A
def test_A_sale_10000_7000_cash_3000_credit(db, mid):
    st = stage_candidate(mid, "wamid.A", sale(), db=db)
    # Staging posts nothing
    assert st.posting_status == PostingStatus.PENDING_CONFIRMATION
    assert count(db, mid, "transactions") == 0 and stock(db, mid) == 300
    res = confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db)

    assert res.status == "COMMITTED" and not res.idempotent_replay
    assert (res.total_amount, res.received_amount, res.credit_amount) == (Decimal("10000.00"), Decimal("7000.00"), Decimal("3000.00"))
    txn = get_transaction(mid, res.txn_id, db=db)
    assert txn["status"] == "COMMITTED" and txn["intent"] == "SALE_INVOICE" and txn["party_id"] == "ramesh-textiles"
    assert txn["total_minor"] == 1_000_000 and txn["received_minor"] == 700_000 and txn["credit_minor"] == 300_000
    # udhaar is a PENDING_COLLECTION node, not a received payment
    credit_nodes = [p for p in txn["payments"] if p["method"] == "UDHAAR_CREDIT"]
    assert len(credit_nodes) == 1 and credit_nodes[0]["status"] == "PENDING_COLLECTION"
    # only actual cash was recorded as a payment
    pays = [s.to_dict() for s in col(db, mid, "payments").stream()]
    assert [(p["method"], p["amount_minor"]) for p in pays] == [("CASH", 700_000)]
    # receivable and stock
    assert get_party_balance(mid, "ramesh-textiles", db=db).outstanding == Decimal("3000.00")
    assert stock(db, mid) == 250
    mv = [s.to_dict() for s in col(db, mid, "inventory_movements").stream()]
    assert len(mv) == 1 and mv[0]["movement_type"] == "SALE_DEDUCT" and mv[0]["quantity"] == -50 and mv[0]["balance_after"] == 250
    assert mv[0]["reference_txn_id"] == res.txn_id
    # accounting
    assert acct(db, mid, "CASH") == 700_000 and acct(db, mid, "ACCOUNTS_RECEIVABLE") == 300_000
    assert acct(db, mid, "SALES_REVENUE") == -1_000_000
    assert col(db, mid, "transaction_candidates").document(st.candidate_id).get().to_dict()["posting_status"] == "POSTED"


# ------------------------------------------------------------------ B
def test_B_repayment_reduces_receivable_without_sale_or_stock_change(db, mid):
    seed_party(db, mid, "ramesh-textiles", "Ramesh Textiles", ["Ramesh"], 3000.0)  # legacy float doc, no *_minor
    post_opening_balances(mid, posted_by=mid, db=db)      # explicit opening posting for the seeded receivable
    _, res = post(db, mid, collection_cand(2000.0, "upi"), "wamid.B")
    assert acct(db, mid, "ACCOUNTS_RECEIVABLE") == 100_000 and get_receivables_reconciliation(mid, db=db).consistent
    assert res.intent == "UDHAAR_COLLECTION" and res.party_balance == Decimal("1000.00")
    assert get_party_balance(mid, "ramesh-textiles", db=db).outstanding == Decimal("1000.00")
    assert stock(db, mid) == 300 and count(db, mid, "inventory_movements") == 0
    assert acct(db, mid, "UPI") == 200_000          # recorded funds increased
    assert acct(db, mid, "SALES_REVENUE") == 0      # no revenue
    s = get_daily_summary(mid, db=db)
    assert s.sales_count == 0 and s.sales_total == 0 and s.collections_total == Decimal("2000.00") and s.upi_received == Decimal("2000.00")


def test_B2_collection_cannot_exceed_outstanding(db, mid):
    seed_party(db, mid, "ramesh-textiles", "Ramesh Textiles", ["Ramesh"], 1000.0)
    post_opening_balances(mid, posted_by=mid, db=db)
    st = stage_candidate(mid, "wamid.B2", collection_cand(2000.0), db=db)
    with pytest.raises(HeldForReviewError):
        confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db)
    assert get_party_balance(mid, "ramesh-textiles", db=db).outstanding == Decimal("1000.00")
    assert count(db, mid, "payments") == 0


# ------------------------------------------------------------------ C
def test_C_production_then_sale(db, mid):
    seed_sku(db, mid, "shirt-cotton-l", "Cotton Shirt (L)", ["cotton shirt", "shirt"], 0.0, 200.0)
    _, prod = post(db, mid, production(200), "wamid.C1")
    assert stock(db, mid) == 200
    assert acct(db, mid, "SALES_REVENUE") == 0 and count(db, mid, "journal_entries") == 0 and count(db, mid, "payments") == 0
    _, s = post(db, mid, sale(qty=50, cash=10000.0, credit=0.0), "wamid.C2")
    assert stock(db, mid) == 150
    mv = sorted((x.to_dict() for x in col(db, mid, "inventory_movements").stream()), key=lambda m: m["balance_after"], reverse=True)
    assert [(m["movement_type"], m["quantity"], m["balance_after"]) for m in mv] == [("PRODUCTION_ADD", 200, 200), ("SALE_DEDUCT", -50, 150)]
    summ = get_daily_summary(mid, db=db)
    assert summ.sales_total == Decimal("10000.00") and summ.units_produced == Decimal("200.000") and summ.sales_count == 1


# ------------------------------------------------------------------ D
def test_D_same_message_confirmed_twice_commits_once(db, mid):
    st = stage_candidate(mid, "wamid.D", sale(), db=db)
    r1 = confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, confirmation_request_id="btn-1", db=db)
    r2 = confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, confirmation_request_id="btn-1", db=db)   # same button
    r3 = confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, confirmation_request_id="btn-2", db=db)   # second tap
    assert not r1.idempotent_replay and r2.idempotent_replay and r3.idempotent_replay
    assert r1.txn_id == r2.txn_id == r3.txn_id
    assert count(db, mid, "transactions") == 1 and count(db, mid, "inventory_movements") == 1 and count(db, mid, "payments") == 1
    assert stock(db, mid) == 250 and get_party_balance(mid, "ramesh-textiles", db=db).outstanding == Decimal("3000.00")
    # re-staging the same WhatsApp message returns the same candidate, never a second one
    again = stage_candidate(mid, "wamid.D", sale(qty=1, cash=200.0, credit=0.0), db=db)
    assert again.already_staged and again.candidate_id == st.candidate_id and again.posting_status == PostingStatus.POSTED
    assert count(db, mid, "transaction_candidates") == 1


def test_D2_request_id_reuse_for_other_candidate_is_conflict(db, mid):
    a = stage_candidate(mid, "wamid.D2a", sale(qty=1, cash=200.0, credit=0.0), db=db)
    b = stage_candidate(mid, "wamid.D2b", sale(qty=1, cash=200.0, credit=0.0), db=db)
    confirm_and_commit(mid, a.candidate_id, confirmed_by=mid, confirmation_request_id="same", db=db)
    with pytest.raises(IdempotencyConflictError):
        confirm_and_commit(mid, b.candidate_id, confirmed_by=mid, confirmation_request_id="same", db=db)
    assert count(db, mid, "transactions") == 1


# ------------------------------------------------------------------ E
def test_E_concurrent_confirmations_commit_once(db, mid):
    st = stage_candidate(mid, "wamid.E", sale(), db=db)
    n = 6
    barrier = threading.Barrier(n)

    def go(i):
        barrier.wait()
        return confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, confirmation_request_id=f"tap-{i}", db=db)

    with ThreadPoolExecutor(n) as ex:
        results = list(ex.map(go, range(n)))
    assert sum(1 for r in results if not r.idempotent_replay) == 1
    assert len({r.txn_id for r in results}) == 1
    assert count(db, mid, "transactions") == 1 and count(db, mid, "inventory_movements") == 1
    assert stock(db, mid) == 250
    assert get_party_balance(mid, "ramesh-textiles", db=db).outstanding == Decimal("3000.00")
    assert acct(db, mid, "CASH") == 700_000


def test_E2_concurrent_sales_cannot_oversell(db, mid):
    seed_sku(db, mid, "shirt-cotton-l", "Cotton Shirt (L)", ["cotton shirt", "shirt"], 100.0, 200.0)
    cands = [stage_candidate(mid, f"wamid.E2-{i}", sale(qty=60, cash=12000.0, credit=0.0), db=db) for i in range(2)]
    barrier = threading.Barrier(2)

    def go(c):
        barrier.wait()
        try:
            return confirm_and_commit(mid, c.candidate_id, confirmed_by=mid, db=db)
        except HeldForReviewError as e:
            return e

    with ThreadPoolExecutor(2) as ex:
        out = list(ex.map(go, cands))
    assert sum(isinstance(o, HeldForReviewError) for o in out) == 1
    assert stock(db, mid) == 40 and count(db, mid, "transactions") == 1


# ------------------------------------------------------------------ F
def _assert_untouched(db, mid, cid):
    for name in ("transactions", "inventory_movements", "payments", "journal_entries", "accounts", "confirmations"):
        assert count(db, mid, name) == 0, name
    assert stock(db, mid) == 300
    assert get_party_balance(mid, "ramesh-textiles", db=db).outstanding == Decimal("0.00")
    c = col(db, mid, "transaction_candidates").document(cid).get().to_dict()
    assert c["posting_status"] == "PENDING_CONFIRMATION" and c["committed_txn_id"] is None and c["confirmed_at"] is None


def test_F_failure_after_writes_queued_leaves_no_partial_state(db, mid, monkeypatch):
    st = stage_candidate(mid, "wamid.F", sale(), db=db)
    real = ledger._apply_writes

    def boom(transaction, writes):
        real(transaction, writes)           # every write queued...
        raise RuntimeError("injected failure before commit")

    monkeypatch.setattr(ledger, "_apply_writes", boom)
    with pytest.raises(RuntimeError, match="injected"):
        confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db)
    _assert_untouched(db, mid, st.candidate_id)
    monkeypatch.undo()
    res = confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db)   # clean retry works
    assert not res.idempotent_replay and count(db, mid, "transactions") == 1 and stock(db, mid) == 250


def test_F2_failure_at_commit_rpc_leaves_no_partial_state(db, mid, monkeypatch):
    from google.cloud.firestore_v1.transaction import Transaction
    st = stage_candidate(mid, "wamid.F2", sale(), db=db)
    real_commit = Transaction._commit

    def failing_commit(self):
        raise ConnectionError("injected commit RPC failure")

    monkeypatch.setattr(Transaction, "_commit", failing_commit)
    with pytest.raises(ConnectionError):
        confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db)
    monkeypatch.setattr(Transaction, "_commit", real_commit)
    _assert_untouched(db, mid, st.candidate_id)
    assert not confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db).idempotent_replay


# ------------------------------------------------------------------ G
def test_G_cross_merchant_isolation(db, mid):
    other = "+91" + "".join(random.choice("0123456789") for _ in range(10))
    db.collection("merchants").document(other).set(Merchant(merchant_id=other, business_name="Other Co", owner_name="X").model_dump(mode="json"))
    seed_party(db, other, "ramesh-textiles", "Ramesh Textiles", ["Ramesh"], 0.0)   # same slug, other tenant
    seed_sku(db, other, "shirt-cotton-l", "Cotton Shirt (L)", ["cotton shirt"], 500.0, 100.0)

    st, res = post(db, mid, sale(), "wamid.G")
    # other merchant cannot see/confirm/cancel/read it
    with pytest.raises(CandidateNotFoundError):
        confirm_and_commit(other, st.candidate_id, confirmed_by=other, db=db)
    with pytest.raises(CandidateNotFoundError):
        cancel_candidate(other, st.candidate_id, cancelled_by=other, db=db)
    with pytest.raises(TransactionNotFoundError):
        get_transaction(other, res.txn_id, db=db)
    assert list_transactions(other, db=db) == []
    # unaffected state of the other tenant
    assert stock(db, other) == 500 and get_party_balance(other, "ramesh-textiles", db=db).outstanding == 0
    assert count(db, other, "transactions") == 0 and count(db, other, "accounts") == 0
    # same source message id is a different candidate per merchant
    assert stage_candidate(other, "wamid.G", sale(), db=db).candidate_id != st.candidate_id
    # a different merchant's phone is not an authorised confirmer
    st2 = stage_candidate(mid, "wamid.G2", sale(qty=1, cash=200.0, credit=0.0), db=db)
    with pytest.raises(NotAuthorizedError):
        confirm_and_commit(mid, st2.candidate_id, confirmed_by=other, db=db)
    # authorised secondary phone (digits-only compare, Meta style without '+') works
    db.collection("merchants").document(mid).update({"authorized_phones": ["+919000000001"]})
    assert confirm_and_commit(mid, st2.candidate_id, confirmed_by="919000000001", db=db).status == "COMMITTED"


# ------------------------------------------------------------------ H
def test_H_unknown_sku_held_not_created(db, mid):
    st = stage_candidate(mid, "wamid.H1", sale(item="Silk Saree", qty=2, price=500.0, cash=1000.0, credit=0.0), db=db)
    with pytest.raises(HeldForReviewError, match="Unknown product"):
        confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db)
    assert {s.id for s in col(db, mid, "inventory").stream()} == {"shirt-cotton-l", "kurta-linen-m"}   # nothing created
    assert col(db, mid, "transaction_candidates").document(st.candidate_id).get().to_dict()["posting_status"] == "NEEDS_REVIEW"
    assert count(db, mid, "transactions") == 0


def test_H_insufficient_stock_held_never_negative(db, mid):
    st = stage_candidate(mid, "wamid.H2", sale(qty=301, price=1.0, cash=301.0, credit=0.0), db=db)
    with pytest.raises(HeldForReviewError, match="Insufficient stock"):
        confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db)
    assert stock(db, mid) == 300 and count(db, mid, "transactions") == 0
    # after stock is added the held candidate can be confirmed again (LEDGER-origin hold)
    seed_sku(db, mid, "shirt-cotton-l", "Cotton Shirt (L)", ["cotton shirt", "shirt"], 400.0, 200.0)
    assert confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db).status == "COMMITTED"
    assert stock(db, mid) == 99


def test_H_ambiguous_sku_and_customer_held(db, mid):
    seed_sku(db, mid, "shirt-cotton-m", "Cotton Shirt (M)", ["cotton shirt"], 10.0, 190.0)   # shares alias
    st = stage_candidate(mid, "wamid.H3", sale(qty=1, cash=200.0, credit=0.0), db=db)
    with pytest.raises(HeldForReviewError, match="Ambiguous product"):
        confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db)
    seed_party(db, mid, "ramesh-garments", "Ramesh Garments", ["Ramesh"], 0.0)   # ambiguous "Ramesh"
    st = stage_candidate(mid, "wamid.H4", sale(item="kurta", qty=1, price=450.0, cash=450.0, credit=0.0), db=db)
    with pytest.raises(HeldForReviewError, match="Ambiguous customer"):
        confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db)
    st = stage_candidate(mid, "wamid.H5", sale(item="kurta", qty=1, price=450.0, cash=450.0, credit=0.0, buyer="Nobody"), db=db)
    with pytest.raises(HeldForReviewError, match="Unknown customer"):
        confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db)
    assert count(db, mid, "parties") == 3 and count(db, mid, "transactions") == 0


def test_H_credit_limit_enforced(db, mid):
    seed_party(db, mid, "ramesh-textiles", "Ramesh Textiles", ["Ramesh"], 49000.0, limit=50000.0)
    st = stage_candidate(mid, "wamid.H6", sale(), db=db)   # +3000 credit > 50000 limit
    with pytest.raises(HeldForReviewError, match="Credit limit"):
        confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db)
    assert get_party_balance(mid, "ramesh-textiles", db=db).outstanding == Decimal("49000.00")


# ------------------------------------------------------------------ I
def test_I_journal_entries_balance(db, mid):
    seed_sku(db, mid, "shirt-cotton-l", "Cotton Shirt (L)", ["cotton shirt", "shirt"], 1000.0, 200.0)
    post(db, mid, sale(), "wamid.I1")
    post(db, mid, sale(qty=3, price=33.33, cash=50.0, upi=49.99, credit=0.0), "wamid.I2")      # fractional rupees
    post(db, mid, collection_cand(1500.0, "cash"), "wamid.I3")
    post(db, mid, production(10), "wamid.I4")
    entries = [s.to_dict() for s in col(db, mid, "journal_entries").stream()]
    assert len(entries) == 3                     # production has no journal
    for e in entries:
        assert e["total_debit_minor"] == e["total_credit_minor"]
        assert sum(l["debit_minor"] for l in e["lines"]) == sum(l["credit_minor"] for l in e["lines"])
    accounts = [s.to_dict() for s in col(db, mid, "accounts").stream()]
    assert sum(a["balance_minor"] for a in accounts) == 0          # trial balance
    assert sum(a["debit_total_minor"] for a in accounts) == sum(a["credit_total_minor"] for a in accounts)
    assert acct(db, mid, "SALES_REVENUE") == -(1_000_000 + 9_999)
    assert get_party_balance(mid, "ramesh-textiles", db=db).outstanding == Decimal("1500.00")


def test_I_gst_sale_balances(db, mid):
    c = sale(qty=10, price=200.0, cash=2100.0, credit=0.0)
    c.subtotal, c.tax_amount, c.total_amount = 2000.0, 100.0, 2100.0
    post(db, mid, c, "wamid.I5")
    assert acct(db, mid, "SALES_REVENUE") == -200_000 and acct(db, mid, "GST_PAYABLE") == -10_000 and acct(db, mid, "CASH") == 210_000


# ------------------------------------------------------------------ Extra contract rules
def test_stage_does_not_trust_client_validation_status(db, mid):
    bad = sale()
    bad.total_amount = 9000.0            # contradicts line items; client still claims ACCEPTED
    st = stage_candidate(mid, "wamid.X1", bad, db=db)
    assert st.validation_status == "REJECTED" and st.posting_status == PostingStatus.REJECTED
    with pytest.raises(InvalidStateError):
        confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db)
    assert count(db, mid, "transactions") == 0


def test_high_confidence_alone_never_posts_and_unconfirmed_stays_unposted(db, mid):
    st = stage_candidate(mid, "wamid.X2", sale(conf=1.0), db=db)
    assert count(db, mid, "transactions") == 0 and stock(db, mid) == 300
    assert st.posting_status == PostingStatus.PENDING_CONFIRMATION


def test_unmentioned_payment_is_held_at_staging(db, mid):
    c = sale(cash=0.0, credit=0.0)
    c.payments, c.amount_paid, c.credit_amount, c.payment_status = [], None, None, XPay.UNKNOWN
    st = stage_candidate(mid, "wamid.X3", c, db=db)
    assert st.posting_status == PostingStatus.NEEDS_REVIEW
    with pytest.raises(InvalidStateError):
        confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db)


def test_unsupported_intent_held(db, mid):
    c = TransactionCandidate(transaction_type=TransactionType.PURCHASE, supplier_name="X", total_amount=100.0,
                             payments=[PaymentRecord(method="cash", amount=100.0)], validation_status="ACCEPTED")
    assert stage_candidate(mid, "wamid.X4", c, db=db).posting_status == PostingStatus.NEEDS_REVIEW


def test_stale_confirmation_rejected(db, mid):
    st = stage_candidate(mid, "wamid.X5", sale(), db=db)
    late = datetime.now(timezone.utc) + timedelta(days=2)
    with pytest.raises(StaleConfirmationError):
        confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, now=late, db=db)
    with pytest.raises(StaleConfirmationError):
        confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, expected_snapshot_sha256="0" * 64, db=db)
    assert count(db, mid, "transactions") == 0


def test_snapshot_tamper_detected(db, mid):
    st = stage_candidate(mid, "wamid.X6", sale(), db=db)
    ref = col(db, mid, "transaction_candidates").document(st.candidate_id)
    ref.update({"extracted_snapshot.total_amount": 1.0})
    with pytest.raises(InvalidStateError, match="integrity"):
        confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db)
    assert count(db, mid, "transactions") == 0


def test_cancel_lifecycle(db, mid):
    st = stage_candidate(mid, "wamid.X7", sale(), db=db)
    assert cancel_candidate(mid, st.candidate_id, cancelled_by=mid, reason="wrong", db=db).posting_status == PostingStatus.CANCELLED
    assert cancel_candidate(mid, st.candidate_id, cancelled_by=mid, db=db).posting_status == PostingStatus.CANCELLED   # idempotent
    with pytest.raises(InvalidStateError):
        confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db)
    st2, res = post(db, mid, sale(), "wamid.X8")
    with pytest.raises(InvalidStateError):                       # committed records are not cancelled/edited
        cancel_candidate(mid, st2.candidate_id, cancelled_by=mid, db=db)
    assert get_transaction(mid, res.txn_id, db=db)["status"] == "COMMITTED"
    hist = col(db, mid, "transaction_candidates").document(st.candidate_id).get().to_dict()["status_history"]
    assert [h["to"] for h in hist] == ["PENDING_CONFIRMATION", "CANCELLED"]


def test_reads_party_inventory_list(db, mid):
    post(db, mid, sale(), "wamid.R1")
    post(db, mid, collection_cand(500.0, "cash"), "wamid.R2")
    pb = get_party_balance(mid, party_name="Ramesh Bhai", db=db)
    assert pb.party_id == "ramesh-textiles" and pb.outstanding == Decimal("2500.00") and pb.available_credit == Decimal("47500.00")
    inv = {i.sku_id: i for i in get_inventory(mid, db=db)}
    assert inv["shirt-cotton-l"].current_stock == Decimal("250.000") and not inv["shirt-cotton-l"].below_reorder_level
    assert get_inventory(mid, "kurta-linen-m", db=db)[0].current_stock == Decimal("150.000")
    txns = list_transactions(mid, db=db)
    assert [t["intent"] for t in txns] == ["UDHAAR_COLLECTION", "SALE_INVOICE"] or len(txns) == 2
    assert len(list_transactions(mid, intent=TransactionIntent.SALE_INVOICE, db=db)) == 1
    assert len(list_transactions(mid, party_id="suresh-traders", db=db)) == 0
    s = get_daily_summary(mid, db=db)
    assert (s.sales_total, s.cash_received, s.credit_extended, s.collections_total, s.total_receivables_outstanding) == (
        Decimal("10000.00"), Decimal("7500.00"), Decimal("3000.00"), Decimal("500.00"), Decimal("2500.00"))


def test_money_uses_decimal_not_float():
    assert ledger.to_money(0.1) + ledger.to_money(0.2) == ledger.to_money(0.3)
    assert ledger.to_money("2.675") == Decimal("2.68")        # consistent ROUND_HALF_UP
    assert ledger.to_minor(ledger.to_money(99.99)) == 9999


# ------------------------------------------------------------------ review_candidate
def unstated_sale():
    c = sale(cash=0.0, credit=0.0)
    c.payments, c.amount_paid, c.credit_amount, c.payment_status = [], None, None, XPay.UNKNOWN
    c.raw_transcript = "sold 50 shirts to Ramesh for 10000"
    return c


def test_review_releases_needs_review_candidate_and_preserves_history(db, mid):
    st = stage_candidate(mid, "wamid.RV1", unstated_sale(), db=db)
    assert st.posting_status == PostingStatus.NEEDS_REVIEW
    ref = col(db, mid, "transaction_candidates").document(st.candidate_id)
    before = ref.get().to_dict()
    with pytest.raises(InvalidStateError):                       # cannot bypass review
        confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db)

    out = review_candidate(mid, st.candidate_id, reviewed_by=mid, corrected_candidate=sale(), review_note="Ramesh paid 7000 cash",
                           expected_snapshot_sha256=before["snapshot_sha256"], db=db)
    assert out.posting_status == PostingStatus.PENDING_CONFIRMATION
    after = ref.get().to_dict()
    assert after["review_resolved"] and after["snapshot_sha256"] != before["snapshot_sha256"]
    assert after["original_extraction"] == before["original_extraction"] == before["extracted_snapshot"]   # original preserved
    assert len(after["review_history"]) == 1 and after["review_history"][0]["previous_snapshot"] == before["extracted_snapshot"]
    assert after["review_history"][0]["by"] == mid and after["review_history"][0]["note"] == "Ramesh paid 7000 cash"
    assert count(db, mid, "transactions") == 0                    # review never posts

    # confirmation must reference the reviewed revision
    with pytest.raises(StaleConfirmationError):
        confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, expected_snapshot_sha256=before["snapshot_sha256"], db=db)
    res = confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, expected_snapshot_sha256=after["snapshot_sha256"], db=db)
    assert res.status == "COMMITTED" and res.credit_amount == Decimal("3000.00") and stock(db, mid) == 250


def test_review_authorization_and_tenancy(db, mid):
    other = "+91" + "".join(random.choice("0123456789") for _ in range(10))
    db.collection("merchants").document(other).set(Merchant(merchant_id=other, business_name="O", owner_name="X").model_dump(mode="json"))
    st = stage_candidate(mid, "wamid.RV2", unstated_sale(), db=db)
    with pytest.raises(NotAuthorizedError):
        review_candidate(mid, st.candidate_id, reviewed_by=other, corrected_candidate=sale(), review_note="x", db=db)
    with pytest.raises(CandidateNotFoundError):
        review_candidate(other, st.candidate_id, reviewed_by=other, corrected_candidate=sale(), review_note="x", db=db)
    with pytest.raises(ValueError):
        review_candidate(mid, st.candidate_id, reviewed_by=mid, corrected_candidate=sale(), review_note="  ", db=db)
    assert col(db, mid, "transaction_candidates").document(st.candidate_id).get().to_dict()["review_history"] == []


def test_review_cannot_bypass_arithmetic_errors(db, mid):
    st = stage_candidate(mid, "wamid.RV3", unstated_sale(), db=db)
    ref = col(db, mid, "transaction_candidates").document(st.candidate_id)
    sha = ref.get().to_dict()["snapshot_sha256"]
    bad = sale()
    bad.total_amount = 9000.0                                    # contradicts line items
    with pytest.raises(ValidationFailedError):
        review_candidate(mid, st.candidate_id, reviewed_by=mid, corrected_candidate=bad, review_note="force it", db=db)
    over = sale(cash=11000.0, credit=0.0)                        # overpayment
    over.payments[0].amount = 11000.0
    with pytest.raises(ValidationFailedError):
        review_candidate(mid, st.candidate_id, reviewed_by=mid, corrected_candidate=over, review_note="force it", db=db)
    d = ref.get().to_dict()
    assert d["snapshot_sha256"] == sha and d["posting_status"] == "NEEDS_REVIEW" and not d["review_resolved"]
    assert [h["outcome"] for h in d["review_history"]] == ["REFUSED", "REFUSED"]     # attempts audited
    with pytest.raises(InvalidStateError):
        confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db)
    assert count(db, mid, "transactions") == 0


def test_review_with_remaining_issue_stays_held(db, mid):
    st = stage_candidate(mid, "wamid.RV4", unstated_sale(), db=db)
    still = unstated_sale()
    out = review_candidate(mid, st.candidate_id, reviewed_by=mid, corrected_candidate=still, review_note="still unknown", db=db)
    assert out.posting_status == PostingStatus.NEEDS_REVIEW
    d = col(db, mid, "transaction_candidates").document(st.candidate_id).get().to_dict()
    assert not d["review_resolved"] and d["review_count"] == 1


def test_review_not_allowed_after_posting_cancel_or_reject(db, mid):
    st, _ = post(db, mid, sale(), "wamid.RV5")
    with pytest.raises(InvalidStateError):
        review_candidate(mid, st.candidate_id, reviewed_by=mid, corrected_candidate=sale(qty=1, cash=200.0, credit=0.0), review_note="edit", db=db)
    bad = sale()
    bad.total_amount = 9000.0
    rej = stage_candidate(mid, "wamid.RV6", bad, db=db)
    with pytest.raises(InvalidStateError):
        review_candidate(mid, rej.candidate_id, reviewed_by=mid, corrected_candidate=sale(), review_note="fix", db=db)
    assert count(db, mid, "transactions") == 1


def test_review_fixes_unknown_sku_hold(db, mid):
    c = sale(item="Silk Saree", qty=2, price=500.0, cash=1000.0, credit=0.0)
    st = stage_candidate(mid, "wamid.RV7", c, db=db)
    with pytest.raises(HeldForReviewError):
        confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db)          # LEDGER hold
    out = review_candidate(mid, st.candidate_id, reviewed_by=mid, review_note="it was kurtas",
                           corrected_candidate=sale(item="kurta", qty=2, price=450.0, cash=900.0, credit=0.0), db=db)
    assert out.posting_status == PostingStatus.PENDING_CONFIRMATION
    assert confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db).status == "COMMITTED"
    assert stock(db, mid, "kurta-linen-m") == 148


# ------------------------------------------------------------------ opening balances
def test_opening_balance_explicit_posting(db, mid):
    seed_party(db, mid, "ramesh-textiles", "Ramesh Textiles", ["Ramesh"], 3000.0)
    seed_party(db, mid, "suresh-traders", "Suresh Traders", ["Suresh"], 500.5, limit=30000.0)
    rec = get_receivables_reconciliation(mid, db=db)
    assert rec.unposted_opening_total == Decimal("3500.50") and rec.ar_account_balance == 0

    # a repayment is held until the legacy receivable is backed by a journal
    st = stage_candidate(mid, "wamid.OB1", collection_cand(2000.0, "upi"), db=db)
    with pytest.raises(HeldForReviewError, match="opening-balance"):
        confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db)
    assert get_party_balance(mid, "ramesh-textiles", db=db).outstanding == Decimal("3000.00")

    with pytest.raises(NotAuthorizedError):
        post_opening_balance(mid, "ramesh-textiles", posted_by="+910000000000", db=db)
    r = post_opening_balance(mid, "ramesh-textiles", posted_by=mid, db=db)
    assert r.amount == Decimal("3000.00") and not r.already_posted
    assert post_opening_balance(mid, "ramesh-textiles", posted_by=mid, db=db).already_posted      # idempotent
    post_opening_balances(mid, posted_by=mid, db=db)                                              # picks up suresh only
    assert acct(db, mid, "ACCOUNTS_RECEIVABLE") == 350_050 and acct(db, mid, "OPENING_BALANCE_EQUITY") == -350_050
    assert count(db, mid, "opening_balances") == 2 and count(db, mid, "journal_entries") == 2
    # party balance itself untouched (no history rewrite)
    assert get_party_balance(mid, "ramesh-textiles", db=db).outstanding == Decimal("3000.00")
    rec = get_receivables_reconciliation(mid, db=db)
    assert rec.consistent and rec.unposted_opening_total == 0 and rec.ar_account_balance == rec.party_balance_total

    assert confirm_and_commit(mid, st.candidate_id, confirmed_by=mid, db=db).party_balance == Decimal("1000.00")   # held candidate now posts
    assert acct(db, mid, "ACCOUNTS_RECEIVABLE") == 150_050
    accounts = [a.to_dict() for a in col(db, mid, "accounts").stream()]
    assert sum(a["balance_minor"] for a in accounts) == 0
    rec = get_receivables_reconciliation(mid, db=db)
    assert rec.consistent and rec.ar_account_balance == rec.party_balance_total == Decimal("1500.50")


def test_opening_balance_sale_then_opening_stays_consistent(db, mid):
    seed_party(db, mid, "ramesh-textiles", "Ramesh Textiles", ["Ramesh"], 1000.0)
    post(db, mid, sale(), "wamid.OB2")                       # +3000 credit, ledger-backed
    assert get_receivables_reconciliation(mid, db=db).unposted_opening_total == Decimal("1000.00")
    post_opening_balance(mid, "ramesh-textiles", posted_by=mid, db=db)
    rec = get_receivables_reconciliation(mid, db=db)
    assert rec.consistent and rec.ar_account_balance == Decimal("4000.00") == rec.party_balance_total
