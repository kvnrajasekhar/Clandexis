"""Copy a canonical emulator merchant into an isolated local PostgreSQL account.

Source is read-only. Replay uses the existing validator/planner and SQL ledger.
A complete source archive stays on disk and in imported workflow payloads. The
source fingerprint prevents mixing different exports when resuming an import.
"""

import argparse, json, os, socket
from urllib.parse import urlsplit
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid5, NAMESPACE_URL
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb
from google.cloud import firestore
from app.schemas.transaction import TransactionCandidate
from app.services.postgres_db import PostgresDatabase
from app.services import postgres_ledger as target

COLLECTIONS = [
    "inventory",
    "parties",
    "transactions",
    "transaction_candidates",
    "inventory_movements",
    "journal_entries",
    "opening_balances",
    "payments",
]


def export_source(source_id):
    if not os.environ.get("FIRESTORE_EMULATOR_HOST"):
        raise ValueError("This command reads only the local Firestore emulator")
    address = urlsplit("//" + os.environ["FIRESTORE_EMULATOR_HOST"])
    with socket.create_connection((address.hostname, address.port), timeout=3):
        pass
    db = firestore.Client(project=os.environ["GCP_PROJECT_ID"])
    ref = db.collection("merchants").document(source_id)
    if not ref.get(timeout=10, retry=None).exists:
        raise ValueError("Source merchant does not exist")
    archive = {
        "merchant": ref.get(timeout=10, retry=None).to_dict(),
        "merchant_id": source_id,
    }
    for name in COLLECTIONS:
        archive[name] = {r.id: r.to_dict() for r in ref.collection(name).stream()}
    conversation = ref.collection("conversations").document("whatsapp")
    archive["conversation"] = conversation.get().to_dict()
    archive["conversation_messages"] = {
        r.id: r.to_dict() for r in conversation.collection("messages").stream()
    }
    archive["invoice_artifacts"] = {
        tid: {
            r.id: r.to_dict()
            for r in ref.collection("transactions")
            .document(tid)
            .collection("invoice_artifacts")
            .stream()
        }
        for tid in archive["transactions"]
    }
    return archive


def bootstrap(db, mid, phone, data, opening_stock, opening_credit):
    with db.transaction(mid) as c:
        if c.execute("SELECT id FROM merchants WHERE id=%s", (mid,)).fetchone():
            return
        c.execute(
            "INSERT INTO merchants(id,legacy_firestore_id,phone_e164,is_approved,business_name) VALUES(%s,%s,%s,true,%s)",
            (mid, data["merchant_id"], phone, data["merchant"]["business_name"]),
        )
        for key, row in data["inventory"].items():
            c.execute(
                "INSERT INTO products(id,merchant_id,legacy_firestore_id,sku_code,product_name,aliases,unit,default_unit_price,reorder_level) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    uuid5(UUID(mid), key),
                    mid,
                    key,
                    key,
                    row["item_name"],
                    row.get("aliases", []),
                    row.get("unit", "pieces"),
                    row.get("default_unit_price"),
                    row.get("reorder_level", 0),
                ),
            )
        for key, row in data["parties"].items():
            c.execute(
                "INSERT INTO parties(id,merchant_id,legacy_firestore_id,display_name,aliases,credit_limit) VALUES(%s,%s,%s,%s,%s,%s)",
                (
                    uuid5(UUID(mid), key),
                    mid,
                    key,
                    row["display_name"],
                    row.get("aliases", []),
                    row.get("credit_limit"),
                ),
            )
        event = target._source(
            c,
            {"id": mid, "phone_e164": phone},
            "migration-opening",
            "ADMIN",
            "admin_import",
        )
        snap = Jsonb(
            dict(
                schema_version="1.1",
                transaction_type="opening_balance",
                validation_status="ACCEPTED",
                missing_fields=[],
                needs_clarification=False,
                requires_review=False,
            )
        )
        candidate = c.execute(
            "INSERT INTO transaction_candidates(merchant_id,source_message_id,original_extraction,extracted_snapshot,posting_status,expires_at) VALUES(%s,%s,%s,%s,'PENDING_CONFIRMATION',now()+interval '30 minutes') RETURNING *",
            (mid, event, snap, snap),
        ).fetchone()
        decision = c.execute(
            "INSERT INTO confirmations(merchant_id,candidate_id,source_message_id,request_id,action,actor_phone,candidate_revision,expected_snapshot_sha256) VALUES(%s,%s,%s,'migration-opening','CONFIRM',%s,%s,%s) RETURNING id",
            (
                mid,
                candidate["id"],
                event,
                phone,
                candidate["revision"],
                candidate["snapshot_sha256"],
            ),
        ).fetchone()["id"]
        total = sum(opening_credit.values(), Decimal(0))
        tid = c.execute(
            "INSERT INTO transactions(merchant_id,candidate_id,confirmation_id,intent,business_date,subtotal,payment_status,legacy_firestore_id) VALUES(%s,%s,%s,'OPENING_BALANCE',current_date,%s,'NOT_APPLICABLE','migration-opening') RETURNING id",
            (mid, candidate["id"], decision, total),
        ).fetchone()["id"]
        for sku, qty in opening_stock.items():
            if qty:
                c.execute(
                    "INSERT INTO inventory_movements(merchant_id,transaction_id,product_id,movement_type,quantity_delta) VALUES(%s,%s,%s,'OPENING_STOCK',%s)",
                    (mid, tid, uuid5(UUID(mid), sku), qty),
                )
        for party, amount in opening_credit.items():
            if amount:
                c.execute(
                    "INSERT INTO party_balance_movements(merchant_id,transaction_id,party_id,balance_type,amount_delta) VALUES(%s,%s,%s,'RECEIVABLE',%s)",
                    (mid, tid, uuid5(UUID(mid), party), amount),
                )
        if total:
            jid = c.execute(
                "INSERT INTO journal_entries(merchant_id,transaction_id,description) VALUES(%s,%s,%s) RETURNING id",
                (mid, tid, "Migrated opening receivables"),
            ).fetchone()["id"]
            for account, debit, credit in [
                ("ACCOUNTS_RECEIVABLE", total, 0),
                ("OPENING_BALANCE_EQUITY", 0, total),
            ]:
                c.execute(
                    "INSERT INTO journal_lines(merchant_id,journal_entry_id,account_code,account_name,debit,credit) VALUES(%s,%s,%s,%s,%s,%s)",
                    (mid, jid, account, account, debit, credit),
                )
        c.execute(
            "UPDATE transaction_candidates SET posting_status='POSTED' WHERE merchant_id=%s AND id=%s",
            (mid, candidate["id"]),
        )


def migrate(data, db, phone):
    mid = str(uuid5(NAMESPACE_URL, "clandexis:firestore:" + data["merchant_id"]))
    encoded = json.dumps(data, sort_keys=True, default=str)
    fingerprint = sha256(encoded.encode()).hexdigest()
    stock = {k: Decimal(str(v["current_stock"])) for k, v in data["inventory"].items()}
    credit = {
        k: (
            Decimal(v["udhaar_balance_minor"]) / 100
            if v.get("udhaar_balance_minor") is not None
            else Decimal(str(v.get("current_udhaar_balance", 0)))
        )
        for k, v in data["parties"].items()
    }
    for row in data["inventory_movements"].values():
        stock[row["sku_id"]] -= Decimal(str(row["quantity"]))
    ordered = sorted(
        data["transactions"].items(), key=lambda pair: pair[1]["committed_at"]
    )
    for tid, row in ordered:
        if row["status"] != "COMMITTED" or row["intent"] not in (
            "SALE_INVOICE",
            "UDHAAR_COLLECTION",
            "PRODUCTION_LOG",
        ):
            raise ValueError(
                "Only canonical committed MVP transactions can be migrated"
            )
        if row.get("party_id"):
            delta = (
                Decimal(row["credit_minor"]) / 100
                if row["intent"] == "SALE_INVOICE"
                else (
                    -Decimal(row["received_minor"]) / 100
                    if row["intent"] == "UDHAAR_COLLECTION"
                    else Decimal(0)
                )
            )
            credit[row["party_id"]] -= delta
    if any(q < 0 for q in stock.values()) or any(a < 0 for a in credit.values()):
        raise ValueError("Source opening balances cannot be reconciled")
    bootstrap(db, mid, phone, data, stock, credit)
    with db.transaction(mid) as c:
        old = c.execute(
            "SELECT request_hash FROM idempotency_keys WHERE merchant_id=%s AND operation='migration' AND request_id='source'",
            (mid,),
        ).fetchone()
        if old and old["request_hash"] != fingerprint:
            raise ValueError(
                "Source changed since import began; use a fresh isolated destination"
            )
        c.execute(
            "INSERT INTO idempotency_keys(merchant_id,operation,request_id,request_hash,response_data) VALUES(%s,'migration','source',%s,%s) ON CONFLICT DO NOTHING",
            (mid, fingerprint, Jsonb({"status": "IMPORTING"})),
        )
    for tid, row in ordered:
        candidate = data["transaction_candidates"][row["candidate_id"]]
        snap = TransactionCandidate.model_validate(candidate["extracted_snapshot"])
        if snap.schema_version != "1.1":
            raise ValueError(
                "Legacy schema needs an explicit reviewed mapping before import"
            )
        snap.transaction_date = row["business_date"]
        event = "import:" + tid
        archive = json.loads(
            json.dumps(
                {
                    "original_transaction": row,
                    "original_candidate": candidate,
                    "invoice_artifacts": data["invoice_artifacts"].get(tid),
                    "journal": data["journal_entries"].get(row.get("journal_id")),
                },
                default=str,
            )
        )
        staged = target.stage_candidate(
            mid,
            event,
            snap,
            db=db,
            source_channel="ADMIN",
            source_payload=archive,
            legacy_candidate_id=row["candidate_id"],
        )
        record = target.get_candidate_record(mid, staged.candidate_id, db=db)
        target.confirm_and_commit(
            mid,
            staged.candidate_id,
            confirmed_by=phone,
            confirmation_request_id="dashboard:import:" + tid,
            expected_snapshot_sha256=record["snapshot_sha256"],
            db=db,
            legacy_transaction_id=tid,
            invoice_number=row.get("invoice_number") or tid,
        )
    committed_candidates = {row["candidate_id"] for _, row in ordered}
    pending = {}
    for legacy_id, row in data["transaction_candidates"].items():
        if legacy_id in committed_candidates or row["posting_status"] not in (
            "NEEDS_REVIEW",
            "PENDING_CONFIRMATION",
            "EXPIRED",
        ):
            continue
        snapshot = TransactionCandidate.model_validate(row["extracted_snapshot"])
        staged = target.stage_candidate(
            mid,
            "legacy-candidate:" + legacy_id,
            snapshot,
            db=db,
            source_channel="ADMIN",
            legacy_candidate_id=legacy_id,
            now=row["created_at"],
            ttl=row["expires_at"] - row["created_at"],
            source_payload=json.loads(
                json.dumps({"original_candidate": row}, default=str)
            ),
        )
        pending[legacy_id] = staged.candidate_id
        if row["expires_at"] <= datetime.now(timezone.utc):
            with db.transaction(mid) as c:
                c.execute(
                    "UPDATE transaction_candidates SET posting_status='EXPIRED' WHERE merchant_id=%s AND id=%s AND posting_status IN ('NEEDS_REVIEW','PENDING_CONFIRMATION')",
                    (mid, staged.candidate_id),
                )
    with db.transaction(mid) as c:
        for history in data.get("conversation_messages", {}).values():
            inbound = history["inbound"]
            event = target._source(
                c,
                {"id": mid, "phone_e164": phone},
                inbound["message_id"],
                payload=json.loads(
                    json.dumps({"legacy_history": history}, default=str)
                ),
            )
            reply = {
                "recipient_phone": phone,
                "text_content": "Historical message imported. Nothing new posted.",
            }
            c.execute(
                "UPDATE workflow_messages SET processing_status='DONE',response_payload=%s WHERE merchant_id=%s AND id=%s",
                (Jsonb({"outbound": reply}), mid, event),
            )
        old = data.get("conversation") or {}
        active = pending.get(old.get("active_candidate_id"))
        if active:
            candidate = c.execute(
                "SELECT * FROM transaction_candidates WHERE merchant_id=%s AND id=%s",
                (mid, active),
            ).fetchone()
            if candidate["expires_at"] > datetime.now(timezone.utc):
                c.execute(
                    """INSERT INTO conversations(merchant_id,sender_phone,active_candidate_id,candidate_revision,
                    latest_question,missing_fields,detected_language,script_preference,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (merchant_id) DO NOTHING""",
                    (
                        mid,
                        phone,
                        active,
                        candidate["revision"],
                        old.get("latest_question"),
                        Jsonb(old.get("missing_fields", [])),
                        old.get("detected_language", "en"),
                        old.get("script_preference", "Latin"),
                        candidate["expires_at"],
                    ),
                )
    with db.transaction(mid) as c:
        for sku, row in data["inventory"].items():
            actual = c.execute(
                "SELECT quantity_on_hand FROM inventory_balances WHERE merchant_id=%s AND product_id=%s",
                (mid, uuid5(UUID(mid), sku)),
            ).fetchone()["quantity_on_hand"]
            if actual != Decimal(str(row["current_stock"])):
                raise ValueError("Migrated stock mismatch")
        for party, row in data["parties"].items():
            actual = c.execute(
                "SELECT udhaar_balance FROM party_balances WHERE merchant_id=%s AND party_id=%s",
                (mid, uuid5(UUID(mid), party)),
            ).fetchone()["udhaar_balance"]
            expected = (
                Decimal(row["udhaar_balance_minor"]) / 100
                if row.get("udhaar_balance_minor") is not None
                else Decimal(str(row.get("current_udhaar_balance", 0)))
            )
            if actual != expected:
                raise ValueError("Migrated receivables mismatch")
        c.execute(
            "UPDATE idempotency_keys SET response_data=%s WHERE merchant_id=%s AND operation='migration' AND request_id='source'",
            (Jsonb({"status": "VERIFIED", "legacy_transactions": len(ordered)}), mid),
        )
    return mid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-merchant", required=True)
    parser.add_argument("--phone", required=True)
    parser.add_argument("--archive", required=True)
    args = parser.parse_args()
    db = PostgresDatabase()
    options = conninfo_to_dict(db.dsn)
    if options.get("host") not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("This importer is limited to the local demo database")
    data = export_source(args.source_merchant)
    path = Path(args.archive)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, default=str, indent=2)
    if path.exists():
        if json.loads(path.read_text()) != json.loads(serialized):
            raise ValueError(
                "Archive already exists with different source data; choose a new archive path"
            )
    else:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(serialized)
    mid = migrate(data, db, args.phone)
    print(
        json.dumps(
            {
                "postgres_merchant_id": mid,
                "legacy_transactions": len(data["transactions"]),
                "status": "VERIFIED",
            }
        )
    )


if __name__ == "__main__":
    main()
