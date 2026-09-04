# Reconciliation command-family constraint lock validation

## Decision status

Proposed on PR #47 until the exact head passes the repository and organization gates. This note records an operability decision only; it does not widen reconciliation, journal-posting, period-close, or accounting-policy authority.

## Problem

Migration `0020_reconciliation_exception_resolution_command.sql` expands the existing `accounting_core.reconciliation_command_identity` command-family `CHECK` constraint to admit `exception_resolution`. A review suggested replacing the immediate check validation with `ADD CONSTRAINT ... NOT VALID` followed by `VALIDATE CONSTRAINT` to reduce migration blocking.

## Constraint and evidence

PostgreSQL 18 documents that `ADD table_constraint` normally scans existing rows, while `NOT VALID` skips that scan. It also documents that most `ADD table_constraint` forms acquire `ACCESS EXCLUSIVE`, whereas `VALIDATE CONSTRAINT` acquires `SHARE UPDATE EXCLUSIVE`. PostgreSQL's explicit-locking contract states that a lock, once acquired, is normally held until the end of the transaction.

Migration 0020 is deliberately one atomic `BEGIN`/`COMMIT` authority migration. Therefore adding the replacement `CHECK` as `NOT VALID` and validating it later in the same transaction does **not** release the earlier `ACCESS EXCLUSIVE` lock before validation. It changes the sequence of work but does not provide the claimed reduced concurrent-update blocking boundary. A trial implementation at `26201383d2f996422918fc615c3e49d2be8c5ca3` was consequently removed by successor `3e73ea5e2efc9d49184031b923f3b6c5bc194cfc`; the canonical atomic migration semantics are retained.

A genuinely lower-blocking replacement would require a deliberately staged, restart-safe migration protocol with a commit boundary between installing an enforced-but-unvalidated replacement constraint and validating it, followed by a short canonical-name swap. That is a materially different migration contract: it creates a partially applied but safe intermediate state and therefore requires its own upgrade/retry/rollback acceptance suite before adoption. It is not introduced as an incidental PR-review quick fix.

## Test-quality follow-up

The useful independent part of the review was retained. `tests/test_reconciliation_exception_resolution_review_regressions.py` now asserts the actual immutable SQL comparisons (`NEW.<field> IS DISTINCT FROM OLD.<field>`) for owner/action/effective/recorded evidence rather than merely searching for field-name substrings. `_Ledger` shared mutable fixture state is also explicitly annotated as `ClassVar`, preventing Ruff RUF012 from confusing intentional shared test state with instance dataclass-style state.

## Failure and operator scenes

If the command-identity table becomes large enough that the current atomic constraint replacement produces unacceptable lock wait, operators must not weaken the constraint or shorten validation coverage. The next change should introduce a staged migration with exact lock-wait measurement, safe resume after interruption at every commit boundary, real PostgreSQL concurrent-writer acceptance, and an explicit rollback/remediation path. Until that evidence exists, atomic authority installation is preferred over a cosmetic `NOT VALID` sequence that leaves the same strongest lock held.

## References

PostgreSQL Global Development Group. (2026). *ALTER TABLE (PostgreSQL 18 documentation).* https://www.postgresql.org/docs/18/sql-altertable.html

PostgreSQL Global Development Group. (2026). *Explicit locking (PostgreSQL 18 documentation).* https://www.postgresql.org/docs/18/explicit-locking.html
