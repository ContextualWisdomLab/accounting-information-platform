# Reconciliation close snapshot authority — 2026-09-01

## Scope

This doctoring note records the research and standards basis for the authority-bearing reconciliation close-package repair on `fix/reconciliation-multi-match-conservation`. It is evidence for the integration candidate, not a release or certification claim.

The repaired construction path derives statement and cash-ledger populations, approved allocations, run state, approval state, exception state, and the exact book-to-bank bridge from PostgreSQL rather than accepting caller-shaped population identifiers or monetary bridge values. Those authority reads are required to share one PostgreSQL `REPEATABLE READ` transaction. The assigned cash account is also scoped to the accounting book that owns it; tenant identity plus chart-account identity alone is not a sufficient accounting-book boundary.

## Why one snapshot is required

A close package is an audit/evidence manifest assembled from several relations. Under PostgreSQL `READ COMMITTED`, successive statements in one transaction may observe different committed database states. That is unsuitable for an authority-bearing package whose population digests and monetary bridge claim to describe one coherent reconciliation state. PostgreSQL documents that `REPEATABLE READ` keeps all ordinary reads in the transaction on the transaction's initial snapshot, subject to PostgreSQL's documented serialization/anomaly semantics. The package builder therefore uses the repository's existing consistent-read session rather than the ordinary session.

Recent database-isolation research also treats strong isolation guarantees as an integrity contract whose actual behavior must be verified rather than merely assumed. Cai et al. (2025) develop sound and complete verification encodings for serializability and snapshot isolation and emphasize that strong isolation guarantees are essential to database consistency and integrity. That work does not itself prescribe this accounting product's exact isolation level, but it supports making the selected isolation semantics explicit, testable, and part of the product contract instead of leaving cross-query consistency implicit.

## Product invariant

For one authority-bearing package, the following facts must be read from one consistent database snapshot and remain in the same tenant/run/accounting-book/currency/knowledge-cutoff scope:

- reconciliation run and immutable command/source provenance;
- retained bank-statement artifact and normalized opening/closing balances;
- immutable statement-entry population at the run knowledge cutoff;
- assigned cash-account journal population through the book and knowledge cutoffs;
- current approved statement/journal allocation population;
- current approved match/approval snapshots; and
- current reconciliation exception population.

The cash-journal query joins the assigned `chart_account` and requires the journal's `accounting_book_id` to equal that cash account's owning `accounting_book_id`. This prevents a same-tenant chart-account identifier from becoming a cross-book authority shortcut.

The resulting statement/book populations receive deterministic SHA-256 content identities. Exact `Decimal` arithmetic derives opening balances, current-period movements, closing balances, approved consumption, outstanding bank/book items, and the bridge. Caller-supplied statement/book population references and bridge money fields are replaced by these database-derived facts before canonical package verification. Unexplained opening or carry-forward differences are rejected until a durable policy-backed historical-evidence model exists; they are not synthesized to force the bridge to balance.

## Verification contract

The focused regression contracts require:

1. authority-bearing construction to enter `PostgresPostingLedger._consistent_read_session()`;
2. the cash-journal population to prove the accounting-book boundary through the assigned cash chart account;
3. exact statement and book population digests to be derived from source rows;
4. approved allocations never to consume more than immutable source capacity;
5. current-period unmatched bank and book items to remain explicit outstanding items; and
6. an unexplained database-owned bridge difference to fail closed.

Repository and organization exact-head CI, real PostgreSQL integration, security scanning, current-head review, coverage, and release evidence remain authoritative for landing. A local model or an older workflow run cannot substitute for those gates.

## References

Cai, Z., Liu, S., Wei, H., Chen, Y., & Pan, A. (2025). Fast verification of strong database isolation (Extended Version). *Proceedings of the VLDB Endowment, 19*, 563–575. https://consensus.app/papers/fast-verification-of-strong-database-isolation-extended-cai-liu/e7131ce449515d41ab6104cc32c3e2b7/

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Transaction isolation*. https://www.postgresql.org/docs/18/transaction-iso.html

Related product decision: `docs/adr/0056-reconciliation-close-package-provenance.md`.
