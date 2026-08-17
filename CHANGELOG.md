# Changelog

## [Unreleased]

### Changed

- Replaced the engineer-oriented root README with a buyer/operator document:
  current foundation readiness, independent run without Naruon or sibling
  checkouts, and the sibling call path through the published file contracts.
- Recorded pull-request stacking, exact-head CI, writer-boundary, and
  review-bot procedure in `docs/CONTRIBUTING.md` so they stay out of the
  buyer/operator README.

### Fixed

- Hash-locked setuptools, wheel, and packaging so exact-head CI can install and wheel the package without build isolation or network resolution.
- Listed every CPython 3.13 coverage 7.15.4 wheel hash plus the universal wheel so ubuntu-latest can install the preferred manylinux x86_64 artifact under `--require-hashes`.

### Added

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
