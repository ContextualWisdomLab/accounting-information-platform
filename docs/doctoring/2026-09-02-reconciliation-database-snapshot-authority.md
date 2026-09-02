# Reconciliation lifecycle database-snapshot authority

Date: 2026-09-02

This note records the test-first repair for a lifecycle authority defect found while reviewing the reconciliation stack. The application already acquires a tenant/run session advisory lock before opening a fresh `REPEATABLE READ` authority transaction, which keeps run, review, exception, statement, and cash-journal reads on one coherent application snapshot. That did not independently protect the database transition table: a direct SQL caller could insert any syntactically valid `reconciliation_snapshot_hash`, and the migration would incorporate that caller value into the transition-command hash without reconstructing the book-to-bank evidence.

The RED regression is `tests/test_reconciliation_lifecycle_database_authority_postgres.py`. It proves two failure modes against real PostgreSQL: a caller-supplied SHA-256 value must not survive as the persisted lifecycle snapshot, and a direct transition attempt must fail when a source statement entry makes the database-owned bridge stop tying exactly. The predecessor migration accepts both conditions because its transition trigger validates review state but does not derive source-population authority.

Migration `0021_reconciliation_exception_resolution_outbox_pair.sql` now adds `accounting_core.reconciliation_run_database_snapshot_hash`. The function acquires the reconciliation-run lifecycle lock and, in one SQL statement snapshot, reads the run/opening-command scope, retained opening and closing balances, admitted statement entries, assigned cash-account journal lines, approved statement/journal allocations, reviewed matches/approvals, exceptions, and immutable exception-resolution commands. It verifies run-currency consistency, known allocation sources, exact source-capacity conservation, the statement opening-plus-movements equation, and the exact book-to-bank equation before producing a database-owned SHA-256 identity over the complete source/control payload.

`accounting_reconciliation_transition_authority_snapshot_guard` is a `BEFORE INSERT` trigger on `reconciliation_run_transition_command`. It replaces the incoming `reconciliation_snapshot_hash` with the database-derived digest. The trigger name deliberately sorts before the existing transition command-identity and command-hash triggers, so those later triggers consume the database-owned snapshot. PostgreSQL 18 documents that same-kind triggers fire in alphabetical order by trigger name; this ordering is therefore an explicit executable contract rather than an incidental implementation detail.

The server digest is intentionally not a second implementation of Python JSON serialization. It is an independent database evidence identity over the same or stronger underlying facts. The supported application path still uses the pre-lock session advisory lock followed by a fresh `REPEATABLE READ` transaction to avoid stale snapshots after lock waits. PostgreSQL's `REPEATABLE READ` semantics bind all statements in that transaction to the snapshot established by its first non-transaction-control statement, while the database trigger prevents the transition row itself from accepting caller-shaped snapshot authority.

This repair does not grant journal posting, reversal, period-close, chart-account selection, exception approval, or accounting-policy authority. It does not make Context Graph Contracts or Enterprise Architecture Core authoritative for financial facts and introduces no cross-service SQL. It also does not claim IFRS, SOC 2, CSAP, or ISO certification.

## Verification boundary

Merge evidence requires the new real-PostgreSQL RED/GREEN regression plus the unchanged exact-head Accounting Foundation suite, exact 100% owned production statement/branch coverage, repository/public-docstring contracts, SAST/security/dependency checks, reproducible package/SBOM/provenance, and current-head review. A queued, skipped, predecessor, or model-only result is not evidence.

## References

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18 documentation: SET TRANSACTION*. https://www.postgresql.org/docs/18/sql-set-transaction.html

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18 documentation: Overview of trigger behavior*. https://www.postgresql.org/docs/18/trigger-definition.html
