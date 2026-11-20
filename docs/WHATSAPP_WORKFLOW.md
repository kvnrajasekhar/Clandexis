# WhatsApp conversation flow

This guide describes the approved-phone MVP implemented in the current checkout. Live-phone verification of the corrected cloud revision is pending. See [integration setup](WHATSAPP_INTEGRATION.md) and [the incident record](WHATSAPP_P0_INCIDENT.md) for release context.

## Merchant experience

1. Send a text message or supported audio/image/PDF attachment from an approved phone.
2. The application extracts and validates a candidate, then persists it under that merchant.
3. Missing or ambiguous facts produce a specific follow-up. Reply in the same conversation.
4. Each clarification reviews the same candidate and preserves known facts. Telugu, Hindi, English, and code-switched text retain a language/script preference.
5. A complete valid candidate displays buyer, items, quantities, prices, paid, credit, total, and its ID, with **Confirm** and **Cancel**.
6. Confirm commits once. Cancel closes the candidate without posting.
7. A committed sale can produce a basic PDF invoice. A repayment updates the party receivable while preserving sales and stock.

## Clarification example

Illustrative Romanized Telugu exchange:

> Merchant — Nenu Raju ki 10 shirts ammnu.
>
> Follow-up — Okkokka item price entha?
>
> Merchant — 200 each.
>
> Follow-up — Entha payment vachindi? Cash, UPI, leka udhaar aa?
>
> Merchant — 500 UPI, 1500 udhaar.

The valid summary should preserve Raju and 10 shirts, total ₹2,000, UPI ₹500, and credit ₹1,500. Only the candidate's Confirm button can post it.

For `आज मैंने राजू को 10 शर्ट बेचीं।`, the missing-price question uses Hindi Devanagari. The model and validator still determine the actual candidate; always inspect the displayed facts.

## State and action rules

| Situation | Application behavior |
| --- | --- |
| Unknown sender or wrong business phone | Ignore before merchant processing |
| Missing price or payment information | Ask for missing facts; withhold actionable Confirm buttons |
| Partial answer | Revalidate and persist another review of the same candidate |
| Message recognized as a different transaction while one is pending | Ask to continue or cancel the pending candidate |
| `Continue` | Repeat the current question or direct the user to the candidate's existing action |
| Candidate-specific Cancel | Close the pending candidate and clear conversation context |
| Bare `YES` / `NO` | Ask for the specific candidate's buttons |
| Malformed button or foreign candidate | Reject without extraction or posting |
| Expired candidate | Reject confirmation and request fresh details |
| Repeated Confirm | Return persisted results with one posting effect |
| Provider failure | Release the processing lease so the persisted event can retry |
| Concurrent message while busy | Persist `DEFERRED` and ask the sender to retry |
| `PDF ledgers kavali` | Direct to authenticated dashboard reports/downloads; create no transaction |
| Query or unknown model intent | Return a read-only/unsupported response; skip staging |

The current ledger posts sales, repayments, and stock additions. Other extracted financial intents remain held for review.

## Stored context and audit

The conversation uses its persisted `active_candidate_id`, revision, and expiry. It loads the relevant candidate instead of selecting a transaction from unrelated history. Original extraction and review history remain available for audit. Successful confirmation/cancellation clears active context.

Every processed event carries its Meta message ID, normalized sender, resolved merchant, and receive timestamp. Event replies are cached persistently. The ledger independently enforces merchant scope and duplicate protection.

## Completion evidence

A successful test must connect the incoming message ID, merchant, candidate ID, confirmation event, and committed transaction. Verify stock, party receivable, payments, and dashboard totals in the selected database. Invoice facts must match the committed sale.

The [verification guide](TESTING.md) records the local PostgreSQL results and the remaining real-phone/cloud checks.
