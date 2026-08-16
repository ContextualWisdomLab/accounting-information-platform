# ADR 0002: Proposal and receipt boundary

**Status:** Accepted

## Decision

The Metering Billing Platform owns `accounting_journal_proposal`; the Accounting Information Platform consumes it and owns `accounting_posting_receipt`.

## Consequences

Both contracts are versioned and hash-addressed. Producer and consumer repositories run conformance fixtures. `posted` is impossible in the proposal status enumeration and authoritative only in the receipt.
