# Reconciliation transition snapshot authority repair — 2026-09-02

## Problem statement

The reconciliation lifecycle service reconstructed statement and book populations from PostgreSQL and hashed the exact bridge in application code, but migration `0019_reconciliation_run_command_evidence.sql` accepted any caller-supplied `reconciliation_snapshot_hash` and population references that matched the expected digest shape. The transition-command trigger then bound those caller values into its own command hash. A privileged/direct SQL writer could therefore manufacture a syntactically valid digest without proving the same source population and bridge that the supported service path had observed.

That is an authority defect, not a formatting defect. A reconciliation run is close evidence only when the database can independently prove that the immutable statement population, posted cash-book population, reviewed allocations/decisions, and exception state form one exact bridge with no unexplained difference.

## Test-first trace

Commit `919d868cb36de51534cd1f6b254ebfb0e78dadf5` added the RED repository contract `tests/test_reconciliation_transition_database_snapshot_authority.py`. It requires a checked-in database-authority migration, requires the public/canonical install boundary to apply it, and requires the transition insert path to replace all three caller-owned authority values: reconciliation snapshot digest, statement-population reference, and book-population reference.

Commit `e30f39b9fba977992c747dc83f4633f7170a79c0` added the PostgreSQL authority overlay. Commit `88f4a43fd69183df947166e48f391bad394d3195` wired every supported foundation install, including the historical `persistence.apply_foundation_migration` import path, through that overlay.

## Database authority contract

`accounting_core.reconciliation_run_database_snapshot_authority(...)` now reconstructs a same-or-stronger authority snapshot from database-owned facts. It binds the run/opening-command scope, opening and closing bank balances, the complete immutable statement-entry population at the knowledge cutoff, the scoped posted cash-book population at the book cutoff, approved statement/journal allocations, reviewed match/approval state, and exception state. It independently recomputes statement movement arithmetic, book opening/period/closing arithmetic, outstanding statement-side and book-side items, allocation source/capacity validity, and the final book-to-bank equation.

The function fails closed unless the statement has exactly one opening and closing balance record at the authority cutoff, statement source identities are non-empty and unique, currencies agree with the run, approved allocations resolve to an in-scope source without exceeding exact capacity, and `book_closing_balance + outstanding_book_items - outstanding_bank_items = statement_closing_balance`. The last invariant raises `reconciliation_database_bridge_unexplained` when the database cannot prove the tie.

The database then hashes canonical JSONB source/control populations with PostgreSQL 18 core `sha256()` and returns server-owned statement, book, and transition snapshot identities. A BEFORE INSERT trigger named `accounting_reconciliation_transition_database_authority_guard` overwrites the caller's three values before the existing transition-command hash trigger runs. PostgreSQL executes same-kind triggers in name order, so the command hash can bind only the database-derived authority values. The child exception-resolution migration may replace the command-hash function without removing or bypassing this earlier authority trigger.

## DDD and ownership

This remains inside the Reconciliation Review supporting subdomain and the `reconciliation_run` aggregate. The new function is a persistence-side invariant/domain-service implementation for the existing lifecycle command; it does not create a second aggregate, does not post or reverse journals, does not close fiscal periods, and does not acquire accounting-policy authority. Bank-statement and journal records remain immutable source evidence. The transition row owns only the retained proof that those sources tied under one lifecycle decision boundary.

## Operability and migration boundary

The parent branch already owns `0020_reconciliation_run_completion_evidence.sql`; that released/parent identity is not available for reuse. The database-authority overlay therefore has the next unique operational identity, `0021_reconciliation_run_database_snapshot_authority.sql`. The earlier branch state that replaced parent `0020` with the new overlay was invalid because it silently removed an existing migration and also caused the repository migration-identity ratchet to reject the chain. Commit lineage `11e6e7087d8ed86c94ab82ad865ad9caf7618eb7` -> `ddae3382831d6b5efd812ba08d3bad39305bde70` -> `1e28c7f9d97023e835c50d1f02b1790254783eb7` renumbered the overlay, updated installer/README references, and `c9994b56526e0af6b6fc77e63e7f1256090bd5fb` restored the exact parent `0020` completion-evidence blob. `fd785df6ec3bc52018554ed3ed6878ba7d71eaf6` updates the regression contract to require both parent `0020` and overlay `0021`.

`migration_install.apply_foundation_migration()` preserves two fail-closed boundaries. If the base chain is incomplete, the canonical base loader retains ownership of the earliest precise write-free diagnostic. If the base chain is complete, the installer requires both parent `0020_reconciliation_run_completion_evidence.sql` and the #43 overlay `0021_reconciliation_run_database_snapshot_authority.sql` before any base database write, then applies the overlay after the base chain. Descendant reconciliation and Period Close migrations must move only through normal non-force restacks onto this repaired identity chain; historical evidence text and released parent identities are not renumbered to make a child stack fit.

No certification claim follows from this repair. It strengthens auditability, tamper resistance, and segregation of application versus database authority in a manner aligned with the repository's SOC 2/CSAP-oriented control posture.

## Verification still required on the exact head

The change is not merge evidence until one unchanged exact head passes the real PostgreSQL lifecycle suite, deferred-invariant proof, 100% owned coverage, repository/docstring contracts, SAST/security/dependency gates, and independent review. In particular, the real PostgreSQL regression must demonstrate that a fabricated application projection cannot cause the persisted transition snapshot/population identities to equal caller-selected values and that a genuinely tied database population remains finalizable.

## References

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: CREATE TRIGGER*. https://www.postgresql.org/docs/18/sql-createtrigger.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Binary string functions and operators*. https://www.postgresql.org/docs/18/functions-binarystring.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: JSON functions and operators*. https://www.postgresql.org/docs/18/functions-json.html
