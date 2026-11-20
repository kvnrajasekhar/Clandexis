"""Seed a new emulator-only scenario. Re-running with the same merchant never resets it."""
import os
from app.services import ledger
from seed.seed_textile_msme import seed_demo_data

def main():
    if os.environ.get("FIRESTORE_EMULATOR_HOST") != "127.0.0.1:8185" or os.environ.get("GCP_PROJECT_ID") != "demo-clandexis-local":
        raise SystemExit("Use the local demo environment.")
    merchant = os.environ["DASHBOARD_DEMO_MERCHANT_ID"]
    db = ledger.get_db()
    if db.collection("merchants").document(merchant).get().exists:
        return
    seed_demo_data(merchant_id=merchant, shirt_stock=200, ramesh_opening=0, db=db)
    ledger.post_opening_balances(merchant, posted_by=merchant, db=db)

if __name__ == "__main__":
    main()
