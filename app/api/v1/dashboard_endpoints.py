"""
Judge & Merchant Dashboard Read-Only and Staging API Endpoints.
Connects directly to the canonical Claude ledger (app/services/ledger.py)
and Codex confirmation service (app/services/confirmation.py)
without duplicating any ledger logic.
"""

from decimal import Decimal
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.persistence import ledger
from app.core.security import SessionIdentity, dashboard_identity
from app.services.confirmation import AuthenticatedMerchant, ConfirmationError, handle_confirmation
from app.services.posting_flow import generate_posting_document
from uuid import uuid4
from app.core.observability import unavailable

router = APIRouter(prefix="/dashboard", tags=["Judge & Merchant Dashboard"], dependencies=[Depends(dashboard_identity)])
logger = logging.getLogger(__name__)

def _to_json_compatible(obj: Any) -> Any:
    """Recursively convert Decimals and dataclasses to JSON-serializable primitives."""
    if isinstance(obj, Decimal):
        return float(obj)
    if hasattr(obj, "__dict__"):
        return {k: _to_json_compatible(v) for k, v in obj.__dict__.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_to_json_compatible(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _to_json_compatible(v) for k, v in obj.items()}
    return obj


@router.get("/summary", summary="Get Daily Business Pulse")
def get_dashboard_summary(
    merchant_id: Optional[str] = Query(None),
    identity: SessionIdentity = Depends(dashboard_identity),
    business_date: Optional[str] = Query(None, description="Optional ISO date YYYY-MM-DD"),
):
    """
    Returns today's sales, cash, upi, bank receipts, credit issued, and collections.
    """
    merchant_id = identity.merchant_id
    try:
        summary = ledger.get_daily_summary(merchant_id=merchant_id, business_date=business_date)
        return _to_json_compatible(summary)
    except ledger.MerchantNotFoundError:
        raise HTTPException(status_code=404, detail="Merchant not found")
    except Exception as exc:
        raise unavailable(logger, "dashboard.summary", exc, "Dashboard operation failed") from None


@router.get("/inventory", summary="Get Live Inventory Levels")
def get_dashboard_inventory(
    merchant_id: Optional[str] = Query(None),
    identity: SessionIdentity = Depends(dashboard_identity),
    sku_id: Optional[str] = Query(None, description="Optional SKU identifier"),
):
    """
    Returns live inventory stock, default unit price, and reorder levels.
    """
    merchant_id = identity.merchant_id
    try:
        items = ledger.get_inventory(merchant_id=merchant_id, sku_id=sku_id)
        return _to_json_compatible(items)
    except ledger.MerchantNotFoundError:
        raise HTTPException(status_code=404, detail="Merchant not found")
    except Exception as exc:
        raise unavailable(logger, "dashboard.inventory", exc, "Dashboard operation failed") from None


@router.get("/parties", summary="Get Counterparties and Credit Balances")
def get_dashboard_parties(
    merchant_id: Optional[str] = Query(None),
    identity: SessionIdentity = Depends(dashboard_identity),
):
    """
    Returns customer and supplier balances from canonical Firestore state.
    """
    merchant_id = identity.merchant_id
    try:
        return _to_json_compatible(ledger.list_parties(merchant_id))
    except Exception as exc:
        raise unavailable(logger, "dashboard.parties", exc, "Dashboard operation failed") from None


@router.get("/transactions", summary="List Committed Transactions")
def get_dashboard_transactions(
    merchant_id: Optional[str] = Query(None),
    identity: SessionIdentity = Depends(dashboard_identity),
    limit: int = Query(25, ge=1, le=100),
):
    """
    Lists committed transactions from canonical ledger.
    """
    merchant_id = identity.merchant_id
    try:
        txns = ledger.list_transactions(merchant_id=merchant_id, limit=limit)
        return _to_json_compatible(txns)
    except Exception as exc:
        raise unavailable(logger, "dashboard.transactions", exc, "Dashboard operation failed") from None


@router.get("/candidates", summary="List Staged Candidates for Review")
def get_dashboard_candidates(
    merchant_id: Optional[str] = Query(None),
    identity: SessionIdentity = Depends(dashboard_identity),
):
    """
    Lists transaction candidates awaiting confirmation or held in review.
    """
    merchant_id = identity.merchant_id
    try:
        candidates = ledger.list_candidates(merchant_id)
        for row in candidates:
            row["revision"] = row.get("snapshot_sha256")
            for key in ("created_at", "expires_at"):
                if row.get(key):
                    row[key] = row[key].isoformat()
        return _to_json_compatible(candidates)
    except Exception as exc:
        raise unavailable(logger, "dashboard.candidates", exc, "Dashboard operation failed") from None


class ActionCandidateRequest(BaseModel):
    merchant_id: Optional[str] = None  # Compatibility check only; never authorizes.
    candidate_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    confirmed_by: Optional[str] = None  # Deprecated; identity comes from session.
    source_event_id: Optional[str] = Field(default=None, max_length=256)
    reason: Optional[str] = None


def _action(payload, identity, action):
    if ((payload.merchant_id is not None and payload.merchant_id != identity.merchant_id)
            or (payload.confirmed_by is not None and payload.confirmed_by != identity.merchant_id)):
        raise HTTPException(403, "Merchant access denied")
    merchant = AuthenticatedMerchant(identity.merchant_id)
    try:
        result = handle_confirmation(merchant, payload.candidate_id, action,
            payload.source_event_id or f"dashboard:{uuid4().hex}")
        if action == "cancel":
            return {"posting_status": result}
        response = _to_json_compatible(result)
        response["txn_id"] = result.transaction_id  # Existing dashboard display contract.
        response["invoice"] = generate_posting_document(merchant, result)
        return response
    except ConfirmationError:
        raise HTTPException(409, "Candidate unavailable, expired or requires review")
    except Exception as exc:
        raise unavailable(logger, f"dashboard.candidate_{action}", exc,
                          "Dashboard operation failed") from None


@router.post("/candidates/confirm", summary="Authorize & Commit a Staged Candidate")
def confirm_dashboard_candidate(payload: ActionCandidateRequest,
                                identity: SessionIdentity = Depends(dashboard_identity)):
    return _action(payload, identity, "confirm")


@router.post("/candidates/cancel", summary="Cancel a Staged Candidate")
def cancel_dashboard_candidate(payload: ActionCandidateRequest,
                               identity: SessionIdentity = Depends(dashboard_identity)):
    return _action(payload, identity, "cancel")

from app.schemas.transaction import TransactionCandidate
from app.services.confirmation import get_confirmation_service
from fastapi.responses import FileResponse, RedirectResponse
from pathlib import Path
from config import settings
from pdf_service import InvoiceArtifact
from pdf_service.gcs_store import GCSArtifactStore


class ReviewRequest(BaseModel):
    candidate: TransactionCandidate
    note: str = Field(min_length=1)


class ClarificationReply(BaseModel):
    answer: str = Field(min_length=1, max_length=1000)
    revision: str = Field(min_length=1)


@router.post("/candidates/{candidate_id}/clarify")
def clarify_candidate(candidate_id: str, reply: ClarificationReply,
                      identity: SessionIdentity = Depends(dashboard_identity)):
    from app.services.interpreter import InterpreterService
    from app.services.validator import BusinessValidator

    merchant_id = identity.merchant_id
    try:
        document = ledger.get_candidate_record(merchant_id, candidate_id)
    except (ledger.LedgerError, ValueError):
        raise HTTPException(404, "Candidate unavailable")
    if not document or document.get("merchant_id") != merchant_id:
        raise HTTPException(404, "Candidate unavailable")
    if document["posting_status"] != "NEEDS_REVIEW" or document["snapshot_sha256"] != reply.revision:
        raise HTTPException(409, "Candidate changed. Refresh before replying.")
    current = TransactionCandidate.model_validate(document["extracted_snapshot"])
    facts = current.model_dump_json(exclude={"validation_status", "validation_warnings", "requires_review"})
    prompt = (f"Update the same transaction with the merchant's reply. Retain known facts. "
              f"Ask for any remaining missing price or payment details in the merchant's language and script. "
              f"Use Latin transcription for party names and English product names for catalog lookup. "
              f"Only derive amounts from stated facts. Leave missing prices and payments null. "
              f"Current facts: {facts}\nMerchant reply: {reply.answer}")
    candidates = InterpreterService().interpret(raw_text=prompt).transactions
    if len(candidates) != 1 or candidates[0].transaction_type != current.transaction_type:
        raise HTTPException(422, "Reply must clarify this transaction.")
    corrected = BusinessValidator.validate_candidate(candidates[0])
    try:
        result = ledger.review_candidate(merchant_id, candidate_id, reviewed_by=merchant_id,
            corrected_candidate=corrected, review_note=reply.answer, expected_snapshot_sha256=reply.revision)
    except ledger.LedgerError as exc:
        raise HTTPException(409, "Candidate unavailable or details inconsistent. Refresh and check the amounts.") from exc
    return {"candidate_id": result.candidate_id, "posting_status": result.posting_status,
            "question": corrected.clarification_question}


@router.post("/candidates/{candidate_id}/review")
def review_candidate(candidate_id: str, payload: ReviewRequest,
                     identity: SessionIdentity = Depends(dashboard_identity)):
    try:
        row = get_confirmation_service().resolve_review(AuthenticatedMerchant(identity.merchant_id),
            candidate_id, payload.candidate, payload.note)
    except ConfirmationError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"candidate_id": row.candidate_id, "review_resolved": row.review_resolved}


@router.get("/transactions/{txn_id}/invoice")
def download_invoice(txn_id: str, identity: SessionIdentity = Depends(dashboard_identity)):
    from app.services.persistence import get_invoice_artifact
    try:
        tx = ledger.get_transaction(identity.merchant_id, txn_id)
        if tx.get("intent") != "SALE_INVOICE":
            raise ValueError("Invoice requires sale")
        artifact = get_invoice_artifact(identity.merchant_id, txn_id)
        if artifact is None:
            raise ValueError("Invoice unavailable")
    except (ledger.LedgerError, ValueError):
        raise HTTPException(404, "Invoice unavailable")
    if artifact.merchant_id != identity.merchant_id or artifact.transaction_id != txn_id:
        raise HTTPException(404, "Invoice unavailable")
    if settings.INVOICE_LOCAL_ROOT:
        root = Path(settings.INVOICE_LOCAL_ROOT).resolve()
        path = (root / artifact.storage_key).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(404, "Invoice unavailable")
        return FileResponse(path, media_type="application/pdf", filename=artifact.filename)
    return RedirectResponse(GCSArtifactStore(settings.GCS_BUCKET_NAME).document_url(artifact))
