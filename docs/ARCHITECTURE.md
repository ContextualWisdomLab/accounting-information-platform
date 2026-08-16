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

`accounting_information_platform.core` is the reference implementation for proposal validation, policy checks, posting, reversal, and trial balance. It has no network or database dependency. The PostgreSQL adapter must pass the same behavior fixtures before becoming authoritative.

## Deployment evolution

1. Reference core and contracts.
2. PostgreSQL proposal-intake and posting transaction.
3. Read-only API and operator hold queue.
4. Billing integration and source-to-posting reconciliation.
5. Revenue accounting and settlement accounting.
6. Cash, ISO 20022 adapters, close, multi-currency, reporting, and consolidation.

The modules may remain in one deployable service until throughput, data residency, or independent control ownership justifies separation. Service boundaries must not introduce direct cross-service SQL.
