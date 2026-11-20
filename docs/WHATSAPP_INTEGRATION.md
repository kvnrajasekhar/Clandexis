# WhatsApp integration

The adapter and application flow are implemented. The Meta subscription remains disabled after the old callback was found to target a different Cloud Run revision. Restore it only after the unsafe route is contained and the corrected deployment passes the release checks in [the deployment guide](DASHBOARD_DEPLOYMENT.md).

## Identity configuration

One approved sender phone maps to one independent merchant account. The WhatsApp business/test number identifies the transport endpoint and stays separate from merchant identities.

Configure an explicit JSON allowlist in the environment. For PostgreSQL, values are existing merchant UUIDs:

```dotenv
WHATSAPP_DEMO_PHONE_MAP='{"+918001111111":"11111111-1111-1111-1111-111111111111","+919002222222":"22222222-2222-2222-2222-222222222222"}'
```

The numbers above are synthetic examples. The adapter normalizes senders to E.164 and rejects unknown senders, invalid maps, duplicate normalized numbers, and maps assigning multiple phones to the same merchant. It checks the incoming business phone-number ID against `META_PHONE_NUMBER_ID`.

In PostgreSQL, the merchant's `phone_e164` must match and `is_approved` must be true. The Firestore backend retains its merchant-level `authorized_phones` check in addition to the explicit map. Mapping never provisions an account or authorizes a shared fallback merchant.

## Credentials

Use the existing ignored `.env` or process/deployment secrets. The tracked `.env.example` supplies configuration names.

| Primary setting | Accepted alias | Purpose |
| --- | --- | --- |
| `META_VERIFY_TOKEN` | `WHATSAPP_VERIFY_TOKEN` | GET challenge verification |
| `META_WHATSAPP_TOKEN` | `WHATSAPP_ACCESS_TOKEN` | Media retrieval and outbound calls |
| `META_PHONE_NUMBER_ID` | `WHATSAPP_PHONE_NUMBER_ID` | Meta business/test phone resource ID |
| `META_APP_SECRET` | `WHATSAPP_APP_SECRET`, `META-WHATSAPP_APP_SECRET` | POST webhook HMAC |
| `META_API_VERSION` | — | Configured Graph API version |

A primary variable takes precedence, including an explicitly empty value. The application ID is used for administrative subscription inspection; the existing send/HMAC path uses the settings above. Both local demo launchers deliberately disable Meta settings.

Keep certificate verification enabled. The previous Sophos inspection failure was resolved by changing to a trusted network. A runtime behind inspection needs its approved CA configuration.

## Inbound processing

1. Verify `X-Hub-Signature-256` against the raw POST body.
2. Check the business phone resource and resolve the approved sender.
3. Normalize the message ID, sender, merchant, timestamp, text/media, and interactive reply.
4. Retrieve attachment bytes before calling the existing Gemini pipeline. Downloads are limited to 16 MiB, and the MIME type reaches the interpreter.
5. Acquire the merchant conversation lease and process the persisted event. Each saved source payload contains the current sender's message from a batch.
6. Stage or clarify a candidate, or route its explicit Confirm/Cancel action to the ledger.
7. After commit, generate a sale invoice and dispatch the response through the existing Meta client.

Failed/empty media retrieval asks for resubmission. Query/report requests receive a read-only response. No network, rendering, or storage calls execute inside a ledger transaction callback.

## Conversation storage

| Data | PostgreSQL | Firestore |
| --- | --- | --- |
| Active candidate, question, missing fields, language/script, revision, expiry, lease | `conversations` | `merchants/{id}/conversations/whatsapp` |
| Received events and cached replies | `workflow_messages` | Conversation `messages` subcollection |
| Candidate facts and original extraction | `transaction_candidates` | Merchant `transaction_candidates` |
| Review history | `candidate_reviews` | Candidate `review_history` |

Cached replies survive restart. Busy events are recorded as `DEFERRED` and ask the sender to retry. A lease expires after five minutes if a process dies; subsequent retry can resume. There is no automatic replay worker.

The [conversation guide](WHATSAPP_WORKFLOW.md) describes the merchant flow. [Tests](TESTING.md) cover independent phones, batched-payload isolation, restart recovery, retries, and confirmation safety.

## Outbound and documents

`POST /api/v1/send` requires the separate internal bearer token. Use it only from trusted services.

Meta test recipients must be registered in the app's allowed-recipient list. A Graph API acceptance response establishes request acceptance; delivery and inbound processing need separate evidence from the real conversation and webhook events.

The local filesystem invoice store supports authenticated dashboard downloads. WhatsApp document delivery needs the existing GCS store, artifact association, and an accessible signed URL. The current GCS URL resolver requests a 15-minute V4 URL. Cloud credentials must be able to sign that URL. [Invoice contracts](CONFIRMATION_PDF_INTERFACE.md) describe retry behavior.
