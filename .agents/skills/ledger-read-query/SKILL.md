---
name: ledger-read-query
description: Read-only conversational analytics over merchant inventory, udhaar ledger, and sales history without state mutations.
---

# Ledger Read Query Skill

## Purpose
Enables MSME owners to query their business status via vernacular WhatsApp voice or text:
- "Kitna stock bacha hai?" (How much stock is left?)
- "Ramesh ka kitna udhaar baaki hai?" (How much udhaar does Ramesh owe?)
- "Aaj kitni bikri hui?" (What were today's total sales?)

## Security Boundary
- Strictly read-only queries against Firestore replicas or direct document lookups.
- Zero state mutation privileges.
- Tenant isolation enforced strictly by `merchant_id`.
