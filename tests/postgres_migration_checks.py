"""A real Firestore emulator source copied through canonical plans into PostgreSQL."""

import json, os
from decimal import Decimal
import pytest
from app.services.postgres_db import PostgresDatabase
from app.services import postgres_ledger as sql
from app.services import ledger as source
from scripts.migrate_local_demo_postgres import migrate
from tests.test_ledger_emulator import db, mid, sale, collection_cand


def test_copy_and_resume_preserves_source_and_financial_totals(db, mid):
    row = source.stage_candidate(
        mid, "sale", sale(cash=0, upi=2000, credit=8000, buyer="Suresh"), db=db
    )
    source.confirm_and_commit(mid, row.candidate_id, confirmed_by=mid, db=db)
    row = source.stage_candidate(
        mid, "repay", collection_cand(amount=3000, buyer="Suresh"), db=db
    )
    source.confirm_and_commit(mid, row.candidate_id, confirmed_by=mid, db=db)
    ref = db.collection("merchants").document(mid)
    data = {"merchant_id": mid, "merchant": ref.get().to_dict()}
    for name in [
        "inventory",
        "parties",
        "transactions",
        "transaction_candidates",
        "inventory_movements",
        "journal_entries",
        "opening_balances",
        "payments",
    ]:
        data[name] = {d.id: d.to_dict() for d in ref.collection(name).stream()}
    data["invoice_artifacts"] = {}
    pending = source.stage_candidate(
        mid,
        "pending",
        sale(cash=0, upi=0, credit=10000, buyer="Suresh").model_copy(
            update={"needs_clarification": True, "missing_fields": ["payment_details"]}
        ),
        db=db,
    )
    data["transaction_candidates"] = {
        d.id: d.to_dict() for d in ref.collection("transaction_candidates").stream()
    }
    data["conversation"] = {
        "active_candidate_id": pending.candidate_id,
        "detected_language": "te",
        "script_preference": "Latin",
        "latest_question": "Payment entha?",
        "missing_fields": ["payment_details"],
    }
    data["conversation_messages"] = {
        "history": {
            "inbound": {"message_id": "original.history"},
            "outbound": {"text_content": "Payment entha?"},
        }
    }
    original = json.dumps(data, default=str, sort_keys=True)
    target = PostgresDatabase()
    dest = migrate(data, target, mid)
    again = migrate(data, target, mid)
    assert dest == again
    summary = sql.get_daily_summary(dest, db=target)
    assert (
        summary.sales_total == 10000
        and summary.upi_received == 5000
        and summary.total_receivables_outstanding == 5000
    )
    assert (
        next(
            i for i in sql.get_inventory(dest, db=target) if "Shirt" in i.item_name
        ).current_stock
        == 250
    )
    assert (
        len(
            [
                t
                for t in sql.list_transactions(dest, db=target)
                if t["intent"] == "SALE_INVOICE"
            ]
        )
        == 1
    )
    with target.transaction(dest) as c:
        imported = c.execute(
            "SELECT legacy_firestore_id FROM transactions WHERE merchant_id=%s AND intent<>%s",
            (dest, "OPENING_BALANCE"),
        ).fetchall()
        assert {r["legacy_firestore_id"] for r in imported} == set(data["transactions"])
        pending_id = c.execute(
            "SELECT id FROM transaction_candidates WHERE merchant_id=%s AND legacy_firestore_id=%s",
            (dest, pending.candidate_id),
        ).fetchone()["id"]
        assert (
            c.execute(
                "SELECT active_candidate_id FROM conversations WHERE merchant_id=%s",
                (dest,),
            ).fetchone()["active_candidate_id"]
            == pending_id
        )
    for name in ["transactions", "inventory", "parties"]:
        assert {d.id: d.to_dict() for d in ref.collection(name).stream()} == data[name]
    assert json.dumps(data, default=str, sort_keys=True) == original
