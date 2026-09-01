# Accounting Ubiquitous Language

Status: accepted architecture vocabulary. Definitions describe this product's model and evidence boundaries; they are not a substitute for accounting, tax or legal advice.

Use these terms consistently in source, tests, ADRs, APIs and operator documentation. When an external provider uses a conflicting term, preserve the provider term inside its Anti-Corruption Layer and translate it at the boundary.

## Authority and scope

**Tenant account** — The isolation boundary under which accounting objects, references and database access are scoped. A request field is not database tenant authority.

**Legal entity** — The accounting subject whose books and reporting obligations are represented. A customer, workspace or Billing account is not automatically a legal entity.

**Accounting book** — A tenant/legal-entity-scoped ledger view with its own period control and accounting purpose. Statutory and management books may share dates without sharing close state.

**Accounting policy** — A versioned, effective-dated rule used by Accounting to determine treatment such as account-role mapping. A model suggestion or source-system field is not accounting policy.

**Semantic account role** — The business meaning submitted by a source system, such as a receivable or revenue role. It is resolved by Accounting; source systems do not choose final chart-account identifiers.

**Chart account** — An Accounting-owned ledger account valid for a defined scope and effective interval.

## Proposal and posting

**Journal proposal** — Immutable source evidence requesting accounting treatment. It is an input to Accounting and is not a journal, posting or statutory fact.

**Source payload hash** — The exact cryptographic identity of immutable command/source evidence. The same idempotency key with a different source payload hash is conflicting reuse, not replay.

**Idempotency key** — A command identity under which an exact replay returns the original durable result. It never authorizes changed evidence under the same key.

**General journal** — The authoritative persisted journal header owned by Accounting. Once posted, it is append-only.

**Journal line** — A debit or credit line belonging to one general journal and one tenant/book scope. Persisted journal lines collectively satisfy the database-owned balance invariant.

**Balanced journal** — A persisted journal whose exact debit total equals its exact credit total and that contains the required durable lines. Application checks are defense in depth; PostgreSQL owns the commit-time invariant.

**Posting** — The atomic Accounting transaction that validates policy/period scope, persists immutable journal facts, creates the posting receipt and writes transactional outbox evidence.

**Posting receipt** — Accounting's authoritative evidence that a proposal was posted. An upstream invoice/payment status is not a posting receipt.

**Replay** — Re-submission of the same command identity with the same immutable evidence, yielding the original durable result without duplicate facts.

**Conflicting reuse** — Re-submission of an existing command identity with changed immutable evidence. It fails closed.

## Correction and close

**Reversal** — A new equal-and-opposite journal linked to an original posted journal. A reversal never edits or deletes the original fact.

**Reposting** — A separately authorized posting used after reversal when corrected accounting treatment is required.

**Fiscal period** — An accounting date interval controlled at accounting-book scope.

**Soft close** — A period-control state that blocks ordinary posting while allowing only explicitly authorized closing/adjusting/reversal paths defined by policy and database capability.

**Hard close** — A period-control state that preserves immutable close evidence/snapshot and rejects subsequent journal insertion into the locked period under the implemented contract.

**Reopen** — An explicitly authorized period-state transition with durable command and audit evidence. It is not a side effect of a journal request.

**Trial balance** — Deterministic debit/credit aggregation from authoritative posted facts, or an immutable hard-close snapshot where the contract specifies one. It is a projection, not a second ledger.

**Reporting projection** — A read-only derivation such as a ledger, balance, rollforward or financial statement. A projection cannot post, reverse or change policy.

## Bank evidence and reconciliation

**Bank statement artifact** — Retained immutable source evidence for a bank statement, including its source hash and artifact locator. It is external evidence, not an accounting journal.

**Bank statement record** — Canonical Accounting-owned statement metadata translated through the bank/provider Anti-Corruption Layer.

**Statement entry** — One canonical immutable bank movement from a statement. A statement entry must never automatically become a journal line.

**Statement balance** — Exact source balance evidence with explicit credit/debit direction and effective time. It is distinct from a calculated book balance.

**Bank account assignment** — Effective Accounting-owned mapping from an external bank account identity to legal-entity/book/cash-account scope.

**Knowledge cutoff** — The system-time boundary after which evidence is excluded from a reconciliation run. It answers what was known by the run, not when the underlying business event economically occurred.

**Book cutoff** — The accounting-date boundary used to select posted book facts for a reconciliation run.

**Reconciliation run** — Immutable tenant/entity/book/bank/currency and cutoff scope under which reconciliation evidence is evaluated. A run is not an approval and does not post.

**Reconciliation candidate** — A deterministic proposed relationship between bank evidence and book evidence. It remains a proposal until explicitly reviewed under the implemented decision contract.

**Allocation** — An exact Decimal amount consuming part of a statement or journal source within a reconciliation match. Allocation conservation prevents double consumption across matches.

**Reviewed match** — A candidate plus the complete structured statement/journal allocation evidence reviewed for a decision.

**Approval evidence** — Immutable maker/checker decision evidence bound to the reviewed match snapshot and source payload. An approved reconciliation match is not permission to post an accounting adjustment.

**Reconciliation exception** — Explicit unresolved or resolved evidence describing why a source population cannot yet satisfy deterministic reconciliation/close-review criteria.

**Active approved population** — The complete database-current set of reconciliation matches whose current status is approved for the run. A caller-selected subset is not authoritative.

**Statement population** — The complete immutable statement-entry population included by the authoritative run scope/cutoff and identified by a deterministic digest.

**Book population** — The complete authoritative posted cash-book population included by book and knowledge cutoffs and identified by a deterministic digest.

**Book-to-bank bridge** — Exact Decimal reconciliation equation connecting bank closing evidence, book balance and explicitly identified outstanding items/differences. Caller-provided totals do not become authoritative merely by appearing in a close package.

**Reconciled run** — A run that has satisfied the repository's supported durable transition contract and database-authoritative reconciliation criteria. Direct SQL status editing is not a supported lifecycle command.

**Close-review package** — Tamper-evident read-only evidence assembled from authoritative reconciliation state for a separately authorized period-close review. It cannot approve a match, close a period, post/reverse a journal or alter accounting policy.

## Integration and evidence

**Published Language** — A versioned schema/API/event contract used between bounded contexts or repositories. Internal ORM rows and application tables are not a Published Language.

**Anti-Corruption Layer (ACL)** — A boundary that translates external Billing, bank, tax or ERP concepts into Accounting-owned contracts without importing the external model into the accounting domain.

**Transactional outbox evidence** — Publication evidence persisted atomically with the accounting fact that produced it. A later synthetic event cannot repair a failed accounting transaction's atomicity.

**Domain event** — A durable statement that an Accounting-owned business fact occurred. It is emitted from the owning transaction and is distinct from a transport delivery acknowledgement.

**Evidence reference** — Stable identity plus cryptographic digest, and when applicable cutoff/provenance, for immutable material used by an accounting decision or projection.

**Effective time** — When a business/accounting fact or policy is valid in the modeled world (`valid_from`/`valid_to`, accounting date, period interval).

**System time** — When the system recorded or observed evidence (`recorded_at`, `posted_at`, decision timestamps). System time must not silently replace effective time.

## Terms that must not be conflated

- `invoice`, `payment`, `refund` and `provider settlement` are commercial/operational evidence owned upstream; they are not journals or posting receipts.
- `journal proposal` is not `general journal`.
- `approved reconciliation` is not `approved journal posting` and does not authorize an adjustment.
- `statement entry` is not `journal line`.
- `bank closing balance` is not `book cash balance` until the exact bridge proves their relationship.
- `soft close` is not `hard close`.
- `reversal` is not mutation of the original journal.
- `tenant reference` in HTTP is not database tenant authority.
- `model suggestion` is not accounting policy, approval or statutory truth.
- `successful workflow` is not release evidence unless it ran against the exact unchanged protected source head and satisfies the applicable gate contract.

## Naming rule

New domain types, commands, routes, events, migrations and tests should use the terms above rather than synonyms that obscure authority. Provider-specific vocabulary remains inside its ACL. If a new concept cannot be defined without mixing owners or lifecycle responsibilities, update the Context Map/ADR before adding the implementation.
