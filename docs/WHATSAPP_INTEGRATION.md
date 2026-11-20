# WhatsApp integration

The signed webhook checks the incoming business phone ID against META_PHONE_NUMBER_ID. Each approved sender is normalized to E.164 and resolved using WHATSAPP_DEMO_PHONE_MAP, an explicit JSON mapping from sender phone to existing merchant document ID. Empty configuration rejects everyone. Duplicate normalized phones, invalid IDs, and multiple phones mapped to the same merchant fail closed. No merchant is automatically provisioned and no shared demo fallback exists.

Example configuration for isolated emulator merchants is WHATSAPP_DEMO_PHONE_MAP='{"+918001111111":"merchant-a","+919002222222":"merchant-b"}'. Both merchant documents must already exist and their authorized_phones must include their respective sender for the canonical ledger authorization check. The WhatsApp business/test phone is a separate transport identity and cannot send as a merchant.

Each merchant has one persisted conversations/whatsapp document and a messages subcollection. The state holds active_candidate_id, latest_question, missing_fields, detected_language, script_preference, candidate_revision and expires_at. Every stored event includes the canonical inbound message ID, sender, merchant, receive timestamp and source payload, plus its reply. Ledger candidates retain original_extraction and review_history. A processing lease serializes events across instances. Cached event replies survive restart. A process dying mid-event releases through the lease timeout on a subsequent retry. Conversation context never scans unrelated dashboard candidates.

Clarifications progressively review the same candidate through the existing ledger review interface. Known buyers, item names, quantities, prices and payments are preserved. Unsupported requests and unrelated new sales cannot overwrite pending context. Use Continue to repeat the current question or the candidate-specific Cancel button before resending a new transaction. Only a valid candidate gets Confirm buttons. Bare YES/NO cannot commit a WhatsApp sale.

## Runtime credentials

Put credentials in the ignored repository .env or inject them into the process. .env.example is a tracked template and is never loaded as credentials. Keep the runtime file readable only by your user.

| Runtime setting | Accepted alias |
| --- | --- |
| META_VERIFY_TOKEN | WHATSAPP_VERIFY_TOKEN |
| META_WHATSAPP_TOKEN | WHATSAPP_ACCESS_TOKEN |
| META_PHONE_NUMBER_ID | WHATSAPP_PHONE_NUMBER_ID |
| META_APP_SECRET | WHATSAPP_APP_SECRET or META-WHATSAPP_APP_SECRET |

The primary name takes precedence, including an explicitly empty value. The application ID is not required by the existing send or HMAC verification path. Prefer underscore names in shell environments.

The browser demo launcher explicitly disables Meta settings. Start the emulator and seed through scripts/local_demo.sh first, then stop only the web server. Load the same demo session and export FIRESTORE_EMULATOR_HOST=127.0.0.1:8185, GCP_PROJECT_ID=demo-clandexis-local, TRUSTED_ORIGINS=http://127.0.0.1:8180 and INVOICE_LOCAL_ROOT pointing to the demo invoices directory. Unset exported empty META_APP_SECRET, META_WHATSAPP_TOKEN, META_PHONE_NUMBER_ID and META_VERIFY_TOKEN so .env can supply them. Launch .venv/bin/uvicorn webhook_service.main:app --host 127.0.0.1 --port 8180.

Meta needs a reachable HTTPS callback ending in /webhook, the matching verification token, and a messages subscription. Register that URL in the Meta app. POST requests must carry a valid HMAC signed with the app secret. Configure the sender binding before testing.

## Media and confirmation

Audio, image and PDF IDs are resolved and downloaded before extraction. Bytes and normalized MIME type reach the existing Gemini interpreter. Downloads have a 16 MiB limit. Failed or empty downloads request resubmission and never stage a caption-only transaction. Confirmation routes to the persisted candidate. Documents are generated after committed sales.

Local invoice files are available through authenticated dashboard downloads. WhatsApp document delivery additionally requires the existing artifact URL resolver and an accessible signed storage URL.

## Verification

Run tests/run_ledger_tests.sh with Java 21 and firebase-tools. The application emulator suite verifies a signed voice callback, downloaded fixture bytes reaching Gemini inline media, a sender mapped to a separate merchant, ledger commit, PDF generation, duplicate confirmation, repayment and unauthorized callers. Meta transport and Gemini responses are stubbed in the deterministic voice test. Live Meta delivery requires separate verification.

## Phone MVP emulator verification

The completed release run used the real Firestore emulator with Meta delivery and Gemini responses stubbed for repeatable financial assertions. It passed 134 tests with zero failures and zero skips. The dashboard production build and pip dependency check also passed.

Executed commands on this Mac were:

```bash
PATH="$HOME/.cache/clandexis-tools/jdk-21.0.13.1+2-jre/Contents/Home/bin:$PATH" FIREBASE_BIN="$HOME/.cache/clandexis-tools/fb/node_modules/.bin/firebase" bash tests/run_ledger_tests.sh -rP
(cd dashboard && npm run build)
.venv/bin/python -m pip check
```

The final run created these isolated test accounts. Emulator fixtures are disposable and distinct from the existing personal demo.

| Test sender | Merchant document | Initial shirts | Final shirts | Transactions | Raju credit |
| --- | --- | --- | --- | --- | --- |
| +918001111111 | merchants/+915582429019 | 300 | 290 | 1 | INR 1500 |
| +919002222222 | merchants/phone_919002222222_485caf4b7f234e48832b2b27a38b7e0e | 200 | 200 | 0 | INR 0 |

Each account stored its own conversations/whatsapp document and messages subcollection. Phone A completed the same candidate using separate price and payment replies. Phone B retained a separate pending Hindi candidate. Foreign-candidate confirmation, bare YES, duplicate confirmations and a PDF ledger request produced no extra posting. A separate Python process read the active candidate after the first reply. Concurrent confirmation events posted once. A mixed-sender webhook batch preserved only each phone's own source payload in its history.

Busy concurrent messages are recorded as DEFERRED and ask the sender to retry. The processing lease expires after five minutes if a process dies. No background queue or automatic replay worker has been added.

The existing approved personal sender mapping is explicitly configured in the ignored parent .env. No financial records were reset, merged or deleted. The unsafe Cloud Run service remains untrusted, and its Meta subscription remains disabled. A corrected cloud deployment and real phone retest are still required before reconnecting Meta.
