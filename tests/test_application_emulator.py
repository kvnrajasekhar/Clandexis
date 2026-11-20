"""Real HTTP -> confirmation -> Firestore -> PDF -> outbound integration.

Firestore and document rendering are real. Only Meta network delivery is recorded.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

if not os.getenv("FIRESTORE_EMULATOR_HOST"):
    pytest.skip("FIRESTORE_EMULATOR_HOST required", allow_module_level=True)

from app.services import ledger
from app.services.confirmation import AuthenticatedMerchant, get_confirmation_service, button_id
from app.services import posting_flow
from pdf_service import InvoiceService, LocalArtifactStore
from pdf_service.firestore_repository import FirestoreInvoiceRepository
from models.canonical import MessageType
from tests.test_ledger_emulator import db, mid, sale, collection_cand, count, col, stock, unstated_sale, seed_party
from tests.auth_helpers import configure_demo, sign_in, SEND_TOKEN, META_SECRET, PASSWORD, SESSION_SECRET
import webhook_service.main as application
from webhook_service.meta_whatsapp_handler import MetaWhatsAppService


class MetaRecorder(MetaWhatsAppService):
    def __init__(self):
        self.sent = []
        self.phone_number_id = "phone"

    async def close(self):
        pass

    async def dispatch_outbound(self, outbound):
        self.sent.append(outbound)
        return type("Sent", (), {"messages": [{"id": "test-outgoing"}]})()


@pytest.fixture
def app_client(db, mid, monkeypatch, tmp_path):
    db.collection("merchants").document(mid).update({"authorized_phones": [mid]})
    configure_demo(monkeypatch, mid)
    monkeypatch.setattr(ledger, "_client", db)
    recorder = MetaRecorder()
    monkeypatch.setattr(application, "MetaWhatsAppService", lambda: recorder)
    # Real Firestore association plus actual ReportLab bytes; only URL delivery is local.
    service = InvoiceService(FirestoreInvoiceRepository(db, ledger), LocalArtifactStore(tmp_path))
    monkeypatch.setattr(posting_flow, "_invoice_service", service)
    monkeypatch.setattr(posting_flow, "_url_resolver", lambda artifact: "https://test.invalid/" + artifact.storage_key)
    with TestClient(application.app, raise_server_exceptions=False) as client:
        yield client, recorder, tmp_path


def callback(client, mid, candidate_id, event="wamid.tap"):
    payload = {"object": "whatsapp_business_account", "entry": [{"id": "waba", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"display_phone_number": "15550000000", "phone_number_id": "phone"},
        "messages": [{"from": mid.lstrip("+"), "id": event, "timestamp": "1795132800", "type": "interactive",
                      "interactive": {"type": "button_reply", "button_reply": {
                          "id": button_id(candidate_id, "confirm"), "title": "Confirm"}}}]
    }}]}]}
    return signed_callback(client, payload)


def signed_callback(client, payload):
    body = json.dumps(payload).encode()
    signature = hmac.new(META_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/webhook", content=body, headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=" + signature})


def test_webhook_sale_commit_invoice_and_duplicate(app_client, db, mid):
    client, sender, root = app_client
    row = get_confirmation_service().stage(AuthenticatedMerchant(mid), "wamid.sale", 0, sale())
    assert count(db, mid, "transactions") == 0 and not list(root.rglob("*.pdf"))
    assert callback(client, mid, row.candidate_id).status_code == 200
    assert count(db, mid, "transactions") == 1
    assert stock(db, mid) == 250
    assert "COMMITTED" in sender.sent[0].text_content
    assert "7000.00" in sender.sent[0].text_content and "3000.00" in sender.sent[0].text_content
    assert sender.sent[1].message_type == MessageType.DOCUMENT
    pdfs = list(root.rglob("*.pdf"))
    assert len(pdfs) == 1
    from pypdf import PdfReader
    text = "\n".join(p.extract_text() for p in PdfReader(pdfs[0]).pages)
    assert "INR 10,000.00" in text and "INR 7,000.00" in text and "INR 3,000.00" in text
    tx = next(col(db, mid, "transactions").stream())
    assert tx.reference.collection("invoice_artifacts").document("basic_v1").get().exists
    assert callback(client, mid, row.candidate_id).status_code == 200
    assert callback(client, mid, row.candidate_id, "wamid.second-tap").status_code == 200
    assert count(db, mid, "transactions") == 1 and len(list(root.rglob("*.pdf"))) == 1


def test_repayment_no_sale_invoice_or_stock_movement(app_client, db, mid):
    client, sender, root = app_client
    seed_party(db, mid, "ramesh-textiles", "Ramesh Textiles", ["Ramesh"], 5000)
    ledger.post_opening_balance(mid, "ramesh-textiles", posted_by=mid, db=db)
    row = get_confirmation_service().stage(AuthenticatedMerchant(mid), "wamid.repayment", 0, collection_cand())
    assert callback(client, mid, row.candidate_id).status_code == 200
    assert count(db, mid, "transactions") == 1 and stock(db, mid) == 300
    assert ledger.get_party_balance(mid, "ramesh-textiles", db=db).outstanding == 3000
    assert not list(root.rglob("*.pdf"))
    assert len(sender.sent) == 1 and "COMMITTED" in sender.sent[0].text_content


def test_review_cannot_post_until_resolved(app_client, db, mid):
    client, sender, root = app_client
    merchant = AuthenticatedMerchant(mid)
    service = get_confirmation_service()
    row = service.stage(merchant, "wamid.review", 0, unstated_sale())
    callback(client, mid, row.candidate_id)
    assert count(db, mid, "transactions") == 0 and not list(root.rglob("*.pdf"))
    assert all(message.message_type != MessageType.DOCUMENT for message in sender.sent)
    service.resolve_review(merchant, row.candidate_id, sale(), "Merchant verified 7000 cash, 3000 credit")
    callback(client, mid, row.candidate_id, "wamid.reviewed")
    assert count(db, mid, "transactions") == 1 and len(list(root.rglob("*.pdf"))) == 1


def test_dashboard_identity_csrf_and_foreign_candidate(app_client, db, mid):
    client, _, _ = app_client
    for path in ("summary", "inventory", "parties", "transactions", "candidates"):
        assert client.get(f"/api/v1/dashboard/{path}").status_code == 401
    payload = {"merchant_id": mid, "confirmed_by": mid, "candidate_id": "cand_fake"}
    for action in ("confirm", "cancel"):
        assert client.post(f"/api/v1/dashboard/candidates/{action}", json=payload).status_code == 401
    sign_in(client)
    assert client.get("/api/v1/dashboard/summary").status_code == 200
    assert client.get("/api/v1/dashboard/summary", params={"merchant_id": "other"}).status_code == 403
    row = get_confirmation_service().stage(AuthenticatedMerchant(mid), "wamid.dashboard", 0, sale())
    payload["candidate_id"] = row.candidate_id
    assert client.post("/api/v1/dashboard/candidates/confirm", json=payload,
                       headers={"X-CSRF-Token": "invalid"}).status_code == 403
    assert client.post("/api/v1/dashboard/candidates/confirm", json=payload | {"confirmed_by": "other"}).status_code == 403
    assert client.post("/api/v1/dashboard/candidates/confirm", json=payload | {"merchant_id": "other"}).status_code == 403
    assert count(db, mid, "transactions") == 0
    response = client.post("/api/v1/dashboard/candidates/confirm", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["posting_status"] == "COMMITTED"
    assert response.json()["invoice"]["status"] == "GENERATED"


def test_webhook_hmac_send_and_no_secret_leak(app_client, caplog, monkeypatch):
    client, _, _ = app_client
    assert client.post("/webhook", json={}).status_code == 403
    assert client.post("/webhook", content=b"{}", headers={"X-Hub-Signature-256": "sha256=forged"}).status_code == 403
    monkeypatch.setattr(application.settings, "META_APP_SECRET", None)
    assert client.post("/webhook", json={}).status_code == 403
    body = {"recipient_phone": "+919876543210", "text_content": "hello"}
    assert client.post("/api/v1/send", json=body).status_code == 401
    assert client.post("/api/v1/send", json=body, headers={"Authorization": "Bearer " + SEND_TOKEN}).status_code == 200
    token = "verification-token-must-never-be-logged"
    response = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": token})
    assert response.status_code == 403
    # Ignore HTTPX's test-client request URL log; assert application logger never logs token.
    messages = " ".join(r.getMessage() for r in caplog.records if not r.name.startswith("httpx"))
    assert token not in messages
    response = client.post("/api/v1/auth/login", json={"username": "demo", "password": {"secret": PASSWORD}})
    assert response.status_code == 422
    assert all(secret not in response.text for secret in (PASSWORD, SESSION_SECRET, SEND_TOKEN, META_SECRET))


def test_invoice_failure_keeps_committed_sale(app_client, db, mid, monkeypatch):
    client, sender, _ = app_client
    class Broken:
        def generate_document(self, *args):
            raise RuntimeError("secret-credential-should-never-appear")
    monkeypatch.setattr(posting_flow, "_invoice_service", Broken())
    row = get_confirmation_service().stage(AuthenticatedMerchant(mid), "wamid.failure", 0, sale())
    response = callback(client, mid, row.candidate_id)
    assert response.status_code == 200 and count(db, mid, "transactions") == 1
    assert "PENDING_RETRY" in sender.sent[0].text_content
    assert "secret-credential" not in sender.sent[0].text_content


def test_signed_webhook_cannot_confirm_other_merchants_sale(app_client, db, mid):
    client, sender, root = app_client
    row = get_confirmation_service().stage(AuthenticatedMerchant(mid), "wamid.owner-sale", 0, sale())
    assert callback(client, "+910000000000", row.candidate_id).status_code == 200
    assert count(db, mid, "transactions") == 0 and not list(root.rglob("*.pdf"))
    assert sender.sent == []


def test_both_application_entrypoints_use_same_authenticated_bridge(app_client, db, mid):
    from app.main import create_app
    row = get_confirmation_service().stage(AuthenticatedMerchant(mid), "wamid.other-entrypoint", 0, sale())
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        assert client.get("/api/v1/dashboard/summary").status_code == 401
        sign_in(client)
        response = client.post("/api/v1/dashboard/candidates/confirm", json={"candidate_id": row.candidate_id})
        assert response.status_code == 200, response.text
        assert response.json()["posting_status"] == "COMMITTED"
        assert response.json()["invoice"]["status"] == "GENERATED"
    assert count(db, mid, "transactions") == 1


def test_dashboard_downloads_committed_invoice_and_rejects_foreign_access(app_client, mid):
    client, _, root = app_client
    sign_in(client)
    row = get_confirmation_service().stage(AuthenticatedMerchant(mid), "dashboard-sale", 0, sale())
    from config import settings
    from unittest.mock import patch
    with patch.object(settings, "INVOICE_LOCAL_ROOT", str(root)):
        posted = client.post("/api/v1/dashboard/candidates/confirm", json={"candidate_id":row.candidate_id})
        assert posted.status_code == 200
        url = "/api/v1/dashboard/transactions/" + posted.json()["transaction_id"] + "/invoice"
        download = client.get(url)
        assert download.status_code == 200 and download.content.startswith(b"%PDF-")
        assert client.get(url, params={"merchant_id":"other"}).status_code == 403
        client.cookies.clear()
        assert client.get(url).status_code == 401


def test_dashboard_review_revalidates_without_posting(app_client, db, mid):
    client, _, _ = app_client
    sign_in(client)
    row = get_confirmation_service().stage(AuthenticatedMerchant(mid), "review", 0, unstated_sale())
    url = "/api/v1/dashboard/candidates/" + row.candidate_id + "/review"
    response = client.post(url, json={"candidate":sale().model_dump(mode="json"), "note":"Checked payment against bill"})
    assert response.status_code == 200 and response.json()["review_resolved"]
    assert count(db, mid, "transactions") == 0


def test_browser_extraction_receipt_matches_persisted_candidate(app_client, db, mid, monkeypatch):
    """Fixture only at the model boundary. Validator, HTTP, staging and Firestore are real."""
    from app.api.v1 import endpoints
    from app.pipeline import PipelineService
    from app.schemas.transaction import ExtractionResponse
    from app.services.interpreter import InterpreterService

    monkeypatch.setattr(InterpreterService, "__init__", lambda self, model_name=None: None)
    monkeypatch.setattr(InterpreterService, "interpret", lambda self, **kw:
                        ExtractionResponse(transactions=[sale(cash=0, upi=2000, credit=8000)]))
    monkeypatch.setattr(endpoints, "pipeline", PipelineService())
    client, _, _ = app_client
    sign_in(client)
    payload = {"merchant_id": mid, "raw_text": "fixture sale", "stage": True}
    response = client.post("/api/v1/extract/text", json=payload)
    assert response.status_code == 200
    [receipt] = response.json()["staged_candidates"]
    row = col(db, mid, "transaction_candidates").document(receipt["candidate_id"]).get().to_dict()
    assert receipt["validation_status"] == row["validation_status"] == "ACCEPTED"
    assert receipt["posting_status"] == row["posting_status"] == "PENDING_CONFIRMATION"
    assert count(db, mid, "transactions") == 0
    assert row["source_channel"] == "dashboard"


def test_clarification_reply_is_scoped_to_candidate_revision(app_client, db, mid, monkeypatch):
    """Only Gemini is stubbed. A reply reviews the held candidate without posting."""
    from app.services.interpreter import InterpreterService
    from app.schemas.transaction import ExtractionResponse
    client, _, _ = app_client
    sign_in(client)
    row = get_confirmation_service().stage(AuthenticatedMerchant(mid), "partial-sale", 0, unstated_sale())
    monkeypatch.setattr(InterpreterService, "__init__", lambda self: None)
    monkeypatch.setattr(InterpreterService, "interpret", lambda self, raw_text:
                        ExtractionResponse(transactions=[sale()]))
    url = "/api/v1/dashboard/candidates/" + row.candidate_id + "/clarify"
    payload = {"answer": "Merchant clarified price and payment", "revision": row.revision}
    assert client.post(url, json=payload | {"revision": "stale"}).status_code == 409
    assert client.post(url + "?merchant_id=other", json=payload).status_code == 403
    response = client.post(url, json=payload)
    assert response.status_code == 200 and response.json()["posting_status"] == "PENDING_CONFIRMATION"
    assert response.json()["candidate_id"] == row.candidate_id
    assert client.post(url, json=payload).status_code == 409
    assert count(db, mid, "transactions") == 0


def test_mapped_voice_to_gemini_and_confirmation(app_client, db, mid, monkeypatch):
    from types import SimpleNamespace
    import httpx
    import agent_orchestrator.orchestrator as orchestrator
    from app.pipeline import PipelineService
    from app.services import interpreter

    client, meta, _ = app_client
    # Separate identities, even when the tenant ID itself resembles a phone number.
    phone = "+14155550123"
    ref = db.collection("merchants").document(mid)
    ref.update({"authorized_phones": [phone]})
    generated = []
    media = b"OggS fixture audio bytes"

    def generate_content(**request):
        generated.append(request["contents"][0].inline_data)
        return SimpleNamespace(text=json.dumps({"transactions": [sale().model_dump(mode="json")]}))

    monkeypatch.setattr(interpreter, "get_genai_client", lambda: SimpleNamespace(
        models=SimpleNamespace(generate_content=generate_content)))
    monkeypatch.setattr(orchestrator, "pipeline", PipelineService())

    requests = []
    def graph(req):
        requests.append(req.url.path)
        if req.url.path == "/v21.0/media42":
            return httpx.Response(200, json={"url": "https://cdn.test/audio"})
        return httpx.Response(200, content=media)

    meta.token = "test-token"
    meta.api_version = "v21.0"
    meta.graph_base_url = "https://graph.test"
    meta.http_client = httpx.AsyncClient(transport=httpx.MockTransport(graph))
    message = {"from": phone[1:], "id": "wamid.voice", "timestamp": "1795132800", "type": "audio",
               "audio": {"id": "media42", "mime_type": "audio/ogg; codecs=opus"}}
    value = {"messaging_product": "whatsapp", "metadata": {
        "display_phone_number": "15550000000", "phone_number_id": "phone"}, "messages": [message]}
    payload = {"object": "whatsapp_business_account", "entry": [
        {"id": "waba", "changes": [{"field": "messages", "value": value}]}]}
    other = db.collection("merchants").document("also-linked")
    other.set({"authorized_phones": [phone]})
    try:
        assert signed_callback(client, payload).json()["messages_received"] == 0
        assert not requests and not meta.sent
    finally:
        other.delete()
    assert signed_callback(client, payload).json()["messages_received"] == 1
    assert requests == ["/v21.0/media42", "/audio"]
    assert [(part.data, part.mime_type) for part in generated] == [(media, "audio/ogg")]
    assert len(meta.sent) == 1 and meta.sent[0].recipient_phone == phone
    cid = meta.sent[0].buttons[0].id.split(":")[-1]
    assert count(db, mid, "transactions") == 0
    assert callback(client, mid, cid).json()["messages_received"] == 0
    assert callback(client, phone, cid).json()["messages_received"] == 1
    tx, = col(db, mid, "transactions").stream()
    assert tx.to_dict()["confirmed_by"] == phone
    media = b""
    message["id"] = "wamid.empty"
    assert signed_callback(client, payload).json()["messages_received"] == 1
    assert "resend" in meta.sent[-1].text_content
    assert len(generated) == 1 and count(db, mid, "transaction_candidates") == 1
