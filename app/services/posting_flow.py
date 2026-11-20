"""Document side effects run only after the ledger commit has returned."""
import logging
from config import settings
from app.services import ledger
from pdf_service import InvoiceService, LocalArtifactStore
from pdf_service.firestore_repository import FirestoreInvoiceRepository
from pdf_service.gcs_store import GCSArtifactStore

logger = logging.getLogger(__name__)
_invoice_service = None
_url_resolver = None


def configure_posting_documents(service, url_resolver=None):
    global _invoice_service, _url_resolver
    _invoice_service, _url_resolver = service, url_resolver


def generate_posting_document(merchant, posting):
    global _invoice_service, _url_resolver
    if posting.posting_status != "COMMITTED":
        raise ValueError("Document generation requires a committed result")
    try:
        tx = ledger.get_transaction(merchant.merchant_id, posting.transaction_id)
        if tx.get("status") != "COMMITTED" or tx.get("merchant_id") != merchant.merchant_id:
            raise ValueError("Committed transaction unavailable")
        if tx.get("intent") != "SALE_INVOICE":
            return {"status": "NOT_APPLICABLE"}
        if _invoice_service is None:
            if settings.INVOICE_LOCAL_ROOT:
                store = LocalArtifactStore(settings.INVOICE_LOCAL_ROOT)
            else:
                store = GCSArtifactStore(settings.GCS_BUCKET_NAME)
                _url_resolver = store.document_url
            _invoice_service = InvoiceService(FirestoreInvoiceRepository(), store)
        artifact = _invoice_service.generate_document(merchant, posting.transaction_id)
        outcome = {"status": "GENERATED", "filename": artifact.filename,
                   "transaction_id": artifact.transaction_id}
        if _url_resolver:
            outcome["document_url"] = _url_resolver(artifact)
        return outcome
    except Exception as exc:
        # Never undo a commit or expose credential/storage exceptions to callers.
        logger.warning("Post-commit invoice requires retry (%s)", type(exc).__name__)
        return {"status": "PENDING_RETRY", "transaction_id": posting.transaction_id}
