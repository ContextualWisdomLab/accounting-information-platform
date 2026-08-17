# ADR 0002: Proposal and receipt boundary

**Status:** Accepted

## Decision

The Metering Billing Platform owns `accounting_journal_proposal`; the Accounting Information Platform consumes it and owns `accounting_posting_receipt`.

## Consequences

Both contracts are versioned and hash-addressed. Producer and consumer repositories run conformance fixtures. The published proposal field is `proposal_status` with `draft`, `validated`, `exported`, or `rejected`. `posted` is impossible on that contract and authoritative only in the receipt. AIS ingest accepts `validated` and `exported`; it does not ingest Billing operational reject rows. After AIS emits `posting_receipt`, Billing does not flip the proposal to `posted`.
