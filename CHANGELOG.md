# Changelog

## [Unreleased]

### Changed

- Rewrote the README as a customer and operator page; moved local validation to `CONTRIBUTING.md` and the next durable-intake increment to `docs/doctoring/IMPLEMENTATION_SEQUENCE.md`.
- Expanded ADRs 0001–0005 with Context and APA 7th References grounded in already claimed standards.

### Fixed

- Hash-locked `setuptools==84.0.0` so CI `--no-build-isolation` editable and wheel installs can import `setuptools.build_meta`.

### Added

- ADR 0006 for reporting taxonomy as a versioned projection of the journal core.
- PostgreSQL `numeric` types citation in the standards bibliography.
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
