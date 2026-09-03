# Architecture

## Authority topology

```text
Operational / commercial systems
        |
        | published evidence and accounting_journal_proposal
        v
Accounting Information Platform
  - legal entities and accounting books
  - chart accounts and effective account-role mappings
  - fiscal-period state
  - immutable balanced journals and reversals
  - authoritative posting receipts and transactional outbox
  - close, trial balance, statutory and management projections
        |
        +--> audit / reconciliation evidence
        +--> accounting_posting_receipt
```

Metering and billing remain authoritative for usage, pricing, invoice intent, payment, refund, dispute, and provider-settlement evidence. They can submit accounting proposals but cannot write accounting tables, choose final statutory chart accounts, or claim that a proposal has posted.

## Bounded modules

| Module | Responsibility |
|---|---|
| `proposal_intake` | Published proposal schema, source authority, idempotency and immutable payload identity |
| `policy_resolution` | Tenant, legal entity, book, period, currency and effective account-role mapping |
| `journal_posting` | Exact decimal validation, database-owned balance, immutable journal facts and posting receipt |
| `journal_reversal` | Equal-and-opposite correction, reversal lineage and command replay/conflict semantics |
| `close_control` | Open / soft-close / hard-close state and close evidence |
| `trial_balance` | Deterministic aggregation from the authoritative journal population or hard-close snapshot |
| `reporting_projection` | Versioned statements, ledgers, balances, rollforwards and close-package reads |
| `integration_outbox` | Transactional publication evidence and append-only audit history |
| `tax_interface` | VAT register and fail-closed HomeTax submission evidence; no NTS transport in this foundation |
| `bank_statement_registry` | Immutable camt.053.001.14 statement/entry evidence, bank-account-to-book mapping, and host artifact locators |

## Persistence and migration order

The PostgreSQL 18 foundation is installed in order:

1. `database/migrations/0001_accounting_foundation.sql` — normalized tenant, entity, book, period, proposal, journal, receipt, snapshot and outbox foundation.
2. `database/migrations/0002_chart_account_class.sql` — statement classification metadata.
3. `database/migrations/0003_home_tax_submission.sql` — tenant-scoped HomeTax command evidence and idempotency identity.
4. `database/migrations/0004_close_idempotency_key.sql` — deterministic close-command replay identity for hard-close snapshots.
5. `database/migrations/0005_closed_period_guard.sql` — database-owned closed-period authorization and deferred journal-balance enforcement.
6. `database/migrations/0006_concurrency_hot_partition.sql` — transaction lock safety limits and tenant-leading high-write indexes.
7. `database/migrations/0007_runtime_tenant_binding.sql` — database-controlled runtime login-to-tenant binding consumed by forced-RLS policy evaluation.

`0005_closed_period_guard.sql` makes `accounting_closing_writer` a `NOLOGIN` capability role. A soft-closed insert is admitted only when the session login is a member of that role **and** the transaction-local journal classification is `period_closing`, `adjusting`, or `reversal`. The GUC alone is not authority. Hard-closed periods reject every later journal insert.

Deferred constraint triggers recompute persisted journal lines at commit. A durable journal must have at least one line and exact debit and credit totals must match. Application validation is defense in depth, not the only balance control.

## Runtime identity boundary

The application runtime database login is separate from the migration owner and from administrative / break-glass identities. Tenant-scoped tables use RLS and the runtime path is tested with a non-owner, non-superuser, non-`BYPASSRLS` login. Purpose-limited soft-close exceptions use explicit role membership; ordinary runtime identities do not inherit `accounting_closing_writer`.

The HTTP surface currently binds tenant identity through the configured AIS tenant plus `X-CWL-Tenant-Reference`. That header is not a general credential. Production exposure therefore requires a trusted host or gateway that authenticates the caller before traffic reaches this process. Purpose-bound application authorization is tracked separately from the database-credential boundary and must not be inferred from request-body fields, model output, or database GUCs.

The mounted HTTP surface exposes `GET /healthz` as process liveness and
`GET /readyz` as database readiness. Readiness checks PostgreSQL connectivity,
an active bound runtime tenant, and the required checked-in migration/schema
contract through migration `0015`, including the enabled database-owned
DEFERRABLE balance triggers, their exact relation/function registrations, and
their unrestricted event definitions without `WHEN` predicates or `UPDATE OF`
column filters; it also requires forced RLS and exact public tenant-isolation
policies on every tenant-scoped fact table plus the canonical tenant-binding
function definition. Recorded canonical column metadata is an ordered prefix,
so compatible additive tables and columns do not remove an instance from
service while missing or altered required columns fail closed. It installs the
statement bound before the first connected readiness command, uses a single-host
connection and one total five-second readiness budget, applies the remaining
budget before each catalog statement, preserves stricter configured timeouts, and
does not accept the privileged fallback used by accounting commands. Neither probe is caller authentication
or release evidence, and readiness responses use `Cache-Control: no-store` to
prevent stale intermediary reuse.

## Posting transaction

A proposal follows one authoritative transaction boundary:

```text
validate published proposal
 -> bind tenant / entity / book / open period
 -> resolve semantic roles to chart accounts
 -> acquire tenant/command transaction lock and shared fiscal-period advisory lock
 -> persist immutable proposal evidence
 -> persist balanced journal header and lines
 -> persist authoritative posting receipt
 -> persist transactional outbox evidence
 -> COMMIT
```

Exact replay returns the original receipt. Reuse of an idempotency key with changed immutable evidence fails closed. Posted journal facts are append-only; corrections use reversal and, when required, a separately posted replacement.

## Reversal boundary

A reversal never updates or deletes the original journal. It posts an equal-and-opposite journal linked to the original and subject to period policy. The release contract requires replay to be bound to tenant, reversal command idempotency identity, original journal reference and immutable reversal-command evidence hash. Any changed command under the same identity must fail closed. PR #2 remains non-release-ready until exact-current-head tests and PostgreSQL persistence prove that contract together.

A reversal accounting date may not precede the original accounting date. Soft-closed periods may admit an authorized reversal through the purpose-limited closing-writer capability. Hard-closed periods reject a new reversal into the locked period.

## Close boundary

Soft-close changes the fiscal-period state but writes no hard-close snapshot. Ordinary posting is blocked; purpose-limited adjusting / closing / reversal paths may remain available under database authorization.

Hard-close loads one repeatable-read close package, posts the AIS-owned period-closing journal when required, stores the hard-close trial-balance snapshot and locks the period. Later ordinary or reversal inserts into that period are rejected. Close replay is idempotent and does not create a second snapshot or closing journal.

## Read models

The current HTTP / library surface includes:

- proposal accept, posting-receipt lookup and Billing proposal pull;
- fiscal-period open/read/list and soft/hard close;
- journal accept/read/list and reversal read/write;
- outbox and audit-event reads / publish acknowledgement;
- legal-entity, accounting-book, chart-account and account-role-mapping reads;
- trial balance, account ledger, account balances and account rollforwards;
- receivable aging, payable aging and unapplied-cash rollforward;
- income statement, balance sheet, changes in equity, cash-flow and statement packages;
- period-close package and VAT period register;
- fail-closed HomeTax submission evidence and receipt history.
- `/healthz` liveness and `/readyz` database-readiness probes.

Detailed request / response and behavioral contracts live in the corresponding ADRs. Read models do not become alternate posting authorities.

## Billing integration

Billing pull destinations are operator-configured. `BILLING_BASE_URL` plus optional `BILLING_ALLOWED_ORIGINS` define the allowed origins. Origins are normalized, malformed ports / IPv6 fail as accounting validation errors, and loopback, link-local and `localhost` are rejected even when listed in the allowlist. A request body cannot authorize a new destination.

Billing pagination is page-progressive rather than one distributed transaction. An initial fetch failure writes nothing. If a later remote page fails, prior AIS postings remain committed; retry relies on proposal idempotency to replay those receipts without duplicate journals. Repeated cursors and pulls beyond the bounded page count fail closed.

## Evidence and release boundary

No workflow, model output, stale predecessor check or synthetic merge-ref result is accounting evidence. Release evidence must come from one unchanged protected source head with applicable PostgreSQL integration, 100% owned production statement and branch coverage, public API docstrings, repository contracts, security scans, package / SBOM / provenance checks and qualifying independent review all passing together.

## Database tenant trust boundary

The HTTP/authentication adapter supplies a tenant reference, but PostgreSQL independently binds each ordinary runtime login to one tenant using `runtime_tenant_binding` and `session_user` (ADR 0049). Forced-RLS policies consume only that database-controlled identity. Request fields, model output, Billing proposals, and custom session GUCs cannot select another accounting tenant.

## Book-scoped close authority

Shared fiscal-calendar dates do not collapse independent accounting books into one close state. PostgreSQL `accounting_book_period_control` is checked by the journal insert guard and by application admission; statutory and management books can therefore close independently while immutable snapshots remain book scoped.

8. `database/migrations/0008_fiscal_period_open_command.sql` — durable fiscal-period-open command identity and source evidence.
9. `database/migrations/0009_accounting_book_period_control.sql` — accounting-book-scoped close authority and journal guard lookup.
10. `database/migrations/0010_soft_close_command_evidence.sql` — immutable exact soft-close command identity and source count/hash.
11. `database/migrations/0011_bank_statement_evidence.sql` — immutable camt.053.001.14 statement evidence, bank-account assignment, and entry provenance.
12. `database/migrations/0012_bank_assignment_command_identity.sql` — tenant-scoped bank-account-assignment command identity, replay/conflict evidence, and the active book-scope uniqueness guard.
13. `database/migrations/0013_reconciliation_run_exception_evidence.sql` — durable reconciliation-run and exception evidence required by the installed bank-reconciliation control chain.
14. `database/migrations/0014_reconciliation_candidate_allocation.sql` — durable reconciliation candidate, single-approved match, and exact statement/journal allocation rows with forced tenant RLS.
15. `database/migrations/0015_reconciliation_policy_repair.sql` — idempotent forward repair that installs the four reconciliation tenant policies on databases already running migration `0014`.

## Durable soft-close command evidence

`accounting_core.accounting_book_period_control` owns book-period state. Migration `0010_soft_close_command_evidence.sql` augments soft-close rows with immutable tenant-scoped command identity plus source count/hash observed when the transition committed. Soft-close event and evidence share the accounting transaction. Replay reads stored evidence; hard-close separately owns the immutable trial-balance snapshot.
