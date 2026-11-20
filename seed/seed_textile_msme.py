"""
MSME Textile Seed Data Script for Clandexis Invisible ERP (Buildathon 2026).
Resets and populates initial demo data for Sri Balaji Textiles:
- Merchant: Sri Balaji Textiles (+919876543210)
- Counterparties: Ramesh Textiles, Suresh Traders
- Master Inventory: Cotton Shirts (300 units @ ₹200)
"""

import os
import sys
from datetime import datetime, timezone

from config import settings
from models.firestore_schema import (
    InventoryItem,
    Merchant,
    Party,
)


def seed_demo_data(dry_run: bool = False, *, merchant_id="+919876543210", shirt_stock=300, ramesh_opening=2500, db=None):
    merchant = Merchant(
        merchant_id=merchant_id,
        business_name="Sri Balaji Textiles",
        owner_name="Rajesh Kumar",
        preferred_lang="hi",
        high_credit_threshold=settings.HIGH_CREDIT_THRESHOLD,
        whatsapp_phone_number_id=settings.META_PHONE_NUMBER_ID or "15550269999",
        whatsapp_verified_at=datetime.now(timezone.utc),
    )

    parties = [
        Party(
            party_id="ramesh-textiles",
            display_name="Ramesh Textiles",
            aliases=["Ramesh", "Ramesh Bhai", "Ramesh Garments"],
            phone="+919811122233",
            current_udhaar_balance=ramesh_opening,
            credit_limit=50000.0,
            last_txn_at=datetime.now(timezone.utc),
        ),
        Party(
            party_id="suresh-traders",
            display_name="Suresh Traders",
            aliases=["Suresh", "Suresh Ji", "Suresh Wholesale"],
            phone="+919844455566",
            current_udhaar_balance=0.0,
            credit_limit=30000.0,
            last_txn_at=datetime.now(timezone.utc),
        ),
    ]

    inventory = [
        InventoryItem(
            sku_id="shirt-cotton-l",
            item_name="Cotton Shirt (L)",
            aliases=["cotton shirt", "sooti shirt", "shirt", "cotton shirts"],
            unit="pieces",
            current_stock=shirt_stock,
            default_unit_price=200.0,
            reorder_level=50.0,
            updated_at=datetime.now(timezone.utc),
        ),
        InventoryItem(
            sku_id="kurta-linen-m",
            item_name="Linen Kurta (M)",
            aliases=["linen kurta", "kurta", "khadi kurta"],
            unit="pieces",
            current_stock=150.0,
            default_unit_price=450.0,
            reorder_level=25.0,
            updated_at=datetime.now(timezone.utc),
        ),
    ]

    print("=" * 60)
    print("🌱 Clandexis (Invisible ERP) — Seeding MSME Demo Data")
    print("=" * 60)
    print(f"Merchant: {merchant.business_name} ({merchant.merchant_id})")
    print(f"Parties ({len(parties)}): {[p.display_name for p in parties]}")
    print(f"Inventory ({len(inventory)}): {[f'{i.item_name}: {i.current_stock} {i.unit} @ ₹{i.default_unit_price}' for i in inventory]}")

    if dry_run or (db is None and not settings.GOOGLE_APPLICATION_CREDENTIALS):
        print("\n[INFO] Running in local simulation mode (Dry Run).")
        print("To seed live Firestore, set GOOGLE_APPLICATION_CREDENTIALS in .env or run inside Cloud Run.")
        return

    try:
        from google.cloud import firestore
        db = db or firestore.Client(project=settings.GCP_PROJECT_ID)
        
        # 1. Seed Merchant
        m_ref = db.collection("merchants").document(merchant.merchant_id)
        m_ref.set(merchant.model_dump(mode="json"))

        # 2. Seed Parties
        for p in parties:
            m_ref.collection("parties").document(p.party_id).set(p.model_dump(mode="json"))

        # 3. Seed Inventory
        for item in inventory:
            m_ref.collection("inventory").document(item.sku_id).set(item.model_dump(mode="json"))

        print("\n✅ Successfully seeded live Firestore database!")
    except Exception as e:
        raise RuntimeError("Firestore seed failed") from e


if __name__ == "__main__":
    is_dry = "--dry-run" in sys.argv or True
    seed_demo_data(dry_run=is_dry)
