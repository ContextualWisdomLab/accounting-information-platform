# ADR 0002: Proposal and receipt boundary

**Status:** Accepted

## Context

Source systems describe economic events. Accounting determines book treatment.
Invoice issuance, payment capture, and provider payout do not by themselves
determine revenue recognition, contract liability, or period of recognition
(Financial Accounting Standards Board, 2024; IFRS Foundation, 2024).

A shared status field could let a billing product claim `posted` before this
boundary resolves policy, period, and chart accounts. Authoritative outcomes
therefore travel on a separate, versioned receipt and are committed through the
transactional outbox when a service milestone publishes them (Cloud Native
Computing Foundation, 2022).

## Decision

The Metering Billing Platform owns `accounting_journal_proposal`; the Accounting Information Platform consumes it and owns `accounting_posting_receipt`.

## Consequences

Both contracts are versioned and hash-addressed. Producer and consumer repositories run conformance fixtures. The published proposal field is `proposal_status` with `draft`, `validated`, `exported`, or `rejected`. `posted` is impossible on that contract and authoritative only in the receipt. AIS ingest accepts `validated` and `exported`; it does not ingest Billing operational reject rows. After AIS emits `posting_receipt`, Billing does not flip the proposal to `posted`.

The published proposal line field `account_role_code` may name a commercial semantic role. It must not be `retained_earnings`. That role is AIS period-close only (ADR 0024); the proposal schema forbids it, and AIS ingest fail-closes before accept. Billing may not select chart account `310100` or claim the close journal.

## References

Cloud Native Computing Foundation. (2022). *CloudEvents specification, version 1.0.2*. https://github.com/cloudevents/spec

Financial Accounting Standards Board. (2024). *Accounting Standards Codification Topic 606: Revenue from contracts with customers*. https://asc.fasb.org/topic&trid=2121986

IFRS Foundation. (2024). *Post-implementation review of IFRS 15 revenue from contracts with customers: Project summary and feedback statement*. https://www.ifrs.org/projects/completed-projects/2024/pir-ifrs-15/
