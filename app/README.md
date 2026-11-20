# Clandexis AI Engine & Production API

Clandexis transforms messy merchant inputs (WhatsApp voice notes in Telugu/Hindi/English, PDF invoices, receipt photos, raw text) into **validated, accounting-ready transaction candidates**.

---

## Architecture Overview

```
1. Ingestion / Webhook (WhatsApp / Mobile App / Web)
                ↓
2. Stage 2: Input Processing (`app.services.input_processor`)
   - Merchant identity verification
   - MIME detection (Audio, PDF, Image, Text)
   - Idempotency hashing (prevents duplicate ledger postings)
                ↓
3. Stage 3: Gemini Multimodal Interpretation (`app.services.interpreter`)
   - Schema 1.1 extraction contract
   - Identifies intent: `sale`, `purchase`, `payment_received`, `payment_made`, etc.
   - Extracts buyer, items, rates, line totals, taxes, discounts, declared payment terms
   - Formulates clarification questions if missing or ambiguous
                ↓
4. Stage 4: Business Validation & Reconciliation (`app.services.validator`)
   - Strict `Decimal` arithmetic
   - Overpayment / arithmetic mismatch checks -> `REJECTED`
   - Unstated payments guarded -> Asks clarification, never assumes credit
   - Inconsistencies flagged -> `NEEDS_REVIEW`
   - Role resolution (Merchant as Buyer vs Supplier -> `purchase` vs `sale`)
                ↓
5. REST API Layer (`FastAPI` in `app.main`)
   - Swagger OpenAPI docs at `/api/v1/docs`
   - Easy integration for frontend, mobile, and backend microservices
```

---

## Clean Codebase Layout

```
clandexis/
├── app/
│   ├── __init__.py               # Top-level exports
│   ├── main.py                   # FastAPI Application entrypoint
│   ├── pipeline.py               # End-to-end PipelineService (Stages 2 -> 3 -> 4)
│   ├── api/
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       └── endpoints.py      # REST Endpoints: /extract/text, /extract/media, /speech/*
│   ├── core/
│   │   ├── __init__.py
│   │   └── config.py             # App configuration, models, versioning
│   ├── schemas/
│   │   ├── __init__.py
│   │   ├── transaction.py        # Pydantic Schema 1.1 (TransactionCandidate, LineItem, etc.)
│   │   └── speech.py             # TTS / Audio schemas
│   ├── services/
│   │   ├── __init__.py
│   │   ├── input_processor.py    # Stage 2: Deduplication & payload normalizer
│   │   ├── interpreter.py        # Stage 3: Multimodal Gemini JSON extraction
│   │   ├── validator.py          # Stage 4: Strict Decimal Financial Reconciliation
│   │   └── speech.py             # Text-to-Speech synthesizer
│   └── utils/
│       ├── __init__.py
│       ├── client.py             # Authenticated Gemini client with fallback
│       └── media.py              # MIME type and SHA-256 fingerprinting
├── tests/
│   ├── __init__.py
│   └── test_api_endpoints.py     # Automated API & Pipeline test suite
├── run_adversarial_suite.py      # 8-case financial release validation
├── run_extraction_batch.py       # Batch tester for audio/pdf files
├── pytest.ini                    # Pytest configuration
└── .env                          # API credentials
```

---

## How to Run & Integrate

### 1. Start the API Server
```bash
./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
Interactive API docs are available at **`http://localhost:8000/api/v1/docs`**.

### 2. Available API Endpoints

#### `POST /api/v1/extract/text`
Extract and validate a transaction from text (WhatsApp / SMS):
```bash
curl -X POST "http://localhost:8000/api/v1/extract/text" \
  -H "Content-Type: application/json" \
  -d '{
    "merchant_id": "merchant_9876543210",
    "message_id": "msg_001",
    "raw_text": "Ramesh ko 5 packet Atta diya 250 each aur 2 oil bottle 180 each. 1000 cash diya, baki kal dega."
  }'
```

#### `POST /api/v1/extract/media`
Multipart upload for voice notes (`.mp3`, `.wav`, `.ogg`), receipt images (`.jpg`, `.png`), or PDFs:
```bash
curl -X POST "http://localhost:8000/api/v1/extract/media" \
  -F "merchant_id=merchant_123" \
  -F "message_id=msg_voice_002" \
  -F "file=@voice-telugu.mp3"
```

#### `POST /api/v1/speech/synthesize`
Convert clarification questions or confirmation notices to audio:
```bash
curl -X POST "http://localhost:8000/api/v1/speech/synthesize" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Was the 5000 rupees payment received in cash, UPI, or given on credit?"
  }'
```

### 3. Direct Python Library Integration
```python
from app import PipelineService

pipeline = PipelineService()
result = pipeline.process_and_interpret(
    merchant_id="merchant_123",
    raw_text="Sold 1000 shirts to Raju for 20000. 5000 cash, 15000 credit.",
)

# Access validated transaction candidates
tx = result.extraction.transactions[0]
print(tx.validation_status)  # "ACCEPTED", "REJECTED", or "NEEDS_REVIEW"
print(tx.total_amount)       # 20000.0
print(tx.credit_amount)      # 15000.0
```

### 4. Running Automated Tests
```bash
./.venv/bin/pytest -v
```
