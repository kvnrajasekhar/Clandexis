"""Seed one new, explicitly selected SQL demo merchant. Existing accounts are untouched."""

from uuid import UUID, uuid4, uuid5, NAMESPACE_URL
from psycopg.types.json import Jsonb


def seed_demo_data(db, merchant_id, phone, business_name="Sri Balaji Textiles"):
    merchant_id = str(UUID(merchant_id))
    with db.transaction(merchant_id) as conn:
        existing = conn.execute(
            "SELECT phone_e164 FROM merchants WHERE id=%s", (merchant_id,)
        ).fetchone()
        if existing:
            if existing["phone_e164"] != phone:
                raise ValueError("Existing merchant belongs to another phone")
            return merchant_id
        conn.execute(
            "INSERT INTO merchants(id,phone_e164,is_approved,business_name) VALUES(%s,%s,true,%s)",
            (merchant_id, phone, business_name),
        )
        for key, name, aliases in [
            ("suresh", "Suresh Traders", ["Suresh"]),
            ("ramesh", "Ramesh Textiles", ["Ramesh"]),
            ("raju", "Raju", ["Raju"]),
        ]:
            conn.execute(
                "INSERT INTO parties(id,merchant_id,display_name,aliases,credit_limit) VALUES(%s,%s,%s,%s,50000)",
                (uuid5(UUID(merchant_id), key), merchant_id, name, aliases),
            )
        products = [
            (
                "shirt-cotton-l",
                "Cotton Shirt (L)",
                ["shirt", "shirts", "cotton shirt", "cotton shirts"],
                200,
                200,
            ),
            ("kurta-linen-m", "Linen Kurta (M)", ["kurta", "linen kurta"], 450, 150),
        ]
        for sku, name, aliases, price, quantity in products:
            conn.execute(
                """INSERT INTO products(id,merchant_id,sku_code,product_name,aliases,unit,default_unit_price,reorder_level)
                VALUES(%s,%s,%s,%s,%s,'pieces',%s,50)""",
                (uuid5(UUID(merchant_id), sku), merchant_id, sku, name, aliases, price),
            )
        source = conn.execute(
            """INSERT INTO workflow_messages(merchant_id,sender_phone,source_channel,external_message_id,message_type)
            VALUES(%s,%s,'ADMIN','demo-opening-stock','admin_seed') RETURNING id""",
            (merchant_id, phone),
        ).fetchone()["id"]
        snapshot = Jsonb(
            dict(
                schema_version="1.1",
                transaction_type="opening_balance",
                validation_status="ACCEPTED",
                needs_clarification=False,
                requires_review=False,
                missing_fields=[],
            )
        )
        candidate = conn.execute(
            """INSERT INTO transaction_candidates(merchant_id,source_message_id,original_extraction,extracted_snapshot,posting_status,expires_at)
            VALUES(%s,%s,%s,%s,'PENDING_CONFIRMATION',now()+interval '30 minutes') RETURNING *""",
            (merchant_id, source, snapshot, snapshot),
        ).fetchone()
        decision = conn.execute(
            """INSERT INTO confirmations(merchant_id,candidate_id,source_message_id,request_id,action,actor_phone,candidate_revision,expected_snapshot_sha256)
            VALUES(%s,%s,%s,'demo-opening-stock','CONFIRM',%s,%s,%s) RETURNING id""",
            (
                merchant_id,
                candidate["id"],
                source,
                phone,
                candidate["revision"],
                candidate["snapshot_sha256"],
            ),
        ).fetchone()["id"]
        tx = conn.execute(
            """INSERT INTO transactions(merchant_id,candidate_id,confirmation_id,intent,business_date,payment_status)
            VALUES(%s,%s,%s,'OPENING_BALANCE',current_date,'NOT_APPLICABLE') RETURNING id""",
            (merchant_id, candidate["id"], decision),
        ).fetchone()["id"]
        for sku, name, aliases, price, quantity in products:
            conn.execute(
                """INSERT INTO inventory_movements(merchant_id,transaction_id,product_id,movement_type,quantity_delta)
                VALUES(%s,%s,%s,'OPENING_STOCK',%s)""",
                (merchant_id, tx, uuid5(UUID(merchant_id), sku), quantity),
            )
        conn.execute(
            "UPDATE transaction_candidates SET posting_status='POSTED' WHERE merchant_id=%s AND id=%s",
            (merchant_id, candidate["id"]),
        )
    return merchant_id
