# Changelog

## [Unreleased]

### Fixed

- Pinned the Billing-owned journal proposal contract to published `proposal_status` (`draft` | `validated` | `exported` | `rejected`) and ingest only `validated` or `exported` proposals; operational reject rows are not ingested.
- Hash-locked setuptools, wheel, and packaging so exact-head CI can install and wheel the package without build isolation or network resolution.
- Listed every CPython 3.13 coverage 7.15.4 wheel hash plus the universal wheel so ubuntu-latest can install the preferred manylinux x86_64 artifact under `--require-hashes`.

### Added

- Catalog `account_role_mapping` for `cash_receipt` so a Billing `validated` cash proposal posts debit cash / credit accounts receivable through `post_proposal`.
- `accept_journal_proposal` and a stdlib `POST /journal-proposals` boundary that return the AIS posting-receipt contract, scoped by purpose-limited `X-CWL-Tenant-Reference`.
- Catalog policy resolution on `PostgresPostingLedger` so a Billing `JournalProposal` posts through `post_proposal` using AIS `account_role_mapping`, book, period, and policy versions rather than a caller-invented chart mapping.
- First-class fiscal-period close on `PostgresPostingLedger` that snapshots the trial-balance population and sets `soft_closed` or `hard_closed` in one transaction, with idempotent re-close and zero-row rejection of later ordinary posts.
- PostgreSQL 18 posting adapter that persists a balanced journal, idempotent replay, append-only reversal, and trial-balance tie-out through the foundation migration in one commit boundary.
- Hash-locked psycopg 3.3.4 and CPython 3.13 psycopg-binary wheels for exact-head persistence tests.
- Initial Accounting Information Platform product and authority baseline.
- Executable exact-decimal journal proposal, posting, reversal, and trial-balance reference core.
- Consumer contract for Metering Billing Platform journal proposals.
- Authoritative posting-receipt and accounting-policy manifest contracts.
- PostgreSQL 18.4 normalized accounting foundation with tenant-scoped foreign keys and row-level security.
- PRD, TRD, architecture, data model, security, testing, operability, ADRs, and APA 7th standards traceability.
- Offline repository-contract validation and commit-pinned exact-head CI.

### Security

- Rejected card data, PAT plaintext, provider secrets, prompt text, and response text from accounting contracts.
- Required immutable source-payload hashes and fail-closed idempotency conflicts.
