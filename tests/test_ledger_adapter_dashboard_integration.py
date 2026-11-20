"""
Cross-subsystem integration against the REAL Firestore emulator:
  ConfirmationService -> ClaudeLedgerAdapter -> app.services.ledger, and the dashboard router.
Requires FIRESTORE_EMULATOR_HOST (see tests/run_ledger_tests.sh).
"""
import os
import random
from decimal import Decimal

import pytest

if not os.getenv("FIRESTORE_EMULATOR_HOST"):
    pytest.skip("FIRESTORE_EMULATOR_HOST not set: Firestore emulator required", allow_module_level=True)

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.services.ledger as ledger
from app.api.v1.dashboard_endpoints import router as dashboard_router
from app.services.confirmation import AuthenticatedMerchant, ConfirmationError, ConfirmationService, PostingResult
from app.services.confirmation_ledger_adapter import ClaudeLedgerAdapter
from tests.test_ledger_emulator import (  # reuse seeding helpers/fixtures
    col, collection_cand, count, db, mid, post, sale, seed_party, stock, unstated_sale,
)


@pytest.fixture
def svc(db):
    return ConfirmationService(ClaudeLedgerAdapter(db, ledger))


@pytest.fixture
def client(db, mid, monkeypatch, tmp_path):
    """Dashboard app wired the way production wires it: session auth + configured confirmation service."""
    from config import settings
    from app.core import security
    from app.services import posting_flow
    from app.services.confirmation import configure_confirmation
    from pdf_service import InvoiceService, LocalArtifactStore
    from pdf_service.firestore_repository import FirestoreInvoiceRepository

    monkeypatch.setattr(ledger, "_client", db)          # dashboard endpoints call ledger.get_db()
    monkeypatch.setattr(settings, "DASHBOARD_SESSION_SECRET", "s" * 40)
    monkeypatch.setattr(settings, "DASHBOARD_DEMO_PASSWORD", "p" * 20)
    monkeypatch.setattr(settings, "DASHBOARD_DEMO_MERCHANT_ID", mid)
    configure_confirmation(ClaudeLedgerAdapter(db, ledger))
    posting_flow.configure_posting_documents(InvoiceService(FirestoreInvoiceRepository(db), LocalArtifactStore(str(tmp_path))))
    from tests.auth_helpers import configure_demo, sign_in
    from app.core.security import install_security, router as auth_router
    from app.services.confirmation import configure_confirmation
    from app.services.posting_flow import configure_posting_documents
    from pdf_service import InvoiceService, LocalArtifactStore
    from pdf_service.firestore_repository import FirestoreInvoiceRepository
    configure_demo(monkeypatch, mid)
    configure_confirmation(ClaudeLedgerAdapter(db, ledger))
    configure_posting_documents(InvoiceService(FirestoreInvoiceRepository(db, ledger), LocalArtifactStore(tmp_path)))
    app = FastAPI()
    install_security(app)
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(dashboard_router)
    client = TestClient(app)
    sign_in(client)
    return client


def test_adapter_stage_confirm_replay_cancel(db, mid, svc):
    m = AuthenticatedMerchant(mid)
    row = svc.stage(m, "wamid.AD1", 0, sale())
    assert row.status == "AWAITING_CONFIRMATION" and row.revision
    res = svc.handle_confirmation(m, row.candidate_id, "confirm", "wamid.tap1")
    assert isinstance(res, PostingResult) and res.posting_status == "COMMITTED"
    assert res.amount_paid == Decimal("7000.00") and res.credit_outstanding == Decimal("3000.00")
    assert res.inventory_balance == {"shirt-cotton-l": Decimal("250.0")}
    again = svc.handle_confirmation(m, row.candidate_id, "confirm", "wamid.tap2")        # second tap
    assert again.transaction_id == res.transaction_id and count(db, mid, "transactions") == 1

    row2 = svc.stage(m, "wamid.AD2", 0, sale(qty=1, cash=200.0, credit=0.0))
    assert svc.handle_confirmation(m, row2.candidate_id, "cancel", "wamid.c1") == "CANCELLED"
    with pytest.raises(ConfirmationError):
        svc.handle_confirmation(m, row2.candidate_id, "confirm", "wamid.c2")
    assert count(db, mid, "transactions") == 1


def test_adapter_review_flow_end_to_end(db, mid, svc):
    m = AuthenticatedMerchant(mid)
    row = svc.stage(m, "wamid.AD3", 0, unstated_sale())
    assert row.originally_needs_review and not row.review_resolved
    with pytest.raises(ConfirmationError, match="review"):
        svc.handle_confirmation(m, row.candidate_id, "confirm", "wamid.t")
    reviewed = svc.resolve_review(m, row.candidate_id, sale(), "Merchant says 7000 cash, rest udhaar")
    assert reviewed.review_resolved and reviewed.revision != row.revision
    res = svc.handle_confirmation(m, row.candidate_id, "confirm", "wamid.t2")
    assert res.credit_outstanding == Decimal("3000.00") and stock(db, mid) == 250
    d = col(db, mid, "transaction_candidates").document(row.candidate_id).get().to_dict()
    assert d["original_extraction"]["payments"] == [] and len(d["review_history"]) == 1


def test_adapter_review_cannot_force_bad_arithmetic(db, mid, svc):
    m = AuthenticatedMerchant(mid)
    row = svc.stage(m, "wamid.AD4", 0, unstated_sale())
    bad = sale()
    bad.total_amount = 9000.0
    with pytest.raises(ConfirmationError):
        svc.resolve_review(m, row.candidate_id, bad, "force")
    assert count(db, mid, "transactions") == 0


def test_adapter_tenant_isolation(db, mid, svc):
    other = "+91" + "".join(random.choice("0123456789") for _ in range(10))
    from models.firestore_schema import Merchant
    db.collection("merchants").document(other).set(Merchant(merchant_id=other, business_name="O", owner_name="X").model_dump(mode="json"))
    row = svc.stage(AuthenticatedMerchant(mid), "wamid.AD5", 0, sale())
    with pytest.raises(ConfirmationError):
        svc.handle_confirmation(AuthenticatedMerchant(other), row.candidate_id, "confirm", "wamid.x")
    assert count(db, mid, "transactions") == 0


# ---------------------------------------------------------------- dashboard endpoints
def test_dashboard_endpoints_against_ledger(db, mid, client):
    st = ledger.stage_candidate(mid, "wamid.DB1", sale(), db=db)
    nr = ledger.stage_candidate(mid, "wamid.DB2", unstated_sale(), db=db)
    c = client.get("/dashboard/candidates").json()
    assert {x["candidate_id"]: x["posting_status"] for x in c} == {st.candidate_id: "PENDING_CONFIRMATION", nr.candidate_id: "NEEDS_REVIEW"}

    # NEEDS_REVIEW candidate cannot be confirmed from the dashboard
    assert client.post("/dashboard/candidates/confirm", json={"candidate_id": nr.candidate_id}).status_code == 409
    r = client.post("/dashboard/candidates/confirm", json={"candidate_id": st.candidate_id, "source_event_id": "dash-1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["posting_status"] == "COMMITTED" and body["txn_id"] and body["credit_outstanding"] == 3000.0
    assert body["invoice"]["status"] == "GENERATED"                     # post-commit side effect, outside the DB txn
    again = client.post("/dashboard/candidates/confirm", json={"candidate_id": st.candidate_id, "source_event_id": "dash-2"})
    assert again.status_code == 200 and again.json()["txn_id"] == body["txn_id"]       # replay, no 2nd posting
    assert count(db, mid, "transactions") == 1
    assert client.post("/dashboard/candidates/cancel", json={"candidate_id": nr.candidate_id}).json()["posting_status"] == "CANCELLED"

    s = client.get("/dashboard/summary").json()
    assert s["sales_total"] == 10000.0 and s["cash_received"] == 7000.0 and s["credit_extended"] == 3000.0
    inv = {i["sku_id"]: i for i in client.get("/dashboard/inventory").json()}
    assert inv["shirt-cotton-l"]["current_stock"] == 250.0
    parties = {p["party_id"]: p for p in client.get("/dashboard/parties").json()}
    assert parties["ramesh-textiles"]["current_udhaar_balance"] == 3000.0
    tx = client.get("/dashboard/transactions").json()
    assert len(tx) == 1 and tx[0]["status"] == "COMMITTED"


def test_dashboard_requires_session_and_ignores_foreign_merchant(db, mid, client):
    from fastapi.testclient import TestClient
    anon = TestClient(client.app)
    assert anon.get("/dashboard/summary").status_code == 401
    assert client.get("/dashboard/summary", params={"merchant_id": "+910000000009"}).status_code == 403
    st = ledger.stage_candidate(mid, "wamid.DB3", sale(), db=db)
    assert client.post("/dashboard/candidates/confirm", json={"candidate_id": st.candidate_id, "merchant_id": "+910000000009"}).status_code == 403
    no_csrf = TestClient(client.app, cookies=client.cookies)
    assert no_csrf.post("/dashboard/candidates/confirm", json={"candidate_id": st.candidate_id}).status_code == 403
    assert count(db, mid, "transactions") == 0
