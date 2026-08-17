# Architecture

## Authority topology

```text
Operational systems         Metering Billing Platform
        |                              |
        +-------- economic facts ------+
                                       |
                            accounting_journal_proposal
                                       |
                                       v
                    Accounting Information Platform
                    - proposal intake and evidence
                    - policy and mapping resolution
                    - period and currency controls
                    - immutable journals and reversals
                    - trial balance and close
                    - financial reporting projections
                                       |
                          accounting_posting_receipt
                                       |
                  source reconciliation and audit evidence
```

## Bounded modules

| Module | Responsibility |
|---|---|
| `proposal_intake` | schema, source authority, idempotency, payload identity |
| `policy_resolution` | entity, book, period, currencies, account-role mapping |
| `journal_posting` | exact balance, immutable journal and lines, source lineage |
| `journal_reversal` | equal-and-opposite correction and replacement lineage |
| `trial_balance` | deterministic journal population aggregation |
| `close_control` | period states, hold queues, close and reopen governance |
| `reporting_projection` | versioned trial-balance and financial-statement views |
| `integration_outbox` | authoritative receipt and event publication after commit |

## Current implementation

`accounting_information_platform.core` is the reference implementation for proposal validation, policy checks, posting, reversal, and trial balance. It has no network or database dependency. `ingest_journal_proposal` reads the Billing-owned JSON contract field `proposal_status` and accepts only `validated` or `exported` before constructing a status-free `JournalProposal`. `PostgresPostingLedger.post_proposal` resolves `AccountingPolicy` from the foundation catalog (`account_role_mapping`, book by intended role, open fiscal period) in the same transaction as the post. `PostgresPostingLedger` also applies those invariants to PostgreSQL 18 through `database/migrations/0001_accounting_foundation.sql`: one transaction writes the proposal, journal, lines, receipt, and outbox event; replay returns the original receipt; reversal is append-only; `close_fiscal_period` writes a trial-balance snapshot and the period status in one commit; a non-open fiscal period writes zero ordinary-posting rows. The in-memory `PostingLedger.post(proposal, policy)` path remains the reference oracle.

## Deployment evolution

1. Reference core and contracts.
2. PostgreSQL proposal-intake and posting transaction.
3. Read-only API and operator hold queue.
4. Billing integration and source-to-posting reconciliation.
5. Revenue accounting and settlement accounting.
6. Cash, ISO 20022 adapters, multi-currency, reporting, and consolidation.

The modules may remain in one deployable service until throughput, data residency, or independent control ownership justifies separation. Service boundaries must not introduce direct cross-service SQL.
