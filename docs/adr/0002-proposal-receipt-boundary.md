# ADR 0002: Proposal and receipt boundary

**Status:** Accepted

## Context

Source systems describe economic events. Accounting determines book treatment. Invoice issuance, payment capture, and provider payout do not by themselves determine revenue recognition, contract liability, or period of recognition (Financial Accounting Standards Board, 2024; IFRS Foundation, 2024).

A single shared status field would let a billing product claim `posted` before this boundary has resolved policy, period, and chart accounts. Authoritative outcomes must therefore travel on a separate, versioned receipt. When the service milestone publishes those outcomes, commit them through a transactional outbox and replay by event identity (Cloud Native Computing Foundation, 2022).

## Decision

The Metering Billing Platform owns `accounting_journal_proposal`; the Accounting Information Platform consumes it and owns `accounting_posting_receipt`.

## Consequences

Both contracts are versioned and hash-addressed. Producer and consumer repositories run conformance fixtures. `posted` is impossible in the proposal status enumeration and authoritative only in the receipt. No consumer receives a `posted` receipt unless the posting transaction commits.

## References

Cloud Native Computing Foundation. (2022). *CloudEvents specification, version 1.0.2*. https://github.com/cloudevents/spec

Financial Accounting Standards Board. (2024). *Accounting Standards Codification Topic 606: Revenue from contracts with customers*. https://asc.fasb.org/topic&trid=2121986

IFRS Foundation. (2024). *Post-implementation review of IFRS 15 revenue from contracts with customers: Project summary and feedback statement*. https://www.ifrs.org/projects/completed-projects/2024/pir-ifrs-15/
