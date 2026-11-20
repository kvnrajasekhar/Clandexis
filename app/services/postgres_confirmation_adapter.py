"""Existing confirmation protocol backed by the PostgreSQL ledger API."""

import psycopg
from app.schemas.transaction import TransactionCandidate
from app.services.confirmation import ConfirmationError, StoredCandidate, PostingResult
from app.services import postgres_ledger as ledger


class PostgresLedgerAdapter:
    def __init__(self, db=None):
        self.db = db or ledger.get_db()

    def get_candidate(self, merchant_id, candidate_id):
        try:
            r = ledger.get_candidate_record(merchant_id, candidate_id, db=self.db)
        except (ledger.LedgerError, ValueError, psycopg.Error) as exc:
            raise ConfirmationError("Candidate unavailable for this merchant") from exc
        status = {
            "PENDING_CONFIRMATION": "AWAITING_CONFIRMATION",
            "NEEDS_REVIEW": "AWAITING_CONFIRMATION",
            "POSTED": "COMMITTED",
        }.get(r["posting_status"], r["posting_status"])
        candidate = TransactionCandidate.model_validate(r["extracted_snapshot"])
        if r["posting_status"] == "NEEDS_REVIEW":
            candidate.validation_status = "NEEDS_REVIEW"
            candidate.requires_review = True
        return StoredCandidate(
            candidate_id,
            merchant_id,
            candidate,
            r["snapshot_sha256"],
            r["expires_at"],
            status,
            r["review_resolved"],
            r["originally_needs_review"],
        )

    def stage_candidate(
        self, merchant_id, source_event_id, index, candidate, candidate_id, expires_at
    ):
        from datetime import datetime, timezone

        row = ledger.stage_candidate(
            merchant_id,
            source_event_id,
            candidate,
            candidate_index=index,
            ttl=expires_at - datetime.now(timezone.utc),
            db=self.db,
        )
        return self.get_candidate(merchant_id, row.candidate_id)

    def active_candidates(self, merchant_id, now):
        return [
            self.get_candidate(merchant_id, r["candidate_id"])
            for r in ledger.list_candidates(merchant_id, db=self.db)
            if r["posting_status"] in ("PENDING_CONFIRMATION", "NEEDS_REVIEW")
            and r["expires_at"] > now
        ]

    def confirmation_holds(self, merchant_id, candidate_id, now):
        try:
            return ledger.confirmation_holds(
                merchant_id, candidate_id, db=self.db, now=now
            )
        except (ledger.LedgerError, psycopg.Error) as exc:
            raise ConfirmationError("Candidate unavailable or requires review") from exc

    def replace_reviewed_candidate(
        self, merchant_id, candidate_id, expected_revision, candidate, review_note, now
    ):
        try:
            ledger.review_candidate(
                merchant_id,
                candidate_id,
                reviewed_by=merchant_id,
                corrected_candidate=candidate,
                review_note=review_note,
                expected_snapshot_sha256=expected_revision,
                db=self.db,
                now=now,
            )
        except (ledger.LedgerError, psycopg.Error) as exc:
            raise ConfirmationError("Candidate changed or requires review") from exc
        return self.get_candidate(merchant_id, candidate_id)

    def confirm_candidate(
        self,
        merchant_id,
        candidate_id,
        source_event_id,
        expected_revision,
        now,
        *,
        actor_phone=None
    ):
        try:
            result = ledger.confirm_and_commit(
                merchant_id,
                candidate_id,
                confirmed_by=actor_phone or merchant_id,
                confirmation_request_id=source_event_id,
                expected_snapshot_sha256=expected_revision,
                db=self.db,
                now=now,
            )
            with self.db.transaction(merchant_id) as conn:
                payload = conn.execute(
                    "SELECT response_data FROM idempotency_keys WHERE merchant_id=%s AND operation='posting_result' AND request_id=%s",
                    (merchant_id, candidate_id),
                ).fetchone()["response_data"]
            return PostingResult(
                result.txn_id,
                result.received_amount,
                (
                    result.party_balance
                    if result.party_balance is not None
                    else result.credit_amount
                ),
                {
                    k: ledger.rules.to_qty(v)
                    for k, v in payload["inventory_balance"].items()
                },
            )
        except (ledger.LedgerError, psycopg.Error) as exc:
            raise ConfirmationError("Candidate unavailable or requires review") from exc

    def cancel_candidate(
        self,
        merchant_id,
        candidate_id,
        source_event_id,
        expected_revision,
        now,
        *,
        actor_phone=None
    ):
        try:
            return ledger.cancel_candidate(
                merchant_id,
                candidate_id,
                cancelled_by=actor_phone or merchant_id,
                confirmation_request_id=source_event_id,
                db=self.db,
                now=now,
            ).posting_status.value
        except (ledger.LedgerError, psycopg.Error) as exc:
            raise ConfirmationError("Candidate unavailable or requires review") from exc
