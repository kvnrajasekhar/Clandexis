# Clandexis Invisible ERP — Service Architecture, Engineering Invariants & Execution Protocol

> **Document Status**: Authoritative Engineering Charter & Service Standard Operating Protocol (SOP)  
> **Target Audience**: Core Engineering Team, Service Developers, Technical Reviewers, and Hackathon Jury  
> **Location**: `dev_docs/service_architecture_and_invariants_protocol.md`  
> **Last Updated**: November 2026  

---

## Executive Summary & Purpose

In software engineering, high-stakes financial platforms cannot operate on loose agreements or informal conventions. This document defines the **Architectural Invariants, Service Engineering Standards, and System Protocols** governing the development, deployment, and operation of **Clandexis Invisible ERP**.

Think of this document as the **"Constitutional Rules of Development"** for Clandexis: every microservice, AI model integration, database query, and agent workflow must strictly adhere to these protocols. Any pull request or architecture change that violates these non-negotiable invariants will fail continuous integration and will be rejected.

---

## 1. The Core Invariant Axioms ("Non-Negotiable Rules of Development")

```text
┌─────────────────────────────────────────────────────────────────────────────────┐
│                      THE 6 CANONICAL INVARIANT AXIOMS                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│ 1. Strict Separation: Probabilistic AI Proposes — Deterministic Code Commits    │
│ 2. Absolute Double-Entry Equilibrium: Sum of Debits === Sum of Credits          │
│ 3. Append-Only Immutability: Zero SQL UPDATE / DELETE on Financial Ledgers      │
│ 4. Two-Tier Idempotency: Deduplication at Webhook and Candidate Locking         │
│ 5. Cryptographic Attestation: All Exported Financials Signed via Cloud KMS      │
│ 6. Zero Plaintext Secrets: Dynamic Secrets Resolution via Cloud Secret Manager  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Invariant 1: Separation of Probabilistic AI and Deterministic Accounting
- **The Rule**: No Generative AI model (Gemini or otherwise) is ever granted direct write authorization to the database, ledger, or inventory tables.
- **Protocol**: LLMs are confined strictly to **Extraction & Hypothesis Generation (Schema 1.1)**. 
- **The Lifecycle**:
  $$\text{Messy Real-World Input} \xrightarrow{\text{Gemini}} \text{Candidate JSON} \xrightarrow{\text{Deterministic Validator}} \text{Enforced Math} \xrightarrow{\text{1-Tap Merchant Gate}} \text{ACID Commit}$$

### Invariant 2: Mathematical & Double-Entry Equilibrium
- **The Rule**: Every committed financial transaction must balance to the exact paise:
  $$\sum \text{Debits} \equiv \sum \text{Credits}$$
- **Split-Payment Invariant**: For every sales transaction:
  $$\sum \text{Settled Payments (Cash, UPI, Bank)} + \text{Credit/Udhaar Amount} \equiv \text{Net Bill Total}$$
- **Line-Item Invariant**: For every line item $i$:
  $$\text{Quantity}_i \times \text{Unit Price}_i \equiv \text{Line Total}_i$$
- **Protocol**: Float approximations are strictly prohibited. All financial logic must use 2-decimal fixed-point representation (`Decimal("0.01")` with `ROUND_HALF_UP`).

### Invariant 3: Append-Only Immutability & Compensating Entries
- **The Rule**: Once a journal line, inventory movement, or invoice record is committed, it is **immutable**. SQL `UPDATE` or `DELETE` on financial records is strictly forbidden in application code.
- **Protocol**: 
  - To correct an erroneous transaction, the system appends a **Linked Compensating Journal Entry** (`reversal_of_id`) that cancels the original debit/credit legs and restores inventory.
  - Audit trails remain 100% complete and tamper-evident for judicial or financial auditing.

### Invariant 4: Two-Tier Idempotency Guarantee
- **The Rule**: Network retries (Meta WhatsApp webhooks resending after timeout, merchant double-tapping "Confirm") must have **exactly-once execution semantics**.
- **Protocol**:
  - **Tier 1 (Transport Layer)**: SHA-256 hash of `merchant_id + message_id + raw_payload` tracked in deduplication memory. Duplicates receive an immediate HTTP 200 without triggering the AI pipeline.
  - **Tier 2 (Ledger Layer)**: Candidate IDs (`cand_...`) act as atomic state machine locks. If status is already `POSTED`, the ledger commit is skipped, and the cached response/invoice is returned.

### Invariant 5: Institutional Cryptographic Attestation
- **The Rule**: No balance sheet, profit & loss, or trial balance report exported for lenders can exist as a bare, unverifiable PDF.
- **Protocol**: Reports must pass a deterministic trial balance equilibrium check, compute a canonical SHA-256 payload digest, and be signed with an asymmetric RSA-2048 key managed inside **Google Cloud KMS**.

### Invariant 6: Zero Plaintext Secrets in Production
- **The Rule**: No production environment may bundle credentials, database connection strings, or Meta access tokens in `.env` files or container images.
- **Protocol**: All services must use the centralized `get_secret()` abstraction that resolves dynamically from **Google Cloud Secret Manager** at runtime.

---

## 2. End-to-End System Architecture & Service Flow

### 2.1 The Complete Transaction Flow Diagram

```mermaid
sequenceDiagram
    autonumber
    actor Merchant as MSME Merchant (WhatsApp)
    participant WH as Webhook Service (Cloud Run)
    participant SEC as Idempotency & HMAC Guard
    participant ROUTE as Intent Router (gemini-decision-1)
    participant EXT as Multimodal Extractor (Gemini 3.8 Flash)
    participant VAL as Deterministic Business Validator
    participant ORCH as Agent Orchestrator & State Machine
    participant DB as Cloud SQL (PostgreSQL 17)
    participant DOC as PDF & Attestation Engine (KMS + GCS)

    Merchant->>WH: Sends Voice Note / Kacha Bill Photo / Text
    WH->>SEC: Verify HMAC-SHA256 Signature & Idempotency Key
    alt Duplicate Message
        SEC-->>WH: Cached 200 OK (Drop Execution)
        WH-->>Merchant: (Silent drop or re-send last response)
    else Unique Message
        SEC->>ROUTE: Classify Contextual Intent
        alt Read-Only Query (Stock, Udhaar Balance)
            ROUTE->>DB: Execute Read-Only SQL Query
            DB-->>ORCH: Query Result Dataset
            ORCH-->>Merchant: Vernacular Read-Only Answer (Zero Mutation)
        else Transaction Input
            ROUTE->>EXT: Ingest Audio/Image/Text (Schema 1.1)
            EXT-->>VAL: Structured Uncommitted Candidate JSON
            VAL->>VAL: Execute Line Math, Split-Payment & Tax Checks
            alt Missing Critical Facts (e.g., Price missing)
                VAL-->>ORCH: State: AWAITING_CLARIFICATION
                ORCH-->>Merchant: Vernacular Question: "What was the unit price?"
                Merchant->>WH: Replies: "200 rupees per shirt"
                Note over WH,VAL: State preserved in conversation store; merged with candidate
            else Invariants Satisfied
                VAL->>DB: Persist Candidate (Status: PENDING)
                VAL-->>ORCH: State: AWAITING_CONFIRMATION
                ORCH-->>Merchant: Interactive WhatsApp Card: [Confirm] [Edit] [Cancel]
                
                Merchant->>WH: Taps [Confirm] Button
                WH->>DB: Execute Single ACID Tx (SELECT FOR UPDATE)
                Note over DB: Lock SKU Row -> Deduct Stock -> Insert Journal Lines -> Update Udhaar
                DB-->>DOC: Transaction Committed (ID: TX-1082)
                DOC->>DOC: Render PDF & Generate SHA-256 Digest
                DOC->>GCS: Upload PDF to gs://clandexis-pdf-artifacts
                DOC-->>ORCH: Secure Download URL
                ORCH-->>Merchant: ✅ "Transaction Recorded! Stock deducted." + PDF Link
            end
        end
    end
```

### 2.2 Transaction Candidate State Machine

Every transaction lifecycle is managed through explicit, non-overlapping states:

```
  [INBOUND PAYLOAD]
          │
          ▼
    ┌───────────┐
    │ RECEIVED  │ ──(Duplicate detected)──► [DROPPED / 200 OK]
    └─────┬─────┘
          │ (Unique)
          ▼
   ┌─────────────┐
   │ EXTRACTING  │ (Gemini 3.8 Flash Schema 1.1)
   └──────┬──────┘
          │
          ▼
   ┌─────────────┐
   │ VALIDATING  │ (Deterministic Math & Invariant Check)
   └──────┬──────┘
          │
          ├────────────────────────────────┐
          │ (Missing critical facts)       │ (Invariants passed & verified)
          ▼                                ▼
┌───────────────────────┐        ┌───────────────────────┐
│ AWAITING_CLARIFICATION│        │ AWAITING_CONFIRMATION │
└─────────┬─────────────┘        └───────────┬───────────┘
          │ (Answer received)                │
          └───────────► Merged ◄─────────────┤
                                             ├──────(Merchant taps [Cancel])──► [CANCELLED]
                                             │
                                             ▼ (Merchant taps [Confirm])
                                      ┌─────────────┐
                                      │  COMMITTED  │ (ACID Transaction in Cloud SQL)
                                      └──────┬──────┘
                                             │
                                             ▼
                                      ┌─────────────┐
                                      │   POSTED    │ (Ledger updated, PDF delivered)
                                      └─────────────┘
```

---

## 3. AI Models Governance & Allocation Standards

Every AI model utilized in Clandexis is assigned an exact architectural boundary. Models must never be swapped or repurposed without an amendment to this protocol.

### 3.1 Model Allocation Matrix

| Model Identifier | Canonical Role | Input Modalities | Output Format | Latency SLA |
| :--- | :--- | :--- | :--- | :--- |
| **`gemini-3.8-flash`** | Primary Multimodal Extractor | Audio (`audio/ogg`, `audio/mp3`), Image (`image/jpeg`, `image/png`), PDF, Text | Strict Pydantic JSON (`GeminiCandidate` Schema 1.1) | $\le 900\text{ ms}$ |
| **`gemini-decision-1`** | Google Decisions Engine | Natural language string + Conversational State Context | Structured Choice Response (`ChoiceQuestion`) | $\le 200\text{ ms}$ |
| **`gemini-3.5-flash-lite`** | Edge Intent Router | Inbound text string | Enum (`transaction`, `query`, `greeting`, `unsupported`) | $\le 180\text{ ms}$ |
| **`gemini-3.8-flash-tts`** | Vernacular Audio Generator | Clean vernacular text (Telugu, Hindi, Hinglish) | Audio stream (`audio/wav`, `audio/mp3`) | $\le 750\text{ ms}$ |
| **`gemini-4-carbon` / `hydrogen`** | Credit & Underwriting Analyst | 90-day Trial Balance JSON, Invariant Reports | Structured Markdown + Financial Risk Ratings | Offline / Async |

---

### 3.2 Model Execution Standards

#### 1. Gemini 3.8 Flash (Multimodal Extractor Protocol)
- **Prompt Isolation**: System instructions must enforce that the model **never invents** prices, dates, or counterparties. Missing details must be output as `null`.
- **Credit Separation**: The model is forbidden from placing credit/udhaar inside the `payments[]` array. `payments[]` records settled tender only (Cash, UPI, Bank). Outstanding debt must populate `credit_amount`.
- **Audio Verbatim Transcript**: For audio notes, the model must populate `raw_transcript` verbatim in the original vernacular script before mapping to structured fields.

#### 2. Gemini Decisions Engine (`gemini-decision-1` Protocol)
- **Zero Hallucination Intent Resolution**: In conversational interfaces, short messages (e.g., *"₹200"*, *"Yes"*, *"Cancel"*) cause standard LLMs to hallucinate new transactions.
- **Standard**: All contextual intent resolution must use calibrated Choice Criteria:
  ```python
  resp = client.decisions.create(
      model="gemini-decision-1",
      input=f"Merchant message: '{text}'. Has active held transaction: {has_active}.",
      questions={
          "merchant_intent": {
              "type": "choice",
              "instructions": "Determine merchant intent in MSME ERP context.",
              "criteria": {
                  "confirm_candidate": "Confirming or approving an active pending candidate.",
                  "cancel_candidate": "Rejecting or dropping an active pending candidate.",
                  "clarify_field": "Answering missing price/qty for held candidate.",
                  "new_sale": "Reporting a brand-new sale transaction.",
                  "query_pending_status": "Checking what candidates are pending.",
                  "general_greeting": "Greeting, help inquiry, or non-financial message."
              }
          }
      }
  )
  ```

#### 3. Gemini TTS & Audio Readback Protocol
- **Illiterate / Factory Floor Accessibility**: If the inbound message was sent via audio, the system response must generate an audio readback in the merchant’s preferred language (`preferred_language` stored in `merchants` table).
- **Text-First Fallback**: If audio synthesis fails or times out ($>1.2\text{s}$), the system must degrade gracefully to WhatsApp text without blocking the transaction flow.

---

## 4. Multi-Agent Division of Labor & Service Boundaries

Clandexis employs a multi-agent hierarchy where each agent has strict privilege limits:

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        AGENT PRIVILEGE MATRIX                          │
├─────────────────────────┬─────────────┬─────────────┬──────────────────┤
│ Agent Name              │ DB Read     │ DB Write    │ External Network │
├─────────────────────────┼─────────────┼─────────────┼──────────────────┤
│ Gateway & Security      │ No          │ No          │ Meta Graph API   │
│ Intent Router           │ Read (Meta) │ No          │ Google AI Studio │
│ Multimodal Extractor    │ No          │ No          │ Google AI Studio │
│ Business Validator      │ Read (SKU)  │ No          │ None (Pure Logic)│
│ Clarification Agent     │ Read (State)│ Write(State)│ Meta Graph API   │
│ ACID Ledger Agent       │ Full Read   │ Full Write  │ None             │
│ Document & Attestation  │ Read (Tx)   │ Write(Docs) │ Cloud KMS / GCS  │
│ Conversational Query    │ Read-Only   │ Strictly No │ None             │
└─────────────────────────┴─────────────┴─────────────┴──────────────────┘
```

### Agent Responsibilities:
1. **Gateway & Security Agent (`security.py`)**:
   - Validates `X-Hub-Signature-256` using the HMAC-SHA256 secret.
   - Rejects unapproved WhatsApp senders (enforcing 1-phone-to-1-merchant isolation).
2. **Deterministic Business Validator (`validator.py` & `msme_validator.py`)**:
   - Performs Mod-36 Luhn check on Indian GSTINs.
   - Enforces the **Confidence Floor**: If `candidate.confidence < 0.85` or if `credit_amount > 5000.00`, sets `requires_review = True`, blocking instant confirmation.
3. **Clarification Agent (`orchestrator.py`)**:
   - Manages the dialog state. Preserves partially extracted entities across turns so the merchant never repeats known facts.
4. **ACID Ledger Agent (`postgres_ledger.py`)**:
   - Sole custodian of database mutations. Employs `SELECT ... FOR UPDATE` locks to eliminate inventory race conditions.

---

## 5. Google Cloud Platform (GCP) Implementation Standards

All cloud infrastructure must comply with the following standards:

```text
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                          GCP ARCHITECTURE & INTEGRATION MAP                             │
├────────────────────────┬────────────────────────────────────────────────────────────────┤
│ GCP Service            │ Architectural Responsibility                                   │
├────────────────────────┼────────────────────────────────────────────────────────────────┤
│ **Cloud Run**          │ Microservice execution (FastAPI backend + Vite React SPA)      │
│ **Cloud SQL**          │ PostgreSQL 17 ACID ledger, row-level locks, multi-tenant RLS   │
│ **Cloud Storage (GCS)**│ Commercial PDF invoices & attested quarterly report archives   │
│ **Cloud KMS**          │ Asymmetric RSA-2048 signing key for tamper-evident reporting   │
│ **Secret Manager**     │ Dynamic credential injection at startup / runtime              │
│ **BigQuery**           │ Anonymized textile supply chain & credit risk analytics        │
└────────────────────────┴────────────────────────────────────────────────────────────────┘
```

### 5.1 Cloud Run Deployment Protocol
- **Zero-Traffic Canary Rule**: Deployments must execute with `--no-traffic` using git commit SHA tags. Production traffic is only routed after smoke verification:
  ```bash
  gcloud run deploy clandexis-erp \
    --project clandexis \
    --region asia-south1 \
    --source . \
    --no-traffic \
    --tag "release-${RELEASE_SHA:0:8}"
  ```
- **Stateless Microservices**: Instances must not store session state or uncommitted files on local disk. All persistence routes to Cloud SQL or GCS.

### 5.2 Cloud SQL (PostgreSQL 17) Standards
- **Concurrency & Locking**: Stock decrementing queries must acquire row-level locks to prevent stock overselling:
  ```sql
  SELECT current_stock, minimum_reorder_level 
  FROM inventory_balances 
  WHERE merchant_id = $1 AND sku = $2 
  FOR UPDATE;
  ```
- **Connection Isolation**: Applications must configure connection pooling with explicit statement timeouts ($\le 5\text{s}$) to avoid hanging locks during network hiccups.

### 5.3 Cloud KMS & Cryptographic Attestation Standards
- **Key Ring & Specification**: All lender financial attestations must be signed with an asymmetric key:
  - **Key Ring**: `clandexis-attestation-ring`
  - **Key Name**: `ledger-signing-key`
  - **Location**: `asia-south1`
  - **Algorithm**: `RSA_SIGN_PKCS1_2048_SHA256`
- **Signing Protocol**:
  1. Generate canonical JSON representation of balance sheet & trial balance.
  2. Compute SHA-256 digest: $H = \text{SHA-256}(\text{Canonical JSON})$.
  3. Dispatch digest to Cloud KMS `asymmetricSign`.
  4. Embed Base64-encoded signature, Key Version ID, and UTC timestamp into the PDF metadata and `digital_attestations` database table.

### 5.4 Cloud Storage (GCS) File Path Hierarchy
Artifacts must adhere to standard bucket partitioning:
```text
gs://clandexis-pdf-artifacts/
├── {merchant_id}/
│   ├── invoices/
│   │   ├── 2026/
│   │   │   └── INV-2026-0001-{sha256:0:8}.pdf
│   └── attestations/
│       └── Q4-FY26-Attested-Report-{key_version}.pdf
```

---

## 6. Vernacular Language & Human Safety Gate Protocols

### 6.1 Multi-Dialect & Code-Switching Rules
Merchants interact in **Telugu, Hindi, English, or Code-Switched combinations** (*Telugish*, *Hinglish*).
1. **Dynamic Language Persistence**: The system resolves language and script preference from incoming messages and updates `merchants.preferred_language`.
2. **Contextual Translation Barrier**: The system translates internally for accounting structures (e.g., Telugu *"బట్టలు"* or Hindi *"कपड़ा"* $\to$ Master SKU `COT-SHIRT-M`), but replies to the merchant in their exact conversational dialect.
3. **No Synthetic Jargon**: Outbound WhatsApp prompts must use everyday bazaar commercial terms:
   - *Hindi*: Use *"उधार"* (Udhaar), *"बिक्री"* (Bikri), *"जमा"* (Jama).
   - *Telugu*: Use *"అప్పు / బాకీ"* (Appu/Baaki), *"అమ్మకం"* (Ammakam), *"ఖాతా"* (Khata).

### 6.2 The Human-in-the-Loop Safety Gate Trigger Matrix
Automatic ledger commits are strictly blocked. Transactions must present interactive **[Confirm]** buttons if any of the following conditions are met:

| Condition Trigger | Threshold / Criteria | System Action |
| :--- | :--- | :--- |
| **Model Confidence** | Extraction confidence $< 0.85$ | Require explicit confirmation with highlighted warnings |
| **Credit / Udhaar Creation** | Any `credit_amount > 0` | Mandatory review displaying party's new total debt |
| **High Value Transaction** | Total amount $\ge ₹5,000.00$ | Visual confirmation card with full split breakdown |
| **Low / Negative Stock** | Order quantity $>$ Available inventory | Warning flag; allows negative stock only if merchant policy allows |
| **New Counterparty / SKU** | Party or item not found in master catalog | Prompts merchant to confirm auto-creation of party/SKU |

---

## 7. Verification, Legal Compliance & Audit Standards

### 7.1 Non-GST Commercial Invoicing Disclaimer
- **Compliance Policy**: To protect small textile artisans and micro-enterprises operating below the mandatory registration threshold, all generated invoices must explicitly state:
  > *"Commercial Invoice / Delivery Challan. Not a GST Tax Invoice. Issued for trade evidence and commercial settlement."*

### 7.2 GSTIN Checksum Mathematical Verification (Mod-36 Luhn)
- For registered businesses, GSTIN validation must be performed deterministically without AI involvement:
  ```python
  # 15-character structure: 2-digit State + 10-char PAN + 1 Entity + 'Z' + 1 Checksum
  # Checksum is computed over characters 1..14 using radix-36 weighted factors.
  def verify_gstin_checksum(gstin: str) -> bool:
      CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
      factor = 1
      total = 0
      for c in gstin[:14]:
          code = CHARS.index(c)
          val = code * factor
          factor = 1 if factor == 2 else 2
          total += (val // 36) + (val % 36)
      check_digit = (36 - (total % 36)) % 36
      return gstin[14] == CHARS[check_digit]
  ```

### 7.3 Independent Lender Verification Protocol
Lending institutions and NBFCs verify merchant statements at `/verify`:
1. **Input**: Verifier uploads the PDF report or enters `Attestation ID`.
2. **Digest Computation**: The portal extracts raw financial tables and re-computes the canonical SHA-256 digest.
3. **Public Key Retrieval**: The portal fetches the designated public key version from Google Cloud KMS.
4. **Cryptographic Verification**:
   $$\text{Verify}_{\text{KMS Public Key}}(H_{\text{computed}}, \text{Signature}) \in \{\text{VALID}, \text{TAMPERED}, \text{REVOKED}\}$$
5. **Verdict**: Displays an immutable green verification badge with timestamp and signing authority, or a red tampering alert if a single rupee was modified.

---

## 8. Summary Table: Service Architecture At a Glance

| Pillar | Specification / Technology | Verification Standard |
| :--- | :--- | :--- |
| **Cloud Infrastructure** | Google Cloud Run, Cloud SQL, Cloud Storage, KMS | Zero-traffic staging deployments, IAM least-privilege |
| **Authoritative Ledger** | PostgreSQL 17 (Drizzle / SQLAlchemy Core) | ACID `SELECT FOR UPDATE`, Double-Entry $\sum Dr \equiv \sum Cr$ |
| **Multimodal AI Core** | Gemini 3.8 Flash (Schema 1.1) | Pydantic validation, Verbatim vernacular audio transcripts |
| **Decisions & Routing** | Gemini Decisions API (`gemini-decision-1`) | Calibrated Choice Question disambiguation |
| **Voice Synthesis** | Gemini 3.8 Flash TTS / Gemini Omni | Spoken Telugu/Hindi voice notes back to WhatsApp |
| **Messaging Channel** | Meta WhatsApp Business Cloud API (Graph v21.0) | HMAC-SHA256 signature verification, 1-phone-1-merchant RLS |
| **Attestation & Security**| Google Cloud KMS (RSA-2048 Asymmetric) | Public SHA-256 independent verification portal |

---
*This protocol is an authoritative specification for Clandexis Invisible ERP. All future service enhancements must conform to the invariants and execution standards established herein.*
