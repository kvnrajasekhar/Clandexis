"""PostgreSQL persistence for the existing canonical ledger plans and API.

Validation, catalog matching, decimal math and journal plans are reused from the
canonical ledger. Only tenant-scoped reads and atomic persistence differ here.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4, uuid5, NAMESPACE_URL
from psycopg.types.json import Jsonb
from app.schemas.transaction import TransactionCandidate
from app.services import ledger as rules
from app.services.postgres_db import PostgresDatabase

LedgerError = rules.LedgerError
MerchantNotFoundError = rules.MerchantNotFoundError
CandidateNotFoundError = rules.CandidateNotFoundError
TransactionNotFoundError = rules.TransactionNotFoundError
PostingStatus = rules.PostingStatus
from_minor = rules.from_minor


def get_db():
    return PostgresDatabase()


def _merchant(conn, merchant_id, actor=None, lock=False):
    suffix = " FOR UPDATE" if lock else ""
    row = conn.execute(
        "SELECT * FROM merchants WHERE id=%s AND is_approved" + suffix, (merchant_id,)
    ).fetchone()
    if not row:
        raise MerchantNotFoundError("Approved merchant unavailable")
    if actor is not None and actor not in (str(row["id"]), row["phone_e164"]):
        from app.services.whatsapp_identity import resolve_phone
        resolved = resolve_phone(actor)
        if resolved and str(resolved) == str(row["id"]):
            return row
        raise rules.NotAuthorizedError("Actor is not authorized for this merchant")
    return row


def _source(
    conn, merchant, event_id, channel="WHATSAPP", message_type="text", payload=None
):
    row = conn.execute(
        """INSERT INTO workflow_messages
        (merchant_id,sender_phone,source_channel,external_message_id,message_type,source_payload)
        VALUES(%s,%s,%s,%s,%s,%s)
        ON CONFLICT (merchant_id,source_channel,external_message_id) DO NOTHING RETURNING id""",
        (
            merchant["id"],
            merchant["phone_e164"],
            channel.upper(),
            event_id,
            message_type,
            Jsonb(payload or {}),
        ),
    ).fetchone()
    if row:
        return row["id"]
    return conn.execute(
        "SELECT id FROM workflow_messages WHERE merchant_id=%s AND source_channel=%s AND external_message_id=%s",
        (merchant["id"], channel.upper(), event_id),
    ).fetchone()["id"]


def _candidate(conn, merchant_id, candidate_id, lock=False):
    row = conn.execute(
        "SELECT * FROM transaction_candidates WHERE merchant_id=%s AND id=%s"
        + (" FOR UPDATE" if lock else ""),
        (merchant_id, UUID(str(candidate_id))),
    ).fetchone()
    if not row:
        raise CandidateNotFoundError("Candidate unavailable for this merchant")
    return row


def _candidate_record(row, source=None):
    snapshot = row["extracted_snapshot"]
    return {
        **row,
        "merchant_id": str(row["merchant_id"]),
        "candidate_id": str(row["id"]),
        "source_message_id": source or str(row["source_message_id"]),
        "hold_reasons": snapshot.get("validation_warnings", []),
        "validation_warnings": snapshot.get("validation_warnings", []),
    }


def _staged(row, source, already=False):
    return rules.StagedCandidate(
        candidate_id=str(row["id"]),
        merchant_id=str(row["merchant_id"]),
        source_message_id=source,
        validation_status=row["validation_status"],
        posting_status=row["posting_status"],
        validation_warnings=row["extracted_snapshot"].get("validation_warnings", []),
        already_staged=already,
        expires_at=row["expires_at"],
    )


def _assessed(candidate, merchant):
    supplied = TransactionCandidate.model_validate(candidate).model_copy(deep=True)
    tx = rules.BusinessValidator.validate_candidate(
        supplied.model_copy(deep=True), merchant.get("business_name")
    )
    tx.validation_status = max(
        supplied.validation_status,
        tx.validation_status,
        key=rules._SEVERITY.__getitem__,
    )
    status, warnings, posting, reasons = rules._assess(tx, merchant)
    tx.validation_status = status
    tx.validation_warnings = list(dict.fromkeys(warnings + reasons))
    tx.requires_review = posting in (PostingStatus.NEEDS_REVIEW, PostingStatus.REJECTED)
    if posting == PostingStatus.PENDING_CONFIRMATION:
        plan = rules._plan_from_snapshot(tx, datetime.now(rules.IST).date())
        if plan.kind != "stock_added":
            tx.total_amount = float(plan.total)
            tx.amount_paid = float(plan.received)
            tx.credit_amount = float(plan.credit)
    return tx, posting


def stage_candidate(
    merchant_id,
    source_message_id,
    candidate,
    *,
    candidate_index=0,
    ttl=timedelta(minutes=30),
    db=None,
    now=None,
    source_channel="WHATSAPP",
    source_payload=None,
    legacy_candidate_id=None,
    **kwargs,
):
    db = db or get_db()
    now = now or datetime.now(rules.IST)
    with db.transaction(merchant_id) as conn:
        merchant = _merchant(conn, merchant_id)
        source = _source(
            conn, merchant, source_message_id, source_channel, payload=source_payload
        )
        cid = uuid5(
            NAMESPACE_URL,
            f"clandexis:{merchant_id}:{source_channel.lower()}:{source_message_id}:{candidate_index}",
        )
        old = conn.execute(
            "SELECT * FROM transaction_candidates WHERE merchant_id=%s AND id=%s",
            (merchant_id, cid),
        ).fetchone()
        if old:
            return _staged(old, source_message_id, True)
        tx, posting = _assessed(candidate, merchant)
        if tx.schema_version != "1.1":
            raise rules.ValidationFailedError(["Schema 1.1 is required"])
        row = conn.execute(
            """INSERT INTO transaction_candidates
            (id,merchant_id,source_message_id,candidate_index,original_extraction,extracted_snapshot,posting_status,created_at,expires_at,legacy_firestore_id)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (merchant_id,source_message_id,candidate_index) DO NOTHING RETURNING *""",
            (
                cid,
                merchant_id,
                source,
                candidate_index,
                Jsonb(tx.model_dump(mode="json")),
                Jsonb(tx.model_dump(mode="json")),
                posting.value,
                now,
                now + ttl,
                legacy_candidate_id,
            ),
        ).fetchone()
        row = row or _candidate(conn, merchant_id, cid)
        return _staged(row, source_message_id)


def get_candidate_record(merchant_id, candidate_id, *, db=None):
    with (db or get_db()).transaction(merchant_id) as conn:
        _merchant(conn, merchant_id)
        return _candidate_record(_candidate(conn, merchant_id, candidate_id))


def confirmation_holds(merchant_id, candidate_id, *, db=None, now=None):
    """Read-only catalog and stock preflight used before offering WhatsApp Confirm."""
    db = db or get_db()
    now = now or datetime.now(rules.IST)
    with db.transaction(merchant_id) as conn:
        merchant = _merchant(conn, merchant_id)
        candidate = _candidate(conn, merchant_id, candidate_id)
        snap, posting = _assessed(candidate["extracted_snapshot"], merchant)
        if posting != PostingStatus.PENDING_CONFIRMATION:
            return snap.validation_warnings or ["Candidate requires review"]
        try:
            plan = rules._plan_from_snapshot(snap, now.astimezone(rules.IST).date())
            products, _ = _catalog(conn, merchant_id, plan)
        except (rules._Hold, rules._Reject) as exc:
            return list(exc.reasons)
        levels = {
            str(row["product_id"]): row["quantity_on_hand"]
            for row in conn.execute(
                "SELECT * FROM inventory_balances WHERE merchant_id=%s", (merchant_id,)
            ).fetchall()
        }
        for line in plan.lines:
            if plan.kind != "sale":
                continue
            product_id = str(products[line.item_name]["id"])
            if levels.get(product_id, Decimal(0)) - line.quantity < 0:
                return ["Insufficient stock"]
        return []


def list_candidates(merchant_id, *, db=None):
    with (db or get_db()).transaction(merchant_id) as conn:
        _merchant(conn, merchant_id)
        rows = conn.execute(
            "SELECT * FROM transaction_candidates WHERE merchant_id=%s ORDER BY created_at DESC LIMIT 30",
            (merchant_id,),
        ).fetchall()
        return [_candidate_record(r) for r in rows]


def review_candidate(
    merchant_id,
    candidate_id,
    *,
    reviewed_by,
    corrected_candidate,
    review_note,
    expected_snapshot_sha256=None,
    db=None,
    now=None,
):
    with (db or get_db()).transaction(merchant_id) as conn:
        merchant = _merchant(conn, merchant_id, reviewed_by)
        old = _candidate(conn, merchant_id, candidate_id, True)
        if old["posting_status"] not in ("NEEDS_REVIEW", "PENDING_CONFIRMATION"):
            raise rules.InvalidStateError("Candidate is terminal")
        if old["expires_at"] <= (now or datetime.now(rules.IST)):
            raise rules.StaleConfirmationError("Candidate expired")
        if not review_note.strip():
            raise rules.InvalidStateError("Review note required")
        if old["snapshot_sha256"] != expected_snapshot_sha256:
            raise rules.StaleConfirmationError("Candidate revision changed")
        tx, posting = _assessed(corrected_candidate, merchant)
        if posting == PostingStatus.REJECTED:
            raise rules.ValidationFailedError(tx.validation_warnings)
        if old["extracted_snapshot"] == tx.model_dump(mode="json"):
            conn.execute(
                "UPDATE transaction_candidates SET posting_status=%s WHERE merchant_id=%s AND id=%s",
                (posting.value, merchant_id, candidate_id),
            )
            return _staged(
                _candidate(conn, merchant_id, candidate_id),
                str(old["source_message_id"]),
            )
        row = conn.execute(
            """UPDATE transaction_candidates SET extracted_snapshot=%s,revision=revision+1,
            posting_status=%s,latest_review_note=%s,reviewed_by_phone=%s WHERE merchant_id=%s AND id=%s RETURNING *""",
            (
                Jsonb(tx.model_dump(mode="json")),
                posting.value,
                review_note,
                merchant["phone_e164"],
                merchant_id,
                candidate_id,
            ),
        ).fetchone()
        return _staged(row, str(row["source_message_id"]))


def _catalog(conn, merchant_id, plan):
    rows = conn.execute(
        "SELECT * FROM products WHERE merchant_id=%s AND is_active ORDER BY id FOR UPDATE",
        (merchant_id,),
    ).fetchall()
    products = {str(r["id"]): {**r, "item_name": r["product_name"]} for r in rows}
    resolved = {}
    for line in plan.lines:
        key = rules._resolve_one(
            line.item_name, products, ("item_name", "aliases", "sku_code"), "product"
        )
        if line.unit and rules._unit_key(line.unit) != rules._unit_key(
            products[key]["unit"]
        ):
            raise rules._Hold("Item unit differs from catalog")
        resolved[line.item_name] = products[key]
    party = None
    if plan.party_name:
        rows = conn.execute(
            "SELECT * FROM parties WHERE merchant_id=%s ORDER BY id FOR UPDATE",
            (merchant_id,),
        ).fetchall()
        parties = {str(r["id"]): r for r in rows}
        pid = rules._resolve_one(
            plan.party_name, parties, ("display_name", "aliases"), "customer"
        )
        party = parties[pid]
    return resolved, party


def _commit_result(conn, row, replay=False):
    cached = conn.execute(
        "SELECT response_data FROM idempotency_keys WHERE merchant_id=%s AND operation='posting_result' AND request_id=%s",
        (row["merchant_id"], str(row["candidate_id"])),
    ).fetchone()
    if cached:
        return rules.CommitResult.model_validate(
            cached["response_data"]["commit_result"]
        ).model_copy(update={"idempotent_replay": replay})
    balance = None
    if row["party_id"]:
        balance = conn.execute(
            "SELECT udhaar_balance FROM party_balances WHERE merchant_id=%s AND party_id=%s",
            (row["merchant_id"], row["party_id"]),
        ).fetchone()["udhaar_balance"]
    return rules.CommitResult(
        candidate_id=str(row["candidate_id"]),
        txn_id=str(row["id"]),
        merchant_id=str(row["merchant_id"]),
        intent=row["intent"],
        status="COMMITTED",
        total_amount=row["total_amount"],
        received_amount=row["amount_paid"],
        credit_amount=row["credit_amount"],
        party_id=str(row["party_id"]) if row["party_id"] else None,
        party_balance=balance,
        idempotent_replay=replay,
    )


def _confirm_and_commit(
    merchant_id,
    candidate_id,
    *,
    confirmed_by,
    confirmation_request_id=None,
    expected_snapshot_sha256=None,
    db=None,
    now=None,
    legacy_transaction_id=None,
    invoice_number=None,
):
    db = db or get_db()
    now = now or datetime.now(rules.IST)
    request_id = confirmation_request_id or f"confirm:{candidate_id}"
    with db.transaction(merchant_id) as conn:
        merchant = _merchant(conn, merchant_id, confirmed_by, True)
        candidate = _candidate(conn, merchant_id, candidate_id, True)
        decision = conn.execute(
            "SELECT * FROM confirmations WHERE merchant_id=%s AND request_id=%s",
            (merchant_id, request_id),
        ).fetchone()
        if decision and str(decision["candidate_id"]) != str(candidate_id):
            raise rules.IdempotencyConflictError(
                "Confirmation event already belongs to another candidate"
            )
        existing = conn.execute(
            "SELECT * FROM transactions WHERE merchant_id=%s AND candidate_id=%s",
            (merchant_id, candidate_id),
        ).fetchone()
        if existing:
            return _commit_result(conn, existing, True)
        if (
            candidate["posting_status"] != "PENDING_CONFIRMATION"
            or candidate["expires_at"] <= now
        ):
            raise rules.HeldForReviewError(["Candidate is expired or requires review"])
        if expected_snapshot_sha256 != candidate["snapshot_sha256"]:
            raise rules.StaleConfirmationError("Candidate revision changed")
        snap, posting = _assessed(candidate["extracted_snapshot"], merchant)
        if snap.currency != merchant["currency"]:
            raise rules.HeldForReviewError(
                ["Candidate currency differs from merchant currency"]
            )
        if posting != PostingStatus.PENDING_CONFIRMATION:
            raise rules.HeldForReviewError(snap.validation_warnings)
        try:
            plan = rules._plan_from_snapshot(snap, now.astimezone(rules.IST).date())
            products, party = _catalog(conn, merchant_id, plan)
        except (rules._Hold, rules._Reject) as exc:
            raise rules.HeldForReviewError(exc.reasons) from exc
        levels = {
            str(r["product_id"]): r["quantity_on_hand"]
            for r in conn.execute(
                "SELECT * FROM inventory_balances WHERE merchant_id=%s", (merchant_id,)
            ).fetchall()
        }
        deltas = {}
        for line in plan.lines:
            pid = str(products[line.item_name]["id"])
            delta = -line.quantity if plan.kind == "sale" else line.quantity
            deltas[pid] = deltas.get(pid, Decimal(0)) + delta
        if any(levels[pid] + delta < 0 for pid, delta in deltas.items()):
            raise rules.HeldForReviewError(["Insufficient stock"])
        balance = Decimal(0)
        if party:
            balance = conn.execute(
                "SELECT udhaar_balance FROM party_balances WHERE merchant_id=%s AND party_id=%s",
                (merchant_id, party["id"]),
            ).fetchone()["udhaar_balance"]
            if (
                plan.kind == "sale"
                and plan.credit
                and party["credit_limit"] is not None
                and balance + plan.credit > party["credit_limit"]
            ):
                raise rules.HeldForReviewError(["Credit limit exceeded"])
            if plan.kind == "payment_received" and plan.received > balance:
                raise rules.HeldForReviewError(
                    ["Repayment exceeds outstanding receivable"]
                )
        channel = "DASHBOARD" if request_id.startswith("dashboard:") else "WHATSAPP"
        source = _source(conn, merchant, request_id, channel, "interactive_button")
        decision = conn.execute(
            """INSERT INTO confirmations(merchant_id,candidate_id,source_message_id,request_id,
            action,actor_phone,candidate_revision,expected_snapshot_sha256) VALUES(%s,%s,%s,%s,'CONFIRM',%s,%s,%s) RETURNING id""",
            (
                merchant_id,
                candidate_id,
                source,
                request_id,
                merchant["phone_e164"],
                candidate["revision"],
                candidate["snapshot_sha256"],
            ),
        ).fetchone()["id"]
        subtotal = plan.total - plan.tax + plan.discount
        status = (
            "NOT_APPLICABLE"
            if plan.kind == "stock_added"
            else (
                "PAID"
                if not plan.credit
                else "UNPAID" if not plan.received else "PARTIAL"
            )
        )
        row = conn.execute(
            """INSERT INTO transactions(merchant_id,candidate_id,confirmation_id,intent,party_id,
            business_date,currency,subtotal,tax_amount,discount_amount,amount_paid,credit_amount,payment_status,due_date,legacy_firestore_id)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (
                merchant_id,
                candidate_id,
                decision,
                plan.intent.value,
                party["id"] if party else None,
                plan.business_date,
                snap.currency,
                subtotal,
                plan.tax,
                plan.discount,
                plan.received,
                plan.credit,
                status,
                snap.due_date,
                legacy_transaction_id,
            ),
        ).fetchone()
        tid = row["id"]
        for line in plan.lines:
            p = products[line.item_name]
            conn.execute(
                """INSERT INTO transaction_items(merchant_id,transaction_id,product_id,item_description,quantity,unit,unit_price)
                VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (
                    merchant_id,
                    tid,
                    p["id"],
                    line.item_name,
                    line.quantity,
                    line.unit or p["unit"],
                    line.unit_price,
                ),
            )
        for pid, delta in deltas.items():
            conn.execute(
                """INSERT INTO inventory_movements(merchant_id,transaction_id,product_id,movement_type,quantity_delta)
                VALUES(%s,%s,%s,%s,%s)""",
                (
                    merchant_id,
                    tid,
                    pid,
                    "SALE_DEDUCT" if plan.kind == "sale" else "PRODUCTION_ADD",
                    delta,
                ),
            )
        for account, amount in plan.receipts:
            conn.execute(
                """INSERT INTO payments(merchant_id,transaction_id,party_id,direction,method,amount,currency)
                VALUES(%s,%s,%s,'RECEIVED',%s,%s,%s)""",
                (
                    merchant_id,
                    tid,
                    party["id"] if party else None,
                    {
                        rules.ACC_CASH: "CASH",
                        rules.ACC_UPI: "UPI",
                        rules.ACC_BANK: "BANK_TRANSFER",
                    }[account],
                    amount,
                    snap.currency,
                ),
            )
        party_delta = (
            plan.credit
            if plan.kind == "sale"
            else -plan.received if plan.kind == "payment_received" else Decimal(0)
        )
        if party and party_delta:
            conn.execute(
                """INSERT INTO party_balance_movements(merchant_id,transaction_id,party_id,balance_type,amount_delta)
                VALUES(%s,%s,%s,'RECEIVABLE',%s)""",
                (merchant_id, tid, party["id"], party_delta),
            )
        journal = rules._journal_lines(plan)
        if journal:
            jid = conn.execute(
                "INSERT INTO journal_entries(merchant_id,transaction_id,description) VALUES(%s,%s,%s) RETURNING id",
                (merchant_id, tid, plan.intent.value),
            ).fetchone()["id"]
            for entry in journal:
                conn.execute(
                    """INSERT INTO journal_lines(merchant_id,journal_entry_id,account_code,account_name,debit,credit)
                    VALUES(%s,%s,%s,%s,%s,%s)""",
                    (
                        merchant_id,
                        jid,
                        entry["account"],
                        entry["account"],
                        rules.from_minor(entry["debit_minor"]),
                        rules.from_minor(entry["credit_minor"]),
                    ),
                )
        conn.execute(
            "UPDATE transaction_candidates SET posting_status='POSTED' WHERE merchant_id=%s AND id=%s",
            (merchant_id, candidate_id),
        )
        if plan.kind == "sale":
            number = conn.execute(
                "UPDATE merchants SET next_invoice_number=next_invoice_number+1 WHERE id=%s RETURNING invoice_number_prefix,next_invoice_number-1 AS n",
                (merchant_id,),
            ).fetchone()
            conn.execute(
                """INSERT INTO invoices(merchant_id,transaction_id,invoice_number,invoice_date,currency,
                merchant_snapshot,buyer_snapshot,items_snapshot,subtotal,tax_amount,discount_amount,total_amount,amount_paid,amount_due)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    merchant_id,
                    tid,
                    (
                        invoice_number
                        or f"{number['invoice_number_prefix']}-{number['n']:06d}"
                    ),
                    plan.business_date,
                    snap.currency,
                    Jsonb({"business_name": merchant["business_name"]}),
                    Jsonb(
                        {
                            "display_name": (
                                party["display_name"] if party else "Cash customer"
                            )
                        }
                    ),
                    Jsonb(
                        [
                            {
                                "name": l.item_name,
                                "quantity": str(l.quantity),
                                "unit": l.unit or "pieces",
                                "unit_price": str(l.unit_price),
                                "line_total": str(l.line_total),
                            }
                            for l in plan.lines
                        ]
                    ),
                    subtotal,
                    plan.tax,
                    plan.discount,
                    plan.total,
                    plan.received,
                    plan.credit,
                ),
            )
        # Force DB deferred checks before building a successful API result.
        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
        result = _commit_result(conn, row)
        actual_levels = conn.execute(
            "SELECT product_id,quantity_on_hand FROM inventory_balances WHERE merchant_id=%s",
            (merchant_id,),
        ).fetchall()
        payload = {
            "commit_result": result.model_dump(mode="json"),
            "inventory_balance": {
                str(r["product_id"]): str(r["quantity_on_hand"])
                for r in actual_levels
                if str(r["product_id"]) in deltas
            },
        }
        conn.execute(
            "INSERT INTO idempotency_keys(merchant_id,operation,request_id,request_hash,response_data) VALUES(%s,'posting_result',%s,%s,%s)",
            (
                merchant_id,
                str(candidate_id),
                candidate["snapshot_sha256"],
                Jsonb(payload),
            ),
        )
        return result


def confirm_and_commit(merchant_id, candidate_id, **kwargs):
    try:
        return _confirm_and_commit(merchant_id, candidate_id, **kwargs)
    except rules.HeldForReviewError:
        with (kwargs.get("db") or get_db()).transaction(merchant_id) as conn:
            _merchant(conn, merchant_id, kwargs["confirmed_by"], True)
            row = _candidate(conn, merchant_id, candidate_id, True)
            if row["posting_status"] == "PENDING_CONFIRMATION":
                conn.execute(
                    "UPDATE transaction_candidates SET posting_status='NEEDS_REVIEW' WHERE merchant_id=%s AND id=%s",
                    (merchant_id, candidate_id),
                )
        raise


def cancel_candidate(
    merchant_id,
    candidate_id,
    *,
    cancelled_by,
    reason=None,
    confirmation_request_id=None,
    db=None,
    now=None,
):
    with (db or get_db()).transaction(merchant_id) as conn:
        merchant = _merchant(conn, merchant_id, cancelled_by, True)
        row = _candidate(conn, merchant_id, candidate_id, True)
        if row["posting_status"] == "CANCELLED":
            return rules.CandidateStatus(
                candidate_id=str(candidate_id),
                merchant_id=merchant_id,
                posting_status="CANCELLED",
            )
        event = confirmation_request_id or f"cancel:{candidate_id}"
        source = _source(conn, merchant, event, "DASHBOARD", "interactive_button")
        conn.execute(
            """INSERT INTO confirmations(merchant_id,candidate_id,source_message_id,request_id,action,
            actor_phone,candidate_revision,expected_snapshot_sha256) VALUES(%s,%s,%s,%s,'CANCEL',%s,%s,%s)""",
            (
                merchant_id,
                candidate_id,
                source,
                event,
                merchant["phone_e164"],
                row["revision"],
                row["snapshot_sha256"],
            ),
        )
        conn.execute(
            "UPDATE transaction_candidates SET posting_status='CANCELLED' WHERE merchant_id=%s AND id=%s",
            (merchant_id, candidate_id),
        )
        return rules.CandidateStatus(
            candidate_id=str(candidate_id),
            merchant_id=merchant_id,
            posting_status="CANCELLED",
        )


def _transaction_read(conn, merchant_id, row):
    tid = row["id"]
    items = conn.execute(
        "SELECT * FROM transaction_items WHERE merchant_id=%s AND transaction_id=%s ORDER BY id",
        (merchant_id, tid),
    ).fetchall()
    payments = conn.execute(
        "SELECT * FROM payments WHERE merchant_id=%s AND transaction_id=%s ORDER BY id",
        (merchant_id, tid),
    ).fetchall()
    party = (
        conn.execute(
            "SELECT display_name FROM parties WHERE merchant_id=%s AND id=%s",
            (merchant_id, row["party_id"]),
        ).fetchone()
        if row["party_id"]
        else None
    )
    source = conn.execute(
        """SELECT w.external_message_id FROM transaction_candidates c JOIN workflow_messages w
        ON w.merchant_id=c.merchant_id AND w.id=c.source_message_id WHERE c.merchant_id=%s AND c.id=%s""",
        (merchant_id, row["candidate_id"]),
    ).fetchone()
    return {
        "txn_id": str(tid),
        "merchant_id": str(merchant_id),
        "intent": row["intent"],
        "status": "COMMITTED",
        "candidate_id": str(row["candidate_id"]),
        "source_message_id": source["external_message_id"],
        "business_date": row["business_date"].isoformat(),
        "currency": row["currency"],
        "total_amount": row["total_amount"],
        "total_minor": rules.to_minor(row["total_amount"]),
        "received_minor": rules.to_minor(row["amount_paid"]),
        "credit_minor": rules.to_minor(row["credit_amount"]),
        "tax_minor": rules.to_minor(row["tax_amount"]),
        "party_name": party["display_name"] if party else "Cash customer",
        "party_id": str(row["party_id"]) if row["party_id"] else None,
        "committed_at": row["committed_at"],
        "line_items": [
            {
                "sku_id": str(i["product_id"]),
                "item_name": i["item_description"],
                "quantity": i["quantity"],
                "unit": i["unit"],
                "unit_price": i["unit_price"],
                "subtotal": i["line_total"],
            }
            for i in items
        ],
        "payments": [
            {"method": p["method"], "amount": p["amount"], "status": "SETTLED"}
            for p in payments
        ],
    }


def get_transaction(merchant_id, txn_id, *, db=None):
    with (db or get_db()).transaction(merchant_id) as conn:
        _merchant(conn, merchant_id)
        row = conn.execute(
            "SELECT * FROM transactions WHERE merchant_id=%s AND id=%s",
            (merchant_id, UUID(str(txn_id))),
        ).fetchone()
        if not row:
            raise TransactionNotFoundError("Transaction unavailable for this merchant")
        return _transaction_read(conn, merchant_id, row)


def list_transactions(merchant_id, *, limit=25, db=None):
    with (db or get_db()).transaction(merchant_id) as conn:
        _merchant(conn, merchant_id)
        rows = conn.execute(
            "SELECT * FROM transactions WHERE merchant_id=%s AND intent<>'OPENING_BALANCE' ORDER BY committed_at DESC LIMIT %s",
            (merchant_id, limit),
        ).fetchall()
        return [_transaction_read(conn, merchant_id, r) for r in rows]


def get_inventory(merchant_id, sku_id=None, *, db=None):
    with (db or get_db()).transaction(merchant_id) as conn:
        _merchant(conn, merchant_id)
        rows = conn.execute(
            """SELECT p.*,b.quantity_on_hand FROM products p JOIN inventory_balances b
            ON b.merchant_id=p.merchant_id AND b.product_id=p.id WHERE p.merchant_id=%s ORDER BY p.id""",
            (merchant_id,),
        ).fetchall()
        return [
            rules.InventoryStock(
                sku_id=str(r["id"]),
                item_name=r["product_name"],
                unit=r["unit"],
                current_stock=r["quantity_on_hand"],
                default_unit_price=r["default_unit_price"] or 0,
                reorder_level=r["reorder_level"],
                below_reorder_level=r["quantity_on_hand"] < r["reorder_level"],
            )
            for r in rows
            if sku_id is None or str(r["id"]) == sku_id
        ]


def list_parties(merchant_id, *, db=None):
    with (db or get_db()).transaction(merchant_id) as conn:
        _merchant(conn, merchant_id)
        rows = conn.execute(
            """SELECT p.*,b.udhaar_balance FROM parties p JOIN party_balances b
            ON b.merchant_id=p.merchant_id AND b.party_id=p.id WHERE p.merchant_id=%s ORDER BY p.id""",
            (merchant_id,),
        ).fetchall()
        return [
            {
                "party_id": str(r["id"]),
                "display_name": r["display_name"],
                "phone": r["phone_e164"],
                "current_udhaar_balance": r["udhaar_balance"],
                "credit_limit": r["credit_limit"],
            }
            for r in rows
        ]


def get_party_balance(merchant_id, party_id, *, db=None):
    rows = list_parties(merchant_id, db=db)
    row = next((r for r in rows if r["party_id"] == party_id), None)
    if not row:
        raise rules.PartyNotFoundError("Party unavailable")
    limit = row["credit_limit"] or Decimal(0)
    return rules.PartyBalance(
        party_id=party_id,
        display_name=row["display_name"],
        outstanding=row["current_udhaar_balance"],
        credit_limit=limit,
        available_credit=limit - row["current_udhaar_balance"],
    )


def get_daily_summary(merchant_id, business_date=None, *, db=None):
    day = business_date or datetime.now(rules.IST).date().isoformat()
    with (db or get_db()).transaction(merchant_id) as conn:
        _merchant(conn, merchant_id)
        txs = conn.execute(
            "SELECT * FROM transactions WHERE merchant_id=%s AND business_date=%s",
            (merchant_id, day),
        ).fetchall()
        receipts = conn.execute(
            """SELECT p.method,sum(p.amount) AS total FROM payments p JOIN transactions t
            ON t.merchant_id=p.merchant_id AND t.id=p.transaction_id WHERE t.merchant_id=%s AND t.business_date=%s
            AND p.direction='RECEIVED' GROUP BY p.method""",
            (merchant_id, day),
        ).fetchall()
        amounts = {r["method"]: r["total"] for r in receipts}
        outstanding = conn.execute(
            "SELECT coalesce(sum(udhaar_balance),0) AS n FROM party_balances WHERE merchant_id=%s",
            (merchant_id,),
        ).fetchone()["n"]
        produced = conn.execute(
            """SELECT coalesce(sum(m.quantity_delta),0) AS n FROM inventory_movements m JOIN transactions t
            ON t.merchant_id=m.merchant_id AND t.id=m.transaction_id WHERE t.merchant_id=%s AND t.business_date=%s AND t.intent='PRODUCTION_LOG'""",
            (merchant_id, day),
        ).fetchone()["n"]
        sales = [t for t in txs if t["intent"] == "SALE_INVOICE"]
        return rules.DailySummary(
            merchant_id=merchant_id,
            business_date=day,
            transaction_count=sum(t["intent"] != "OPENING_BALANCE" for t in txs),
            sales_count=len(sales),
            sales_total=sum((t["total_amount"] for t in sales), Decimal(0)),
            cash_received=amounts.get("CASH", 0),
            upi_received=amounts.get("UPI", 0),
            bank_received=amounts.get("BANK_TRANSFER", 0),
            credit_extended=sum((t["credit_amount"] for t in sales), Decimal(0)),
            collections_total=sum(
                (t["amount_paid"] for t in txs if t["intent"] == "UDHAAR_COLLECTION"),
                Decimal(0),
            ),
            units_produced=produced,
            total_receivables_outstanding=outstanding,
        )
