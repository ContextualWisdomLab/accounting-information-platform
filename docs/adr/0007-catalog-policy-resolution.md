# ADR 0007: Durable catalog policy resolution

**Status:** Accepted

## Decision

Ordinary posting of a Billing `JournalProposal` resolves `AccountingPolicy` from AIS catalog rows in the same PostgreSQL transaction: tenant, legal entity, book by `intended_book_role_code`, the open fiscal period covering `accounting_date`, and effective `account_role_mapping` rows. Policy and posting-rule versions come from those mapping rows. The adapter does not invent chart-account codes. More than one effective catalog row for the same `account_role_code` already fails closed. A published policy manifest is subject to the same uniqueness before in-memory load (ADR 0005). Caller-supplied `post(proposal, policy)` remains the in-memory reference path.

The in-memory Accounting Policy boundary owns the runtime domain of its fiscal-period interval. `open_period_start` and `open_period_end` must be exact built-in `datetime.date` values before interval ordering is evaluated. ISO-looking strings, `datetime.datetime`, and date subclasses are not accepted as policy facts; caller-defined comparison behavior must not execute while deciding whether a policy interval is valid. The manifest loader still parses ISO date text into built-in dates before constructing the policy.

Runtime evidence is `727fade2688ec572d1addbbba4fceefa564b2510`: tenant/entity/book role, currencies, account mapping, policy/rule versions and calendar values remain fixed while only endpoint runtime type varies. Causal repair `2e5e2d2bcb780b2bed73a7711e4614c3191696b6` reuses the exact-calendar-date admission introduced by the proposal boundary and applies it to both policy interval endpoints before `open_period_start > open_period_end`.

## Consequences

Controllers can ingest a status-free Billing proposal and call `post_proposal` without constructing a mapping. Missing catalog, mapping, or open-period facts fail closed and name the next operator action. Historical journals still store the resolved policy and rule versions used at posting time.

The exact built-in-date rule is a repository runtime invariant, not an assertion that IFRS or IASB prescribes Python types. Its purpose is to keep fiscal-period policy evidence deterministic and prevent Python coercion, lexicographic string ordering, or caller-defined date-subclass comparison behavior from becoming accounting-policy authority.
