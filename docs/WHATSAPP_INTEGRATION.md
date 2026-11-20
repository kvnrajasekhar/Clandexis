# WhatsApp integration

The signed webhook checks the incoming business phone ID against META_PHONE_NUMBER_ID. It maps the sender's E.164 phone to exactly one existing merchant through that document's authorized_phones array. Unknown or ambiguously linked senders are ignored. Configure this binding through an authorized administrator. The webhook never grants access.

For an isolated emulator merchant, add the actual WhatsApp sender to authorized_phones, for example ["+919000000001"]. The dashboard merchant ID and sender phone can differ. Confirmation and cancellation pass the actual sender to the existing ledger for authorization and audit.

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
