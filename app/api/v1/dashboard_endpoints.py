"""
Judge & Merchant Dashboard Read-Only and Staging API Endpoints.
Connects directly to the canonical Claude ledger (app/services/ledger.py)
and Codex confirmation service (app/services/confirmation.py)
without duplicating any ledger logic.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.services import ledger
from app.core.security import SessionIdentity, dashboard_identity
from app.services.confirmation import AuthenticatedMerchant, ConfirmationError, handle_confirmation
from app.services.posting_flow import generate_posting_document
from uuid import uuid4

router = APIRouter(prefix="/dashboard", tags=["Judge & Merchant Dashboard"], dependencies=[Depends(dashboard_identity)])

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
    except Exception as e:
        raise HTTPException(status_code=500, detail="Dashboard operation failed")


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
    except Exception as e:
        raise HTTPException(status_code=500, detail="Dashboard operation failed")


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
        db = ledger.get_db()
        col = ledger._col(db, merchant_id, ledger.COL_PARTIES)
        parties = []
        for doc in col.stream():
            d = doc.to_dict()
            bal_minor = d.get("udhaar_balance_minor")
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
        raise HTTPException(status_code=500, detail="Dashboard operation failed")


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
    except Exception as e:
        raise HTTPException(status_code=500, detail="Dashboard operation failed")


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
        raise HTTPException(status_code=500, detail="Dashboard operation failed")


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
    except Exception:
        raise HTTPException(500, "Dashboard operation failed")


@router.post("/candidates/confirm", summary="Authorize & Commit a Staged Candidate")
def confirm_dashboard_candidate(payload: ActionCandidateRequest,
                                identity: SessionIdentity = Depends(dashboard_identity)):
    return _action(payload, identity, "confirm")


@router.post("/candidates/cancel", summary="Cancel a Staged Candidate")
def cancel_dashboard_candidate(payload: ActionCandidateRequest,
                               identity: SessionIdentity = Depends(dashboard_identity)):
    return _action(payload, identity, "cancel")
