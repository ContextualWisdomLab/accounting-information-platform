# Changelog

## [Unreleased]

### Fixed

- Hash-locked setuptools, wheel, and packaging so exact-head CI can install and wheel the package without build isolation or network resolution.
- Listed every CPython 3.13 coverage 7.15.4 wheel hash plus the universal wheel so ubuntu-latest can install the preferred manylinux x86_64 artifact under `--require-hashes`.

### Added

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
