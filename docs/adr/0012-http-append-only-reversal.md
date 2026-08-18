# ADR 0012: HTTP append-only journal reversal

**Status:** Accepted

## Decision

AIS exposes `accept_journal_reversal` and `POST /journal-reversals` on the same stdlib HTTP surface as proposal accept. The command identifies the original journal by `journal_reference` and/or the Billing `idempotency_key` that produced the original receipt, plus `reversal_date` and `reversal_reason_code`. There is no `reversed_by_actor_reference` persistence field. The handler calls existing `PostgresPostingLedger.reverse` after resolving catalog policy from the original journal. The response is the published `accounting_posting_receipt` for the reversing journal. That receipt uses `posting_status_code=posted` (the existing core contract for the reversing journal). The original journal and its receipt stay `posted`. Replay of the same reversal returns the same reversing receipt and writes no second journal. `GET /journal-reversals` is 405. `GET /posting-receipts?idempotency_key=` still returns the original receipt; `reversal:{journal_reference}` looks up the reversing receipt.

## Consequences

Controllers can reverse a posted journal without an in-process Python import. Cross-tenant reverse is rejected before a write. A closed period, unknown journal, or unknown Billing key fails closed and does not invent a reversal. If `{journal_reference}:reversal` is already a posted journal that is not this original's reversing journal, reverse fails closed (`posted journal is immutable`) and writes no second journal. Replay of the same reversal request still returns the original reversing receipt.
