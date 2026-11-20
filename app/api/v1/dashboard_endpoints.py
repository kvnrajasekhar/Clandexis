"""
Judge & Merchant Dashboard Read-Only and Staging API Endpoints.
Connects directly to the canonical Claude ledger (app/services/ledger.py)
and Codex confirmation service (app/services/confirmation.py)
without duplicating any ledger logic.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services import ledger

router = APIRouter(prefix="/dashboard", tags=["Judge & Merchant Dashboard"])

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
    merchant_id: str = Query("+919876543210", description="Merchant E.164 phone number"),
    business_date: Optional[str] = Query(None, description="Optional ISO date YYYY-MM-DD"),
):
    """
    Returns today's sales, cash, upi, bank receipts, credit issued, and collections.
    """
    try:
        summary = ledger.get_daily_summary(merchant_id=merchant_id, business_date=business_date)
        return _to_json_compatible(summary)
    except ledger.MerchantNotFoundError:
        raise HTTPException(status_code=404, detail=f"Merchant {merchant_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/inventory", summary="Get Live Inventory Levels")
def get_dashboard_inventory(
    merchant_id: str = Query("+919876543210", description="Merchant E.164 phone number"),
    sku_id: Optional[str] = Query(None, description="Optional SKU identifier"),
):
    """
    Returns live inventory stock, default unit price, and reorder levels.
    """
    try:
        items = ledger.get_inventory(merchant_id=merchant_id, sku_id=sku_id)
        return _to_json_compatible(items)
    except ledger.MerchantNotFoundError:
        raise HTTPException(status_code=404, detail=f"Merchant {merchant_id} not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/parties", summary="Get Counterparties and Credit Balances")
def get_dashboard_parties(
    merchant_id: str = Query("+919876543210", description="Merchant E.164 phone number"),
):
    """
    Returns customer and supplier balances from canonical Firestore state.
    """
    try:
        db = ledger.get_db()
        col = ledger._col(db, merchant_id, ledger.COL_PARTIES)
        parties = []
        for doc in col.stream():
            d = doc.to_dict()
            bal_minor = d.get("current_udhaar_balance_minor")
            if bal_minor is not None:
                bal = float(ledger.from_minor(bal_minor))
            else:
                bal = float(d.get("current_udhaar_balance", 0.0))
            parties.append({
                "party_id": doc.id,
                "display_name": d.get("display_name", doc.id),
                "phone": d.get("phone"),
                "current_udhaar_balance": bal,
                "credit_limit": float(d.get("credit_limit", 50000.0)),
            })
        return parties
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/transactions", summary="List Committed Transactions")
def get_dashboard_transactions(
    merchant_id: str = Query("+919876543210", description="Merchant E.164 phone number"),
    limit: int = Query(25, ge=1, le=100),
):
    """
    Lists committed transactions from canonical ledger.
    """
    try:
        txns = ledger.list_transactions(merchant_id=merchant_id, limit=limit)
        return _to_json_compatible(txns)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/candidates", summary="List Staged Candidates for Review")
def get_dashboard_candidates(
    merchant_id: str = Query("+919876543210", description="Merchant E.164 phone number"),
):
    """
    Lists transaction candidates awaiting confirmation or held in review.
    """
    try:
        db = ledger.get_db()
        col = db.collection("merchants").document(merchant_id).collection("transaction_candidates")
        candidates = []
        for doc in col.order_by("created_at", direction=ledger.firestore.Query.DESCENDING).limit(30).stream():
            d = doc.to_dict()
            candidates.append({
                "candidate_id": doc.id,
                "source_message_id": d.get("source_message_id"),
                "posting_status": d.get("posting_status"),
                "validation_status": d.get("validation_status"),
                "hold_source": d.get("hold_source"),
                "hold_reasons": d.get("hold_reasons", []),
                "validation_warnings": d.get("validation_warnings", []),
                "extracted_snapshot": d.get("extracted_snapshot"),
                "created_at": d.get("created_at").isoformat() if d.get("created_at") else None,
                "expires_at": d.get("expires_at").isoformat() if d.get("expires_at") else None,
            })
        return candidates
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class ActionCandidateRequest(BaseModel):
    merchant_id: str
    candidate_id: str
    confirmed_by: str = "+919876543210"
    reason: Optional[str] = None


@router.post("/candidates/confirm", summary="Authorize & Commit a Staged Candidate")
def confirm_dashboard_candidate(payload: ActionCandidateRequest):
    """
    Executes confirm_and_commit from dashboard UI with authorization check.
    """
    try:
        res = ledger.confirm_and_commit(
            merchant_id=payload.merchant_id,
            candidate_id=payload.candidate_id,
            confirmed_by=payload.confirmed_by,
        )
        return _to_json_compatible(res)
    except ledger.LedgerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/candidates/cancel", summary="Cancel a Staged Candidate")
def cancel_dashboard_candidate(payload: ActionCandidateRequest):
    """
    Cancels a candidate in PENDING_CONFIRMATION or NEEDS_REVIEW.
    """
    try:
        res = ledger.cancel_candidate(
            merchant_id=payload.merchant_id,
            candidate_id=payload.candidate_id,
            cancelled_by=payload.confirmed_by,
            reason=payload.reason or "Cancelled from dashboard",
        )
        return _to_json_compatible(res)
    except ledger.LedgerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
