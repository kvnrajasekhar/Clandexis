"""Contract stub only. Not a Firestore ledger implementation or proof of its ACID behavior."""
from dataclasses import replace
from decimal import Decimal
from threading import RLock

from app.services.confirmation import ConfirmationError, StoredCandidate, PostingResult


class LedgerStub:
    def __init__(self):
        self.rows = {}
        self.staged = {}
        self.results = {}
        self.post_count = 0
        self.lock = RLock()

    def stage_candidate(self, merchant_id, source_event_id, index, candidate, candidate_id, expires_at):
        key = merchant_id, source_event_id, index
        if key not in self.staged:
            self.staged[key] = candidate_id
            self.rows[candidate_id] = StoredCandidate(
                candidate_id, merchant_id, candidate, 1, expires_at,
                originally_needs_review=(candidate.validation_status == "NEEDS_REVIEW"
                                         or candidate.needs_clarification or candidate.requires_review))
        return self.get_candidate(merchant_id, self.staged[key])

    def get_candidate(self, merchant_id, candidate_id):
        row = self.rows.get(candidate_id)
        if row is None or row.merchant_id != merchant_id:
            raise ConfirmationError("Candidate unavailable for this merchant.")
        return replace(row, candidate=row.candidate.model_copy(deep=True))

    def active_candidates(self, merchant_id, now):
        return [r for r in self.rows.values() if r.merchant_id == merchant_id
                and r.status == "AWAITING_CONFIRMATION" and r.expires_at > now]

    def _pending(self, merchant_id, candidate_id, expected_revision, now):
        row = self.get_candidate(merchant_id, candidate_id)
        if row.status != "AWAITING_CONFIRMATION" or row.expires_at <= now or row.revision != expected_revision:
            raise ConfirmationError("Expired, terminal or stale candidate")
        return row

    def replace_reviewed_candidate(self, merchant_id, candidate_id, expected_revision, candidate, review_note, now):
        row = self._pending(merchant_id, candidate_id, expected_revision, now)
        row = replace(row, candidate=candidate, revision=row.revision + 1, review_resolved=True)
        self.rows[candidate_id] = row
        return row

    def confirm_candidate(self, merchant_id, candidate_id, source_event_id, expected_revision, now, *, actor_phone=None):
        with self.lock:
            row = self.get_candidate(merchant_id, candidate_id)
            if row.status == "COMMITTED":
                return self.results[candidate_id]
            self._pending(merchant_id, candidate_id, expected_revision, now)
            if row.candidate.validation_status != "ACCEPTED" or (row.originally_needs_review and not row.review_resolved):
                raise ConfirmationError("Not validated")
            self.post_count += 1
            # Deliberately different from candidate amounts: response must use persisted results.
            result = PostingResult("txn_db_1", Decimal("73.00"), Decimal("27.00"), {"shirt": Decimal("18")})
            self.results[candidate_id] = result
            self.rows[candidate_id] = replace(row, status="COMMITTED", revision=row.revision + 1)
            return result

    def cancel_candidate(self, merchant_id, candidate_id, source_event_id, expected_revision, now, *, actor_phone=None):
        with self.lock:
            row = self.get_candidate(merchant_id, candidate_id)
            if row.status == "CANCELLED":
                return "CANCELLED"
            self._pending(merchant_id, candidate_id, expected_revision, now)
            self.rows[candidate_id] = replace(row, status="CANCELLED", revision=row.revision + 1)
            return "CANCELLED"
