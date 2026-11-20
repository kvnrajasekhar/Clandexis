# 🚀 Clandexis (Invisible ERP)

> **Zero-UI Multimodal WhatsApp Conversational ERP for Indian MSMEs**  
> *Startup Buildathon India 2026 — Team Clandexis*  
> **Google Cloud Project:** `clandexis` | **Region:** `asia-south1`

---

## 📌 Executive Summary

**Clandexis (Invisible ERP)** is a zero-UI WhatsApp conversational ERP engineered specifically for Indian Micro, Small, and Medium Enterprises (MSMEs). It directly resolves systemic operating bottlenecks including handwritten **"Kacha Bills"**, double-entry ledger fatigue, unrecorded real-time stock and credit (*udhaar*) movements, and chronic exclusion from formal banking credit.

By deploying multimodal Google AI agents directly over WhatsApp, Clandexis converts messy vernacular voice notes (Hindi, Hinglish, Telugu), handwritten receipts, and quick text messages into:
1. **Atomic ledger entries** (instant stock balance adjustments and udhaar tracking).
2. **GST / MSME compliant digital PDF invoices** delivered directly back to WhatsApp.
3. **Cryptographically signed, bank-ready credit reports** signed via Google Cloud KMS.

---

## 🏗️ 4-Layer Architecture & Tech Stack

| Layer | Technology | Function |
| :--- | :--- | :--- |
| **1. Ingestion & Webhooks** | **Meta WhatsApp Business Cloud API (Graph API v21.0)** + FastAPI on Google Cloud Run | Ingests voice notes (`.ogg`), Kacha Bill images, and interactive button clicks with HMAC-SHA256 signature verification. |
| **2. Multimodal AI & Routing** | **Google Gemini 3.8 Flash** + **Gemini 3.5 Flash-Lite** + **Gemini 3.8 Flash TTS** | Intent routing, vernacular audio transcription, OCR entity extraction with strict JSON `response_schema`. |
| **3. Agentic Orchestration & Safety** | **Google Antigravity SDK** + `.agents/skills/` + Deterministic Invariant Validator | Human-in-the-loop safety gate (triggers whenever confidence < 0.85, math mismatch, or udhaar > ₹5,000). |
| **4. Ledger & Security** | **Google Cloud Firestore (Native, `asia-south1`)** + **Cloud KMS** | Multi-document ACID transactions, append-only inventory movements, and tamper-evident SHA-256 PDF signatures. |

---

## 📁 Repository Directory Structure

```
invisible-erp/
├── .agents/
│   ├── hooks.json                               # Agent lifecycle hooks & mutation guards
│   └── skills/
│       ├── atomic-ledger-write/SKILL.md         # Atomic Firestore ACID mutation skill
│       ├── credit-attestation/SKILL.md          # 90-day ledger aggregation & KMS signing
│       └── ledger-read-query/SKILL.md           # Read-only vernacular business analytics
├── agent-orchestrator/                          # Dev 2 (AI, Prompts & Safety Gate)
│   ├── __init__.py
│   └── validate_split_math.py                   # Invariant check: Total = Sum(LineItems) = Sum(Payments)
├── dashboard/                                   # Dev 4 (React Vite Live Dashboard)
│   └── src/
├── models/                                      # Data Modeling & Canonical Contracts
│   ├── __init__.py
│   ├── canonical.py                             # Channel-agnostic Inbound & Outbound contracts
│   ├── extraction_schema.json                   # Gemini 3.8 Flash structured output schema
│   ├── firestore_schema.py                      # Multi-tenant Firestore collections & entities
│   └── whatsapp_meta.py                         # Meta Cloud API (v21.0) request & webhook models
├── pdf-service/                                 # Dev 3 (PDF Invoicing & KMS Signing)
│   ├── __init__.py
│   ├── generate_invoice.py
│   └── kms_sign_pdf.py
├── seed/
│   ├── __init__.py
│   └── seed_textile_msme.py                     # Demo seeder (Sri Balaji Textiles, Ramesh, Suresh)
├── tests/
│   ├── __init__.py
│   ├── test_meta_whatsapp_models.py             # Webhook parsing, HMAC verification, buttons
│   └── test_split_math.py                       # Math invariant & safety gate unit tests
├── webhook-service/                             # Dev 1 (Ingestion & Cloud Infra)
│   ├── __init__.py
│   ├── main.py                                  # FastAPI app (GET/POST /webhook, /api/v1/send)
│   ├── meta_whatsapp_handler.py                 # Graph API v21.0 client, media download, buttons
│   └── security.py                              # X-Hub-Signature-256 HMAC-SHA256 validator
├── .env.example
├── .gitignore
├── deploy.sh                                    # Google Cloud Run deployment script
├── mcp.json                                     # Model Context Protocol GCP data servers
├── requirements.txt
└── README.md
```

---

## 💬 WhatsApp Integration via Meta Cloud API

### 1. Inbound Webhook Normalization
Incoming webhooks from Meta are verified using HMAC-SHA256 (`X-Hub-Signature-256`) and normalized into `InboundMessage`:
- **Vernacular Voice Notes:** Downloaded via `GET /v21.0/{media_id}` and streamed directly to Gemini 3.8 Flash.
- **Kacha Bill Photos:** Media binary retrieved and fed to Gemini 3.8 Flash multimodal vision.
- **1-Tap Interactive Buttons:** Captures instant user approval/rejection for the Safety Gate.

### 2. Outbound Message Dispatching
- **Text Messages:** Instant confirmations with WhatsApp markdown formatting.
- **Interactive Quick-Reply Buttons:** Dispatches 1-tap buttons (e.g. `[✅ Confirm]`, `[❌ Cancel]`) whenever high credit or low confidence is detected.
- **Document Messages:** Delivers generated GST/MSME PDF invoices and KMS-attested credit reports.
- **Audio Voice Readbacks:** Sends synthesized vernacular audio via Gemini 3.8 Flash TTS.

---

## 📊 Core Data Models & Firestore Collections

| Collection | Document Key | Schema Fields |
| :--- | :--- | :--- |
| `merchants` | E.164 Phone (`+919876543210`) | `business_name`, `owner_name`, `preferred_lang`, `high_credit_threshold` (₹5,000), `whatsapp_verified_at` |
| `merchants/{id}/parties` | Slug (`ramesh-textiles`) | `display_name`, `aliases[]` (fuzzy match), `phone`, `current_udhaar_balance`, `last_txn_at` |
| `merchants/{id}/inventory` | SKU ID (`shirt-cotton-l`) | `item_name`, `aliases[]`, `unit`, `current_stock`, `default_unit_price`, `reorder_level` |
| `merchants/{id}/inventory_movements` | UUID (`mov_123456`) | `sku_id`, `movement_type` (`PRODUCTION_ADD`, `SALE_DEDUCT`), `quantity`, `balance_after`, `reference_txn_id` |
| `merchants/{id}/transactions` | UUID (`txn_98234723`) | `intent`, `source_channel`, `party_id`, `line_items[]`, `payments[]`, `total_amount`, `status`, `confidence_score` |
| `merchants/{id}/attestations` | Report ID (`rep_2026_q4`) | `period_start`, `period_end`, `pdf_sha256_hash`, `kms_signature_b64`, `kms_key_version`, `credit_health_summary` |

---

## 🛡️ Deterministic Financial Invariant Rules

AI-extracted data is **never** permitted to mutate the database without passing deterministic rules:
1. **Line Items Invariant:** $\sum(\text{LineItem.subtotal}) == \text{total\_amount}$
2. **Split Payments Invariant:** $\sum(\text{Payment.amount}) == \text{total\_amount}$
3. **Safety Gate:** If $\text{confidence} < 0.85$ OR $\text{udhaar} > ₹5,000$ OR math fails $\rightarrow$ Stage as `AWAITING_CONFIRMATION` and dispatch interactive confirmation buttons to WhatsApp.

---

## ⚡ Quick Start & Development

### 1. Install Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run Test Suite
```bash
PYTHONPATH=. pytest -v
```

### 3. Run Webhook Service Locally
```bash
uvicorn webhook_service.main:app --host 0.0.0.0 --port 8080 --reload
```

### 4. Reset & Seed Demo Data
```bash
python3 seed/seed_textile_msme.py --dry-run
```
