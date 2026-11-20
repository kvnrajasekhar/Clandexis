"""Translate the existing Claude ledger API; never implement ledger writes here."""
from datetime import datetime, timezone
from decimal import Decimal
from app.schemas.transaction import TransactionCandidate
from app.services.confirmation import ConfirmationError, PostingResult, StoredCandidate


class ClaudeLedgerAdapter:
    def __init__(self, db=None, ledger_module=None):
        if ledger_module is None:
            from app.services import ledger as ledger_module
        self.api = ledger_module
        self.db = db if db is not None else self.api.get_db()

    def _collection(self, merchant_id):
        # Values used as individual Firestore path segments, never as paths.
        if not merchant_id or "/" in merchant_id:
            raise ConfirmationError("Invalid merchant.")
        return self.db.collection("merchants").document(merchant_id).collection("transaction_candidates")

    def _read(self, merchant_id, candidate_id):
        if not candidate_id or "/" in candidate_id:
            raise ConfirmationError("Invalid candidate ID.")
        snap = self._collection(merchant_id).document(candidate_id).get()
        d = snap.to_dict() if snap.exists else None
        if not d or d.get("merchant_id") != merchant_id:
            raise ConfirmationError("Candidate unavailable for this merchant.")
        return d

    def get_candidate(self, merchant_id, candidate_id):
        d = self._read(merchant_id, candidate_id)
        tx = TransactionCandidate.model_validate(d["extracted_snapshot"])
        status = d["posting_status"]
        mapped = {"PENDING_CONFIRMATION": "AWAITING_CONFIRMATION", "NEEDS_REVIEW": "AWAITING_CONFIRMATION",
                  "POSTED": "COMMITTED"}.get(status, status)
        if status == "NEEDS_REVIEW":
            tx.validation_status = "NEEDS_REVIEW"
        return StoredCandidate(
            candidate_id, merchant_id, tx, d["snapshot_sha256"],
            d["expires_at"], mapped, d.get("review_resolved", False),
            d.get("originally_needs_review", False) or d.get("validation_status") == "NEEDS_REVIEW"
            or tx.needs_clarification or tx.requires_review)

    def stage_candidate(self, merchant_id, source_event_id, index, candidate, candidate_id, expires_at):
        now = datetime.now(timezone.utc)
        try:
            result = self.api.stage_candidate(merchant_id, source_event_id, candidate,
                candidate_index=index, ttl=expires_at - now, db=self.db, now=now)
        except self.api.LedgerError as exc:
            raise ConfirmationError(str(exc)) from exc
        return self.get_candidate(merchant_id, result.candidate_id)

    def active_candidates(self, merchant_id, now):
        # Read-only fallback. Ambiguity fails closed; no mutable process-local pointer.
        rows = []
        for snap in self._collection(merchant_id).stream():
            row = self.get_candidate(merchant_id, snap.id)
            if row.status == "AWAITING_CONFIRMATION" and row.expires_at > now:
                rows.append(row)
        return rows

    def replace_reviewed_candidate(self, merchant_id, candidate_id, expected_revision,
                                   candidate, review_note, now):
        # Claude owns candidate lifecycle and must provide this atomic extension.
        review = getattr(self.api, "review_candidate", None)
        if review is None:
            raise ConfirmationError("Ledger review support is unavailable. Submit corrected details as a new candidate.")
        try:
            review(merchant_id, candidate_id, reviewed_by=merchant_id,
                   corrected_candidate=candidate, review_note=review_note,
                   expected_snapshot_sha256=expected_revision, db=self.db, now=now)
        except self.api.LedgerError as exc:
            raise ConfirmationError(str(exc)) from exc
        return self.get_candidate(merchant_id, candidate_id)

    def confirm_candidate(self, merchant_id, candidate_id, source_event_id, expected_revision, now):
        try:
            result = self.api.confirm_and_commit(
                merchant_id, candidate_id, confirmed_by=merchant_id,
                confirmation_request_id=source_event_id,
                expected_snapshot_sha256=expected_revision, db=self.db, now=now)
        except self.api.LedgerError as exc:
            raise ConfirmationError(str(exc)) from exc
        if result.merchant_id != merchant_id or result.status != "COMMITTED":
            raise RuntimeError("Ledger returned an invalid commit result")
        # Read persisted movements referenced by this transaction. This gives stock
        # at posting, including on replay, rather than a later live stock balance.
        tx = self.api.get_transaction(merchant_id, result.txn_id, db=self.db)
        balances = {}
        for movement_id in tx.get("movement_ids", []):
            movement = self.db.collection("merchants").document(merchant_id).collection(
                "inventory_movements").document(movement_id).get()
            if not movement.exists:
                raise RuntimeError("Committed inventory movement is missing")
            row = movement.to_dict()
            if row.get("reference_txn_id") != result.txn_id:
                raise RuntimeError("Inventory movement transaction mismatch")
            balances[row["sku_id"]] = Decimal(str(row["balance_after"]))
        return PostingResult(result.txn_id, result.received_amount, result.credit_amount, balances)

    def cancel_candidate(self, merchant_id, candidate_id, source_event_id, expected_revision, now):
        try:
            result = self.api.cancel_candidate(merchant_id, candidate_id, cancelled_by=merchant_id,
                                               reason=f"Callback {source_event_id}", db=self.db, now=now)
        except self.api.LedgerError as exc:
            raise ConfirmationError(str(exc)) from exc
        return result.posting_status.value
