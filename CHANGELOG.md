# Changelog

## [Unreleased]

### Changed

- Replaced the engineer-oriented root README with a buyer/operator document:
  current foundation readiness, independent run without Naruon or sibling
  checkouts, and the sibling call path through the published file contracts.
- Recorded pull-request stacking, exact-head CI, writer-boundary, and
  review-bot procedure in `docs/CONTRIBUTING.md` so they stay out of the
  buyer/operator README.

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
