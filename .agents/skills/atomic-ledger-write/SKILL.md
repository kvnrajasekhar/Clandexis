---
name: atomic-ledger-write
description: Executes atomic multi-document Firestore writes for inventory movements, sales transactions, and udhaar balances with strict ACID idempotency.
---

# Atomic Ledger Write Skill

## Purpose
Safely mutates the financial and inventory ledger of an MSME tenant upon validated transaction approval.
Ensures zero race conditions and zero partial commits.

## Pre-Conditions (Enforced by Safety Gate)
1. `confidence_score >= 0.85`
2. Total amount exactly matches sum of line items.
3. Total amount exactly matches sum of settled payments + credit.
4. If `udhaar > 5000` or `confidence_score < 0.85`, state must be `AWAITING_CONFIRMATION` until the merchant explicitly confirms via WhatsApp interactive button or text.

## Mutations in Atomic Batch
1. Insert `transactions/{txn_id}` with status `COMMITTED`.
2. Update `inventory/{sku_id}.current_stock -= quantity` (or `+= quantity` for production).
3. If sale has udhaar: update `parties/{party_id}.current_udhaar_balance += udhaar_amount`.
4. Append `inventory_movements/{movement_id}` audit record.
