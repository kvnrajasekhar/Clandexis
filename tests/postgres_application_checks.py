"""Run explicitly with PostgreSQL configuration; no mocked database or ledger writes."""

import json, os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4
from pathlib import Path
import hashlib, hmac
import pytest
from fastapi.testclient import TestClient
from app.services import postgres_ledger as ledger
from app.services.postgres_db import PostgresDatabase
from app.services.confirmation import get_confirmation_service, button_id
from app.services import posting_flow
from app.services.whatsapp_conversation import process_whatsapp_message
from models.canonical import InboundMessage, MessageType
from tests.auth_helpers import configure_demo, sign_in, META_SECRET
from seed.postgres_textile import seed_demo_data
from app.schemas.transaction import (
    TransactionCandidate,
    ExtractionResponse,
    ProcessingResult,
    ProcessedInputPayload,
)
import webhook_service.main as application
import agent_orchestrator.orchestrator as orchestrator


def sale():
    return TransactionCandidate(
        transaction_type="sale",
        buyer_name="Suresh Traders",
        currency="INR",
        items=[
            dict(
                item_name="Cotton Shirt",
                quantity=50,
                unit="pieces",
                unit_price=200,
                line_total=10000,
            )
        ],
        total_amount=10000,
        amount_paid=2000,
        credit_amount=8000,
        payments=[dict(method="upi", amount=2000)],
        payment_status="partial",
        validation_status="ACCEPTED",
    )


@pytest.fixture
def app_client(monkeypatch, tmp_path):
    assert os.environ.get("DATABASE_BACKEND") == "postgres"
    db = PostgresDatabase()
    merchant, other = str(uuid4()), str(uuid4())
    # Each test has unique approved phones and independent records.
    phone = "+91" + str(int(uuid4().hex[:10], 16) % 9000000000 + 1000000000)
    second = "+91" + str(int(uuid4().hex[:10], 16) % 9000000000 + 1000000000)
    seed_demo_data(db, merchant, phone)
    seed_demo_data(db, other, second)
    configure_demo(monkeypatch, merchant)
    monkeypatch.setattr(
        application.settings,
        "WHATSAPP_DEMO_PHONE_MAP",
        json.dumps({phone: merchant, second: other}),
    )
    monkeypatch.setattr(application.settings, "INVOICE_LOCAL_ROOT", str(tmp_path))
    monkeypatch.setattr(posting_flow, "_invoice_service", None)
    monkeypatch.setattr(posting_flow, "_url_resolver", None)
    sent = []

    class Meta(application.MetaWhatsAppService):
        def __init__(self):
            self.phone_number_id = "phone"

        async def close(self):
            pass

        async def dispatch_outbound(self, outbound):
            sent.append(outbound)

    monkeypatch.setattr(application, "MetaWhatsAppService", Meta)
    with TestClient(application.app, raise_server_exceptions=False) as client:
        yield client, db, merchant, other, phone, second, sent, tmp_path


def text(client, phone, body, event):
    return callback(client, phone, event, "text", {"text": {"body": body}})


def confirm(client, phone, cid, event):
    return callback(
        client,
        phone,
        event,
        "interactive",
        {
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": button_id(cid, "confirm"), "title": "Confirm"},
            }
        },
    )


def callback(client, phone, event, kind, fields):
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15551737641",
                                "phone_number_id": "phone",
                            },
                            "messages": [
                                {
                                    "from": phone.lstrip("+"),
                                    "id": event,
                                    "timestamp": "1795132800",
                                    "type": kind,
                                    **fields,
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    body = json.dumps(payload).encode()
    sig = hmac.new(META_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=" + sig,
        },
    )


def mock_pipeline(monkeypatch, tx):
    monkeypatch.setattr(
        orchestrator,
        "pipeline",
        SimpleNamespace(
            process_and_interpret=lambda **kw: ProcessingResult(
                input_metadata=ProcessedInputPayload(
                    merchant_id=kw["merchant_id"],
                    message_id=kw["message_id"],
                    idempotency_key=kw["message_id"],
                ),
                extraction=ExtractionResponse(transactions=[tx]),
            )
        ),
    )


def test_sale_repayment_invoice_dashboard_and_replay(app_client, monkeypatch):
    client, db, merchant, other, phone, second, sent, root = app_client
    mock_pipeline(monkeypatch, sale())
    assert (
        text(
            client,
            phone,
            "Sold 50 shirts to Suresh, 200 each, 2000 UPI and 8000 credit",
            "sale",
        ).json()["messages_received"]
        == 1
    )
    with db.transaction(merchant) as c:
        cid = str(
            c.execute(
                "SELECT active_candidate_id FROM conversations WHERE merchant_id=%s",
                (merchant,),
            ).fetchone()["active_candidate_id"]
        )
    assert ledger.get_daily_summary(merchant).sales_total == 0
    assert confirm(client, phone, cid, "confirm").status_code == 200
    assert ledger.get_daily_summary(merchant).sales_total == 10000
    assert (
        next(
            i for i in ledger.get_inventory(merchant) if "Shirt" in i.item_name
        ).current_stock
        == 150
    )
    assert any("COMMITTED" in (m.text_content or "") for m in sent)
    assert len(list(root.rglob("*.pdf"))) == 1
    confirm(client, phone, cid, "confirm")
    confirm(client, phone, cid, "confirm-again")
    assert ledger.get_daily_summary(merchant).sales_total == 10000
    assert ledger.get_daily_summary(other).sales_total == 0
    assert (
        next(
            i for i in ledger.get_inventory(other) if "Shirt" in i.item_name
        ).current_stock
        == 200
    )
    sign_in(client)
    assert client.get("/api/v1/dashboard/summary").json()["sales_total"] == 10000
    tx = next(
        t for t in ledger.list_transactions(merchant) if t["intent"] == "SALE_INVOICE"
    )
    response = client.get("/api/v1/dashboard/transactions/" + tx["txn_id"] + "/invoice")
    assert response.status_code == 200 and response.content.startswith(b"%PDF")
    assert client.get("/api/v1/dashboard/inventory").status_code == 200
    assert client.get("/api/v1/dashboard/parties").status_code == 200
    assert client.get("/api/v1/dashboard/candidates").status_code == 200
    assert (
        client.get(
            "/api/v1/dashboard/summary", params={"merchant_id": other}
        ).status_code
        == 403
    )
    repay = TransactionCandidate(
        transaction_type="payment_received",
        buyer_name="Suresh Traders",
        total_amount=3000,
        amount_paid=3000,
        credit_amount=0,
        payments=[dict(method="upi", amount=3000)],
        validation_status="ACCEPTED",
        payment_status="paid",
    )
    mock_pipeline(monkeypatch, repay)
    text(client, phone, "Suresh repaid 3000 UPI", "repay")
    rcid = sent[-1].buttons[0].id.split(":")[-1]
    confirm(client, phone, rcid, "repay-confirm")
    assert "Credit outstanding: 5000" in sent[-1].text_content
    s = ledger.get_daily_summary(merchant)
    assert (
        s.sales_total == 10000
        and s.upi_received == 5000
        and s.collections_total == 3000
        and s.total_receivables_outstanding == 5000
    )
    assert (
        next(
            i for i in ledger.get_inventory(merchant) if "Shirt" in i.item_name
        ).current_stock
        == 150
    )
    assert len(list(root.rglob("*.pdf"))) == 1
    text(client, phone, "PDF ledgers kavali", "report")
    assert ledger.get_daily_summary(merchant).sales_total == 10000


def test_partial_clarification_same_candidate_restart_and_isolation(
    app_client, monkeypatch
):
    client, db, merchant, other, phone, second, sent, root = app_client
    first = TransactionCandidate(
        transaction_type="sale",
        buyer_name="Raju",
        items=[dict(item_name="Cotton Shirt", quantity=10)],
        needs_clarification=True,
        missing_fields=["unit_price", "payment_details"],
        raw_transcript="Nenu Raju ki 10 shirts ammnu",
    )
    price = TransactionCandidate(
        transaction_type="sale",
        buyer_name="Raju",
        items=[
            dict(item_name="Cotton Shirt", quantity=10, unit_price=200, line_total=2000)
        ],
        total_amount=2000,
        needs_clarification=True,
        missing_fields=["payment_details"],
    )
    complete = TransactionCandidate(
        transaction_type="sale",
        buyer_name="Raju",
        items=[
            dict(item_name="Cotton Shirt", quantity=10, unit_price=200, line_total=2000)
        ],
        total_amount=2000,
        amount_paid=500,
        credit_amount=1500,
        payments=[dict(method="upi", amount=500)],
        validation_status="ACCEPTED",
        payment_status="partial",
    )
    mock_pipeline(monkeypatch, first)
    text(client, phone, "Nenu Raju ki 10 shirts ammnu", "start")
    with db.transaction(merchant) as c:
        cid = str(
            c.execute(
                "SELECT active_candidate_id FROM conversations WHERE merchant_id=%s",
                (merchant,),
            ).fetchone()["active_candidate_id"]
        )
    assert not sent[-1].buttons and "price entha" in sent[-1].text_content
    confirm(client, phone, cid, "premature")
    assert ledger.get_daily_summary(merchant).sales_total == 0
    mock_pipeline(monkeypatch, price)
    text(client, phone, "200 each", "price")
    assert not sent[-1].buttons and "payment" in sent[-1].text_content
    from app.services.postgres_conversation import PostgresConversationStore
    from app.services.confirmation import configure_confirmation
    from app.services.postgres_confirmation_adapter import PostgresLedgerAdapter

    configure_confirmation(PostgresLedgerAdapter(PostgresDatabase()))
    mock_pipeline(monkeypatch, complete)
    text(client, phone, "500 UPI, 1500 udhaar", "payment")
    assert sent[-1].buttons[0].id == button_id(cid, "confirm")
    with db.transaction(merchant) as c:
        assert (
            c.execute(
                "SELECT count(*) AS n FROM candidate_reviews WHERE merchant_id=%s AND candidate_id=%s",
                (merchant, cid),
            ).fetchone()["n"]
            == 2
        )
        assert c.execute(
            "SELECT active_candidate_id FROM conversations WHERE merchant_id=%s",
            (merchant,),
        ).fetchone()["active_candidate_id"].hex == cid.replace("-", "")
    confirm(client, second, cid, "foreign-confirm")
    assert ledger.get_daily_summary(merchant).sales_total == 0
    confirm(client, phone, cid, "correct-confirm")
    assert ledger.get_daily_summary(merchant).sales_total == 2000
    assert ledger.get_daily_summary(other).sales_total == 0
    with db.transaction(merchant) as c:
        assert (
            c.execute(
                "SELECT original_extraction FROM transaction_candidates WHERE merchant_id=%s AND id=%s",
                (merchant, cid),
            ).fetchone()["original_extraction"]["items"][0]["unit_price"]
            is None
        )


def test_concurrent_confirmation_and_stock_safety(app_client, monkeypatch):
    client, db, merchant, other, phone, second, sent, root = app_client
    mock_pipeline(monkeypatch, sale())
    text(client, phone, "Sold shirts to Suresh", "start")
    cid = sent[-1].buttons[0].id.split(":")[-1]
    message = InboundMessage(
        message_id="concurrent",
        tenant_id=merchant,
        sender_phone=phone,
        message_type=MessageType.INTERACTIVE_BUTTON,
        interactive_id=button_id(cid, "confirm"),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: process_whatsapp_message(message), range(2)))
    assert ledger.get_daily_summary(merchant).sales_count == 1
    assert (
        next(
            i for i in ledger.get_inventory(merchant) if "Shirt" in i.item_name
        ).current_stock
        == 150
    )
    # Same-candidate replay returns original stock-at-posting from persisted result.
    result, _ = orchestrator.orchestrate_inbound_message(
        message.model_copy(update={"message_id": "replay"})
    )
    assert list(result.inventory_balance.values()) == [150]


def test_dashboard_extraction_stage_auth_and_csrf(app_client, monkeypatch):
    import app.api.v1.endpoints as extraction

    client, db, merchant, other, phone, second, sent, root = app_client
    result = ProcessingResult(
        input_metadata=ProcessedInputPayload(
            merchant_id=merchant, message_id="extract", idempotency_key="extract"
        ),
        extraction=ExtractionResponse(transactions=[sale()]),
    )
    monkeypatch.setattr(
        extraction,
        "pipeline",
        SimpleNamespace(process_and_interpret=lambda **kw: result),
    )
    payload = {
        "merchant_id": merchant,
        "message_id": "extract",
        "raw_text": "sale",
        "stage": True,
    }
    assert client.post("/api/v1/extract/text", json=payload).status_code == 401
    sign_in(client)
    assert (
        client.post(
            "/api/v1/extract/text", json=payload, headers={"X-CSRF-Token": "wrong"}
        ).status_code
        == 403
    )
    r = client.post("/api/v1/extract/text", json=payload)
    assert r.status_code == 200
    cid = r.json()["staged_candidates"][0]["candidate_id"]
    assert ledger.get_daily_summary(merchant).sales_total == 0
    r = client.post("/api/v1/dashboard/candidates/confirm", json={"candidate_id": cid})
    assert r.status_code == 200 and r.json()["posting_status"] == "COMMITTED"
    assert r.json()["invoice"]["status"] == "GENERATED"
    assert ledger.get_daily_summary(merchant).sales_total == 10000
    assert "postgresql://" not in r.text


def test_failure_does_not_undo_commit_or_allow_excess_stock(app_client, monkeypatch):
    client, db, merchant, other, phone, second, sent, root = app_client

    class BrokenDocuments:
        def generate_document(self, *args):
            raise RuntimeError("secret-never-expose")

    monkeypatch.setattr(posting_flow, "_invoice_service", BrokenDocuments())
    mock_pipeline(monkeypatch, sale())
    text(client, phone, "sale", "start")
    cid = sent[-1].buttons[0].id.split(":")[-1]
    confirm(client, phone, cid, "confirm")
    assert ledger.get_daily_summary(merchant).sales_total == 10000
    assert (
        "PENDING_RETRY" in sent[-1].text_content
        and "secret-never-expose" not in sent[-1].text_content
    )
    large = sale().model_copy(deep=True)
    large.items[0].quantity = 200
    large.items[0].line_total = 40000
    large.total_amount = 40000
    large.amount_paid = 32000
    large.payments[0].amount = 32000
    mock_pipeline(monkeypatch, large)
    text(client, phone, "sale", "larger")
    bad = sent[-1].buttons[0].id.split(":")[-1]
    confirm(client, phone, bad, "larger-confirm")
    assert ledger.get_daily_summary(merchant).sales_count == 1
    assert (
        next(
            i for i in ledger.get_inventory(merchant) if "Shirt" in i.item_name
        ).current_stock
        == 150
    )


def test_stock_hindi_context_unknown_sender_and_database_isolation(
    app_client, monkeypatch
):
    client, db, merchant, other, phone, second, sent, root = app_client
    production = TransactionCandidate(
        transaction_type="stock_added",
        items=[dict(item_name="Cotton Shirt", quantity=10, unit="pieces")],
        validation_status="ACCEPTED",
    )
    mock_pipeline(monkeypatch, production)
    assert (
        text(client, "+12025550101", "sale", "unknown").json()["messages_received"] == 0
    )
    assert sent == []
    text(client, phone, "Produced 10 cotton shirts", "production")
    cid = sent[-1].buttons[0].id.split(":")[-1]
    confirm(client, phone, cid, "production-confirm")
    assert (
        next(
            i for i in ledger.get_inventory(merchant) if "Shirt" in i.item_name
        ).current_stock
        == 210
    )
    assert ledger.get_daily_summary(merchant).units_produced == 10
    assert ledger.get_daily_summary(merchant).sales_total == 0
    partial = TransactionCandidate(
        transaction_type="sale",
        buyer_name="Raju",
        items=[dict(item_name="Cotton Shirt", quantity=10)],
        needs_clarification=True,
        missing_fields=["unit_price", "payment_details"],
    )
    mock_pipeline(monkeypatch, partial)
    text(client, second, "आज मैंने राजू को 10 शर्ट बेचीं।", "hindi")
    assert "कीमत" in sent[-1].text_content and not sent[-1].buttons
    with db.transaction(other) as conn:
        state = conn.execute(
            "SELECT * FROM conversations WHERE merchant_id=%s", (other,)
        ).fetchone()
        assert (
            state["detected_language"] == "hi"
            and state["script_preference"] == "Devanagari"
        )
        assert (
            conn.execute(
                "SELECT * FROM transactions WHERE merchant_id=%s", (merchant,)
            ).fetchall()
            == []
        )
    confirm(client, phone, str(state["active_candidate_id"]), "foreign")
    assert ledger.get_daily_summary(other).sales_total == 0
    assert (
        next(
            i for i in ledger.get_inventory(other) if "Shirt" in i.item_name
        ).current_stock
        == 200
    )


def test_provider_failure_can_retry_same_persisted_event(app_client, monkeypatch):
    from app.pipeline import PipelineService
    from app.services.input_processor import InputProcessor
    from app.services.validator import BusinessValidator

    client, db, merchant, other, phone, second, sent, root = app_client
    provider_calls = []

    def interpret(**kwargs):
        provider_calls.append(kwargs)
        if len(provider_calls) == 1:
            raise RuntimeError("provider-unavailable")
        return ExtractionResponse(transactions=[sale()])

    pipeline = PipelineService.__new__(PipelineService)
    pipeline.input_processor = InputProcessor()
    pipeline.validator = BusinessValidator()
    pipeline.interpreter = SimpleNamespace(interpret=interpret)
    monkeypatch.setattr(orchestrator, "pipeline", pipeline)
    text(client, phone, "Sold 50 shirts", "retry-source")
    assert not sent
    text(client, phone, "Sold 50 shirts", "retry-source")
    assert sent[-1].buttons and len(provider_calls) == 2
    text(client, phone, "Sold 50 shirts", "retry-source")
    assert len(provider_calls) == 2
    assert ledger.get_daily_summary(merchant).sales_total == 0
