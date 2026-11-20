# Clandexis (Invisible ERP) — WhatsApp Communication Workflow Specification
**Target Platform:** WhatsApp Business Cloud API (Meta Graph API v21.0)  
**System Architecture:** Zero-UI Conversational ERP for Indian MSMEs  
**Project:** Startup Buildathon India 2026 | Google Cloud & Gemini 3.8  
**Document Version:** 1.0.0 (Production Architecture)

---

## 1. Executive Overview & Architecture Principles

**Clandexis (Invisible ERP)** transforms WhatsApp into a full-featured, Zero-UI enterprise resource planning system for 63+ million Indian Micro, Small, and Medium Enterprises (MSMEs). Merchants do not download apps, configure complex web dashboards, or navigate accounting menus. Instead, they interact naturally in their mother tongue (Hinglish, Hindi, Tamil, Telugu, Gujarati, etc.) via:
1. **Audio Voice Notes (`.ogg`):** E.g., *"Ramesh ko 10 meter cotton suit diya ₹3,500 ka, ₹1,000 cash diya baaki udhaar likh lo."*
2. **Photos of Handwritten Kacha Bills (`.jpg` / `.png`):** Rough slip pad entries photographed from a mobile camera.
3. **Natural Text Inputs:** Fast shorthand messages or WhatsApp quick replies.
4. **Interactive 1-Tap Buttons:** WhatsApp interactive action buttons for risk mitigation and confirmation.

### Core Architectural Axioms
* **Channel Adapter Pattern (FR-06 & FR-07):** Decouples the external messaging platform (Meta Cloud API) from internal reasoning, validation, and database operations using canonical envelope models (`InboundMessage` and `OutboundMessage`).
* **Deterministic Financial Invariant Validation:** Generative AI (`gemini-3.8-flash`) extracts structured data, but **never** writes directly to the financial ledger without passing strict mathematical equality checks:
  $$\sum (\text{Quantity} \times \text{Unit Price}) == \sum \text{Line Items} == \text{Total Amount} == \sum (\text{Cash} + \text{Online} + \text{Credit})$$
* **Human-in-the-Loop Safety Gate:** High-risk transactions (extraction confidence $< 0.85$, or credit balance / *udhaar* $> ₹5,000$, or math discrepancies) are staged as `AWAITING_CONFIRMATION` and prompt the merchant with 1-tap interactive WhatsApp buttons before ledger commitment.
* **Fast-ACK & Asynchronous Worker Model:** The inbound webhook returns an HTTP `200 OK` within $< 300\text{ms}$ after HMAC verification to satisfy Meta's strict 3-second timeout constraint. Downstream multimodal AI reasoning, PDF compilation, and audio synthesis run asynchronously.
* **Multimodal Vernacular Feedback Loop:** Every recorded transaction delivers an itemized text invoice, a downloadable PDF invoice, and an optional synthesized audio readback (`gemini-3.8-flash-tts`) in the merchant's colloquial dialect.

---

## 2. High-Level WhatsApp Communication Architecture

```mermaid
flowchart TD
    subgraph External["Meta WhatsApp Cloud Infrastructure"]
        User["MSME Merchant (WhatsApp Mobile)"]
        MetaCloud["Meta Graph API v21.0 Webhooks & Send API"]
    end

    subgraph Ingestion["Track 1: Webhook & Ingestion Layer (FastAPI / Cloud Run)"]
        Security["HMAC-SHA256 Signature Verification\n(X-Hub-Signature-256)"]
        Normalizer["Channel Adapter Normalizer\n(MetaMessage -> InboundMessage)"]
        MediaClient["Meta CDN Media Downloader\n(.ogg voice & .jpg bills)"]
        Dedupe["Idempotency Cache\n(Redis / Memory message_id dedupe)"]
    end

    subgraph Reasoning["Track 2: Agent Orchestration (Gemini 3.5 & 3.8)"]
        Router["Intent Router (gemini-3.5-flash-lite)\nSALE | PURCHASE | QUERY | ATTESTATION"]
        Extractor["Multimodal Extractor (gemini-3.8-flash)\nStrict JSON Schema + Voice/Image Context"]
        MathGuard["Deterministic Invariant Validator\nvalidate_split_math.py"]
        SafetyGate{"Safety Gate Check\nConf >= 0.85 & Udhaar <= ₹5,000?"}
    end

    subgraph Storage["Track 3: Atomic Ledger & Documents (Firestore & Cloud KMS)"]
        FirestoreCommit[("Firestore Multi-Doc Atomic Write\nMerchants | Parties | Inventory | Txns")]
        PDFGen["ReportLab PDF Engine\nGST/Non-GST Invoice Generator"]
        KMSSign["Cloud KMS Cryptographic Signer\nSHA-256 Asymmetric Credit Digest"]
        TTS["Gemini 3.8 Flash TTS\nVernacular Speech Synthesis"]
    end

    subgraph Dispatch["Outbound Messaging"]
        OutboundDispatcher["MetaWhatsAppService Outbound Dispatcher\n(POST /v21.0/{phone_number_id}/messages)"]
    end

    %% Inbound Flow
    User -->|"Voice Note / Photo / Text"| MetaCloud
    MetaCloud -->|"HTTP POST /webhook"| Security
    Security -->|"Valid Signature"| Normalizer
    Normalizer --> Dedupe
    Dedupe -->|"New Message"| MediaClient
    MediaClient -->|"InboundMessage Envelope"| Router
    Router --> Extractor
    Extractor --> MathGuard
    MathGuard --> SafetyGate

    %% Safety Gate Branches
    SafetyGate -->|"Pass (Low Risk)"| FirestoreCommit
    SafetyGate -->|"Fail / High Risk"| OutboundDispatcher
    OutboundDispatcher -->|"1-Tap Confirmation Buttons"| MetaCloud
    MetaCloud --> User

    %% Commitment Flow
    FirestoreCommit --> PDFGen
    FirestoreCommit --> TTS
    PDFGen --> OutboundDispatcher
    TTS --> OutboundDispatcher
    OutboundDispatcher -->|"Text Summary + PDF + Audio"| MetaCloud
    MetaCloud --> User
```

---

## 3. End-to-End Inbound & Outbound Sequence Flows

### Flow A: Standard Happy Path (Auto-Commit Transaction)
*Scenario:* Merchant sends a voice note: *"Sold 5 bags of basmati rice to Gupta Stores at ₹2,200 each, received ₹5,000 cash and balance ₹6,000 udhaar."*  
*Parameters:* Confidence = 0.94, Credit = ₹6,000 (Notice: if Udhaar > ₹5,000 it triggers Safety Gate; in this example, let's trace Udhaar = ₹4,000, Total = ₹11,000, Cash = ₹7,000 so it auto-commits).

```mermaid
sequenceDiagram
    autonumber
    actor Merchant as MSME Merchant (WhatsApp)
    participant Meta as Meta Cloud API (v21.0)
    participant Webhook as Webhook Service (FastAPI)
    participant Agent as Agent Orchestrator (Gemini)
    participant DB as Google Cloud Firestore
    participant PDF as PDF & TTS Services

    Merchant->>Meta: Sends Audio Voice Note (.ogg)
    Meta->>Webhook: POST /webhook (X-Hub-Signature-256, payload)
    Note over Webhook: 1. Verify HMAC-SHA256 signature<br/>2. Return HTTP 200 OK (<300ms)
    Webhook-->>Meta: HTTP 200 OK (Acknowledge)

    par Async Background Processing
        Webhook->>Meta: POST /{phone_number_id}/messages (status="read")
        Meta-->>Merchant: Blue Ticks (Delivered & Read)
    and
        Webhook->>Meta: GET /{media_id} -> Download .ogg audio bytes
        Meta-->>Webhook: Binary audio payload
        Webhook->>Agent: Route InboundMessage(type=AUDIO, bytes)
    end

    Agent->>Agent: gemini-3.5-flash-lite (Intent: RECORD_TRANSACTION)
    Agent->>Agent: gemini-3.8-flash (Extract JSON schema with audio context)
    Agent->>Agent: validate_split_math (Sum Items == Total == Cash + Online + Credit)
    Note over Agent: Math Invariant Valid<br/>Confidence: 0.96 >= 0.85<br/>Credit: ₹4,000 <= ₹5,000 (Auto-Commit)

    Agent->>DB: Atomic Multi-Document Transaction:
    Note over DB: 1. Update Merchant sales stats<br/>2. Update Gupta Stores party ledger (Udhaar +₹4,000)<br/>3. Decrement Inventory (5 bags basmati)<br/>4. Insert Transaction (Status: COMMITTED)
    DB-->>Agent: Transaction ID: TXN-2026-9812

    par Output Artifact Generation
        Agent->>PDF: Generate Invoice PDF (ReportLab)
        PDF-->>Agent: Signed PDF URL (GCS)
    and
        Agent->>Agent: gemini-3.8-flash-tts (Synthesize Hindi Voice Readback)
        Agent-->>Agent: Voice Note Audio URL
    end

    Agent->>Webhook: POST /api/v1/send (OutboundMessage)
    Webhook->>Meta: POST /{phone_number_id}/messages (Text breakdown)
    Webhook->>Meta: POST /{phone_number_id}/messages (Document: invoice.pdf)
    Webhook->>Meta: POST /{phone_number_id}/messages (Audio: readback.ogg)
    Meta-->>Merchant: WhatsApp Messages Delivered
```

---

### Flow B: Human-in-the-Loop Safety Gate Flow (High Credit / Low Confidence)
*Scenario:* Merchant sends a photo of a messy Kacha bill totaling ₹18,500 with ₹12,000 credit (*udhaar*), or extraction confidence is 0.78.

```mermaid
sequenceDiagram
    autonumber
    actor Merchant as MSME Merchant (WhatsApp)
    participant Meta as Meta Cloud API (v21.0)
    participant Webhook as Webhook Service (FastAPI)
    participant Agent as Agent Orchestrator (Gemini)
    participant DB as Google Cloud Firestore

    Merchant->>Meta: Sends Photo of Handwritten Bill
    Meta->>Webhook: POST /webhook (Payload with image media_id)
    Webhook-->>Meta: HTTP 200 OK (<300ms)

    Webhook->>Meta: Download image binary bytes
    Webhook->>Agent: Process InboundMessage(type=IMAGE)

    Agent->>Agent: gemini-3.8-flash extraction
    Note over Agent: Total: ₹18,500 | Credit: ₹12,000<br/>Trigger: Credit > ₹5,000 OR Confidence < 0.85
    
    Agent->>DB: Stage Draft in Firestore (Status: AWAITING_CONFIRMATION)
    DB-->>Agent: Draft ID: DRAFT-7721

    Agent->>Webhook: POST /api/v1/send (Interactive 1-Tap Buttons)
    Webhook->>Meta: POST /{phone_number_id}/messages (interactive: button)
    Meta-->>Merchant: Interactive Card Displayed on WhatsApp:
    Note over Merchant: ⚠️ High Credit Confirmation:<br/>Party: Sharma Cloth Store<br/>Total: ₹18,500 | Udhaar: ₹12,000<br/>[ Confirm (हाँ) ]  [ Cancel (रद्द करें) ]

    alt Merchant Taps Confirm
        Merchant->>Meta: Taps "[ Confirm (हाँ) ]" (interactive_id: CONFIRM_DRAFT-7721)
        Meta->>Webhook: POST /webhook (type=interactive_button)
        Webhook-->>Meta: HTTP 200 OK
        Webhook->>Agent: Process InboundMessage(interactive_id="CONFIRM_DRAFT-7721")
        Agent->>DB: Atomic Commit Draft -> COMMITTED
        Agent->>Webhook: OutboundMessage("✅ Transaction Committed! Invoice generated.")
        Webhook->>Meta: Dispatches confirmation & PDF to WhatsApp
        Meta-->>Merchant: Delivers PDF Invoice & Ledger update
    else Merchant Taps Cancel
        Merchant->>Meta: Taps "[ Cancel (रद्द करें) ]" (interactive_id: CANCEL_DRAFT-7721)
        Meta->>Webhook: POST /webhook (type=interactive_button)
        Webhook-->>Meta: HTTP 200 OK
        Webhook->>Agent: Process InboundMessage(interactive_id="CANCEL_DRAFT-7721")
        Agent->>DB: Update Status -> REJECTED
        Agent->>Webhook: OutboundMessage("❌ Transaction cancelled. No ledger updates made.")
        Webhook->>Meta: Dispatches cancellation note
        Meta-->>Merchant: "Radd kar diya gaya hai."
    end
```

---

### Flow C: Real-Time Ledger Query Flow (*"Sharma ji ka kitna baaki hai?"*)
*Scenario:* Merchant asks verbally: *"Sharma ji ka kitna baaki hai aur pichla payment kab aaya tha?"*

```mermaid
sequenceDiagram
    autonumber
    actor Merchant as MSME Merchant (WhatsApp)
    participant Meta as Meta Cloud API (v21.0)
    participant Webhook as Webhook Service
    participant Agent as Agent Orchestrator
    participant DB as Firestore Ledger

    Merchant->>Meta: Sends Audio Voice Note
    Meta->>Webhook: POST /webhook
    Webhook-->>Meta: HTTP 200 OK
    Webhook->>Agent: InboundMessage(type=AUDIO)

    Agent->>Agent: gemini-3.5-flash-lite (Intent: QUERY_LEDGER)
    Agent->>Agent: gemini-3.8-flash (Extract Entity: name="Sharma", query_type="BALANCE")
    Agent->>DB: Query `parties` where name matches "Sharma" and tenant_id=Merchant
    DB-->>Agent: Party Record: Sharma Trading Co., Current Balance: ₹14,200, Last Paid: 18 Nov (₹5,000)

    Agent->>Agent: Compose Vernacular Response (Hindi/Hinglish):
    Note over Agent: "Sharma Trading Co. ka kul ₹14,200 baaki hai.<br/>Unhone aakhiri payment ₹5,000 ka 18 Nov ko kiya tha."

    Agent->>Webhook: POST /api/v1/send (Text & Optional Audio)
    Webhook->>Meta: POST /{phone_number_id}/messages
    Meta-->>Merchant: Delivers WhatsApp balance summary
```

---

### Flow D: Cryptographic Credit Attestation Flow (Loan & MSME Verification)
*Scenario:* Merchant requests a credit health certificate to show a local bank/NBFC for a working capital loan.

```mermaid
sequenceDiagram
    autonumber
    actor Merchant as MSME Merchant (WhatsApp)
    participant Meta as Meta Cloud API (v21.0)
    participant Webhook as Webhook Service
    participant Agent as Agent Orchestrator
    participant DB as Firestore
    participant KMS as Google Cloud KMS
    participant PDF as PDF Engine

    Merchant->>Meta: Text: "Bank loan ke liye credit report bhejo"
    Meta->>Webhook: POST /webhook
    Webhook-->>Meta: HTTP 200 OK
    Webhook->>Agent: InboundMessage(type=TEXT)

    Agent->>Agent: Intent: ISSUE_CREDIT_ATTESTATION
    Agent->>DB: Fetch 90-day cash flow, turnover, collection velocity, and debtor list
    DB-->>Agent: Aggregated Ledger Metrics (Turnover: ₹4.8L, Defaults: 0)

    Agent->>Agent: Compute SHA-256 Digest of the structured credit summary
    Agent->>KMS: Asymmetric Sign SHA-256 digest with HSM private key (clandexis-ledger-signer)
    KMS-->>Agent: RSA-PSS 2048-bit Signature + Verification Public Key ID

    Agent->>PDF: Generate Official Credit Attestation Certificate (Embedded QR Code + KMS Signature)
    PDF-->>Agent: PDF Certificate URL

    Agent->>Webhook: OutboundMessage(type=DOCUMENT, url=PDF, caption="Aapka verified credit report taiyar hai.")
    Webhook->>Meta: POST /{phone_number_id}/messages
    Meta-->>Merchant: Official Signed Credit Certificate delivered on WhatsApp
```

---

## 4. Detailed Stage-by-Stage Implementation

### Stage 1: Inbound Webhook Verification & HMAC Security
Meta WhatsApp Cloud API sends all inbound events to `https://<domain>/webhook`.

1. **Meta Challenge Handshake (`GET /webhook`):**
   * Parameters received: `hub.mode`, `hub.challenge`, `hub.verify_token`.
   * The webhook must verify that `hub.verify_token == settings.META_VERIFY_TOKEN`.
   * Return `hub.challenge` as `text/plain` with HTTP 200.

2. **Cryptographic Payload Verification (`POST /webhook`):**
   * Meta sends the signature in the header: `X-Hub-Signature-256: sha256=<hash>`.
   * The webhook computes:
     $$\text{Expected Signature} = \text{HMAC-SHA256}(\text{META\_APP\_SECRET}, \text{raw\_request\_body})$$
   * Secure constant-time comparison prevents timing attacks. If mismatched, returns `HTTP 403 Forbidden`.

3. **Fast Acknowledgment:**
   * Returns `{"status": "ok"}` in $< 300\text{ms}$.
   * Hands off payload to FastAPI `BackgroundTasks`.

---

### Stage 2: Channel Adaptation & Media Downloading
Translates vendor-specific Meta JSON into the universal `InboundMessage` envelope:

| Meta Field | Mapped To `InboundMessage` | Purpose |
| :--- | :--- | :--- |
| `entry[0].changes[0].value.messages[0].id` | `message_id` | Deduplication & idempotency key |
| `entry[0].changes[0].value.messages[0].from` | `sender_phone` & `tenant_id` | MSME merchant E.164 phone identifier |
| `messages[0].type` (`audio`, `image`, `text`, `interactive`) | `message_type` | Routes payload to appropriate Gemini pipeline |
| `messages[0].audio.id` / `messages[0].image.id` | `media_id` | Meta CDN asset identifier |
| `messages[0].interactive.button_reply.id` | `interactive_id` | Button click action payload (e.g., `CONFIRM_TXN_...`) |

**Media Asset Download Protocol:**
* If `media_id` is present, the service makes an authorized `GET` to:
  `https://graph.facebook.com/v21.0/{media_id}` with `Authorization: Bearer {META_WHATSAPP_TOKEN}`.
* Resolves the transient CDN download link (`url`), then downloads the binary byte stream.
* Dispatches a blue-tick `read` receipt back to WhatsApp so the user has immediate visual feedback.

---

### Stage 3: Intent Routing (`gemini-3.5-flash-lite`)
A lightweight, high-velocity model categorizes the merchant's input:
* `RECORD_TRANSACTION`: Voice note or image describing sale, purchase, expense, or udhaar.
* `QUERY_LEDGER`: Balance check, customer statement, inventory status.
* `ISSUE_CREDIT_ATTESTATION`: Request for bank certificate or credit proof.
* `CONFIRM_REJECT_ACTION`: Tapped confirmation or text reply confirming staged transaction.
* `GENERAL_HELP`: Usage instructions, commands, or customer onboarding.

---

### Stage 4: Multimodal Extraction (`gemini-3.8-flash`)
The multimodal audio/image payload is supplied directly to `gemini-3.8-flash` with strict schema constraints (`models/extraction_schema.json`).

**Extracted JSON Structure:**
```json
{
  "merchant_phone": "+919876543210",
  "party_name": "Gupta Stores",
  "party_phone": "+919811223344",
  "party_type": "CUSTOMER",
  "transaction_type": "SALE",
  "line_items": [
    {
      "name": "Basmati Rice 25kg",
      "quantity": 5.0,
      "unit": "BAG",
      "unit_price": 2200.0,
      "line_total": 11000.0
    }
  ],
  "total_amount": 11000.0,
  "payment_split": {
    "cash_amount": 7000.0,
    "online_amount": 0.0,
    "credit_amount": 4000.0
  },
  "confidence_score": 0.96,
  "reasoning": "Clear Hindi voice note specifying 5 bags at 2200 with 7000 cash and 4000 credit.",
  "vernacular_readback": "Gupta Stores ko 5 bag basmati rice becha ₹11,000 mein. ₹7,000 cash mil gaya aur ₹4,000 udhaar darj ho gaya."
}
```

---

### Stage 5: Deterministic Invariant Validation & Safety Gate
Before any database write occurs, `agent-orchestrator/validate_split_math.py` executes:

1. **Item Calculation Invariant:**
   $$\sum (\text{item.quantity} \times \text{item.unit\_price}) \stackrel{?}{=} \sum \text{item.line\_total} \stackrel{?}{=} \text{total\_amount}$$
2. **Payment Split Invariant:**
   $$\text{cash\_amount} + \text{online\_amount} + \text{credit\_amount} \stackrel{?}{=} \text{total\_amount}$$

**Safety Gate Evaluation Matrix:**
```
IF Invariant Checks Fail:
    -> Reject extraction, request merchant re-confirmation or manual edit.
ELSE IF (confidence_score < 0.85) OR (credit_amount > ₹5,000.00):
    -> Stage in Firestore as status = "AWAITING_CONFIRMATION"
    -> Dispatch 1-Tap Interactive Buttons to WhatsApp
ELSE:
    -> Status = "COMMITTED"
    -> Execute Atomic Ledger Write
```

---

### Stage 6: Atomic Ledger Mutation & Artifact Compilation
For `COMMITTED` transactions, Firestore executes a multi-document atomic write across:
1. `merchants/{tenant_id}`: Increments total sales count, updates rolling turnover.
2. `parties/{party_id}`: Increases customer's outstanding balance (*udhaar*) by `credit_amount`.
3. `inventory/{sku_id}`: Decrements available stock units by item quantity.
4. `transactions/{txn_id}`: Inserts immutable transaction audit log.

Simultaneously:
* **PDF Service (`pdf-service/`):** Generates a compliant A4/thermal GST invoice via ReportLab and uploads it to Google Cloud Storage.
* **Audio Synthesis:** Converts `vernacular_readback` into natural vernacular voice audio via `gemini-3.8-flash-tts`.

---

### Stage 7: Outbound WhatsApp Dispatching
The Webhook Service translates `OutboundMessage` into Meta Graph API v21.0 payload format:
* **Endpoint:** `POST https://graph.facebook.com/v21.0/{META_PHONE_NUMBER_ID}/messages`
* **Authorization:** `Bearer {META_WHATSAPP_TOKEN}`

---

## 5. WhatsApp Meta API Payloads & Contracts

### A. Inbound WhatsApp Voice Note Webhook (from Meta)
```json
{
  "object": "whatsapp_business_account",
  "entry": [
    {
      "id": "109827364512983",
      "changes": [
        {
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {
              "display_phone_number": "918000012345",
              "phone_number_id": "987654321098765"
            },
            "contacts": [
              {
                "profile": { "name": "Rajesh Kumar (Surat Textiles)" },
                "wa_id": "919876543210"
              }
            ],
            "messages": [
              {
                "from": "919876543210",
                "id": "wamid.HBgMOTE5ODc2NTQzMjEwFQIAEhggM0E2Qzk1RDc...",
                "timestamp": "1774163400",
                "type": "voice",
                "voice": {
                  "mime_type": "audio/ogg; codecs=opus",
                  "sha256": "3e23e8160039594a33894f6564e1b1348bbd7a0088d42c4acb2732...",
                  "id": "112233445566778",
                  "voice": true
                }
              }
            ]
          },
          "field": "messages"
        }
      ]
    }
  ]
}
```

---

### B. Outbound 1-Tap Interactive Confirmation Button (to Meta)
Dispatched when the Safety Gate intercepts high credit or moderate confidence:

```json
{
  "messaging_product": "whatsapp",
  "recipient_type": "individual",
  "to": "919876543210",
  "type": "interactive",
  "interactive": {
    "type": "button",
    "header": {
      "type": "text",
      "text": "⚠️ Confirm High Credit Sale"
    },
    "body": {
      "text": "Party: *Gupta Stores*\nTotal: *₹11,000*\nCash Paid: *₹7,000*\nUdhaar (Credit): *₹4,000*\n\nKya ye transaction khate mein darj karein?"
    },
    "footer": {
      "text": "Clandexis Invisible ERP"
    },
    "action": {
      "buttons": [
        {
          "type": "reply",
          "reply": {
            "id": "CONFIRM_DRAFT_8821",
            "title": "हाँ, Confirm karein"
          }
        },
        {
          "type": "reply",
          "reply": {
            "id": "CANCEL_DRAFT_8821",
            "title": "रद्द करें (Cancel)"
          }
        }
      ]
    }
  }
}
```

---

### C. Outbound PDF Invoice Attachment (to Meta)
```json
{
  "messaging_product": "whatsapp",
  "recipient_type": "individual",
  "to": "919876543210",
  "type": "document",
  "document": {
    "link": "https://storage.googleapis.com/clandexis-invoices/INV-2026-0042.pdf",
    "filename": "Invoice_GuptaStores_0042.pdf",
    "caption": "📄 Bill #0042 for Gupta Stores (₹11,000). Aapka record update ho gaya hai."
  }
}
```

---

## 6. Transaction State Machine Lifecycle

```mermaid
stateDiagram-v2
    [*] --> RECEIVED: Webhook Ingested & Verified
    RECEIVED --> EXTRACTED: Gemini 3.8 Multimodal Extraction Complete
    
    EXTRACTED --> MATH_VALIDATION: validate_split_math Invariant Check
    MATH_VALIDATION --> FAILED_INVARIANT: Math Mismatch
    FAILED_INVARIANT --> OUTBOUND_RETRY_PROMPT: Ask Merchant to Clarify
    
    MATH_VALIDATION --> EVALUATE_GATE: Math Matches Perfectly
    
    EVALUATE_GATE --> AWAITING_CONFIRMATION: Conf < 0.85 OR Udhaar > ₹5,000
    AWAITING_CONFIRMATION --> COMMITTED: Merchant Taps [ Confirm ]
    AWAITING_CONFIRMATION --> REJECTED: Merchant Taps [ Cancel ] / 24hr Timeout
    
    EVALUATE_GATE --> COMMITTED: Conf >= 0.85 AND Udhaar <= ₹5,000
    
    COMMITTED --> ATOMIC_WRITE: Multi-Doc Firestore Transaction
    ATOMIC_WRITE --> ARTIFACT_GEN: PDF Generation + TTS Synthesis
    ARTIFACT_GEN --> DISPATCHED: WhatsApp Invoice & Audio Delivered
    DISPATCHED --> [*]
    REJECTED --> [*]
```

---

## 7. Security, Reliability & Edge Case Engineering

### 1. Webhook Deduplication & Idempotency
* **Problem:** Meta Cloud API guarantees *at-least-once* delivery. Network fluctuations may cause Meta to retry the same webhook payload multiple times.
* **Mitigation:** The service extracts `messages[0].id` (e.g. `wamid.HBgMOTE5...`). It checks a fast key-value store (Redis or in-memory LRU cache with a 24-hour TTL). If `message_id` already exists, the service returns `HTTP 200 OK` immediately and discards duplicate processing.

### 2. Meta Media URL Expiration
* **Problem:** Media download URLs returned by Meta Graph API have an ephemeral lifespan (~5 minutes).
* **Mitigation:** Media downloading is executed immediately in the first step of the async background worker, storing binary buffers in Google Cloud Storage (`gs://clandexis-media-staging/`) or passing bytes directly to the Gemini Multimodal File API.

### 3. Dialect Robustness & Background Noise
* **Problem:** Indian MSME bazaars have loud ambient noise (traffic, loom motors, crowd chatter).
* **Mitigation:** Gemini 3.8's native audio understanding leverages temporal context without needing separate lossy Whisper transcription steps. Prompts explicitly instruct the model to account for Indian conversational nuances (e.g., *"sau"* = 100, *"hazaar"* = 1,000, *"peti"* = 100,000 / boxes, *"khoka"*).

### 4. Zero Data Loss During Failures
* **Problem:** If PDF compilation or TTS fails, does the financial transaction fail?
* **Decoupled Architecture:** Ledger commitment is primary. If PDF rendering or TTS audio generation experiences a transient failure, the ledger transaction remains committed, and a text-only confirmation is immediately delivered with a deferred PDF download link.

---

## 8. Summary Checklist for Integration Testing

| Step | Test Objective | Tool / Test Case | Success Criteria |
| :--- | :--- | :--- | :--- |
| **1** | Webhook Handshake Verification | `GET /webhook?hub.mode=subscribe&...` | HTTP 200 with raw challenge string |
| **2** | HMAC-SHA256 Security Guard | `POST /webhook` with invalid signature | HTTP 403 Forbidden |
| **3** | Valid Webhook Ingestion | `POST /webhook` with mock audio/text | HTTP 200 response in $< 300\text{ms}$ |
| **4** | Voice Note Processing | Simulated `.ogg` Opus audio payload | Correct intent and items extracted |
| **5** | Split Math Invariant | `validate_split_math.py` unit tests | Exact equality verified across all splits |
| **6** | Safety Gate Intercept | Udhaar = ₹6,500 or Confidence = 0.81 | Status = `AWAITING_CONFIRMATION` + Buttons |
| **7** | Interactive Button Callback | Tapping `CONFIRM_DRAFT_...` | Status transitions to `COMMITTED` |
| **8** | Ledger Commit | Firestore atomic batch verification | Party balance and inventory updated |
| **9** | Outbound Delivery | Meta Send API (`/api/v1/send`) | Delivered to mobile device with PDF |
