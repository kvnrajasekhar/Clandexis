---
name: credit-attestation
description: Aggregates quarterly MSME transactions, generates financial summary using Gemini 4 Carbon, and cryptographically signs the SHA-256 PDF digest via Google Cloud KMS.
---

# Credit Attestation Skill

## Purpose
Generates tamper-evident, bank-grade attested financial reports for MSMEs to present to formal banking institutions and NBFCs for credit underwriting.

## Execution Workflow
1. Query committed transactions for the 90-day period from Firestore.
2. Verify trial balance integrity and reconciliation.
3. Compute business health metrics (turnover, cash-to-credit ratio, udhaar recovery period).
4. Run `gemini-4-carbon` for SME financial health assessment and anomaly detection.
5. Render credit report PDF.
6. Compute SHA-256 hash of canonical PDF bytes.
7. Invoke Cloud KMS (`clandexis-attestation-ring` / `ledger-signing-key`) to sign the hash.
8. Store immutable attestation record in Firestore with public verification QR code URL.
