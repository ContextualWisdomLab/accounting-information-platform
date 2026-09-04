# Trial-balance conservation traceability

## Control statement

A retained hard-close trial-balance line is one accounting fact. Its three stored monetary fields are not independent caller assertions: `net_balance_amount` must equal `debit_total_amount - credit_total_amount` exactly in PostgreSQL `numeric(38, 6)`.

The database previously constrained debit and credit to non-negative fixed-scale values but did not relate either amount to `net_balance_amount`. The supported close implementation calculates the net from debit and credit before persistence, yet a direct or legacy writer could persist a different net while still satisfying every row constraint. Because the retained population is later consumed as Period Close and reporting evidence, the invariant belongs at the authoritative PostgreSQL boundary rather than only in the application calculation.

## Standard and platform basis

The IFRS Conceptual Framework describes useful financial information as faithfully representing the economic phenomenon it purports to represent and identifies completeness, neutrality, and freedom from error as characteristics of a faithful representation. This does **not** prescribe a SQL formula or a trial-balance table design. AIS uses it only as the financial-reporting quality rationale for refusing internally contradictory retained monetary evidence.

PostgreSQL 18 `CHECK` constraints are the direct enforcement mechanism because the relation is immutable and row-local. PostgreSQL documents that a `CHECK` added `NOT VALID` is enforced for subsequent inserts and updates while skipping the initial table scan, and that `VALIDATE CONSTRAINT` later scans inherited rows using a less restrictive validation lock. Migration 0030 therefore adds the exact row-local check as `NOT VALID` and validates it before the migration transaction commits. An inherited inconsistency blocks the upgrade; the migration does not rewrite historical amounts.

## Executable trace

| Layer | Exact control |
|---|---|
| Authoritative table | `accounting_reporting.trial_balance_line` |
| Monetary invariant | `net_balance_amount = debit_total_amount - credit_total_amount` |
| Constraint | `trial_balance_line_net_balance_conservation` |
| Migration | `database/migrations/0030_trial_balance_snapshot_immutability.sql` |
| Real PostgreSQL regression | `tests/test_postgres_trial_balance_snapshot_scope_red.py::TrialBalanceSnapshotScopePostgresTests::test_snapshot_line_rejects_nonconserving_net_balance` |
| RED | `03eb7112ff7a6ce67b6fd4d6b0c99f00d3d93aae` |
| Minimal implementation | `06d365b13510735c792a8625b4f6d9011d1f6525` |
| Decision record | `docs/adr/0006-fiscal-period-close-snapshot.md` |
| Owning PR | `#53` |

The regression uses a valid tenant, legal entity, accounting book, fiscal period, snapshot, and same-book chart account, then attempts to retain debit `10.250000`, credit `3.125000`, and net `999.000000`. The expected PostgreSQL failure names `trial_balance_line_net_balance_conservation`. The case isolates arithmetic conservation from tenant, book-scope, close-authority, and immutability failures.

## Authority and non-claims

This control does not make a report IFRS-compliant, audited, assured, approved, or filing-ready. It does not decide accounting policy, select chart accounts, post or reverse journals, close a period, approve reconciliation, or write Billing-owned commercial truth. Reporting and export projections may consume the retained values only after this accounting-owned invariant has been satisfied; they must not create a second monetary authority by overriding the retained debit, credit, or net amounts.

A later reopen or correction policy must preserve the old retained population through explicit successor lineage. It must not repair a historical inconsistency by silently editing an immutable hard-close row.

## References

IFRS Foundation. (2022). *Conceptual framework for financial reporting*. https://www.ifrs.org/content/dam/ifrs/publications/pdf-standards/english/2022/issued/part-a/conceptual-framework-for-financial-reporting.pdf

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18 documentation: Constraints*. https://www.postgresql.org/docs/18/ddl-constraints.html

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18 documentation: ALTER TABLE*. https://www.postgresql.org/docs/18/sql-altertable.html
