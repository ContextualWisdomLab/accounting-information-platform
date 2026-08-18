# ADR 0007: Durable catalog policy resolution

**Status:** Accepted

## Decision

Ordinary posting of a Billing `JournalProposal` resolves `AccountingPolicy` from AIS catalog rows in the same PostgreSQL transaction: tenant, legal entity, book by `intended_book_role_code`, the open fiscal period covering `accounting_date`, and effective `account_role_mapping` rows. Policy and posting-rule versions come from those mapping rows. The adapter does not invent chart-account codes. Caller-supplied `post(proposal, policy)` remains the in-memory reference path.

## Consequences

Controllers can ingest a status-free Billing proposal and call `post_proposal` without constructing a mapping. Missing catalog, mapping, or open-period facts fail closed and name the next operator action. Historical journals still store the resolved policy and rule versions used at posting time.
