# Financial reporting and XBRL proposal slice

## Added

- Added an exact-`Decimal` financial-report proposal package downstream of the existing four-statement read boundary.
- Added current and comparative profit-or-loss headlines, canonical four-statement facts, supplied source paths, claimed snapshot references, source/package digests, and structured explanation codes.
- Added `FinancialReportContext`, `XbrlConceptMapping`, and `XbrlTaxonomyProfile` contracts.
- Added deterministic XBRL 2.1 proposal serialization with duration/instant contexts, ISO 4217 unit, schema reference, monetary facts, and instance digest.
- Added ADR 0067, financial-reporting product documentation, XBRL standards traceability, a dedicated test matrix, and successor Issue #51.

## Changed

- Separated low-level proposal formatting from authoritative AIS publication. Caller-supplied report packages can no longer receive an authoritative-looking report URN.
- The low-level artifact is always `proposed`, `caller_supplied_statement_package`, `unverified`, and `authoritative_report=false`.
- The XBRL result is always `xbrl_validation_status_code=not_run`, `filing_readiness_code=not_ready`, and `authoritative_report=false`.

## Security and correctness

- Rebuilds the full proposal from embedded supplied evidence before XBRL export, rejecting altered derived facts even when an outer hash is recomputed.
- Rejects binary floating-point monetary values, non-finite decimals, malformed line shapes, invalid date types, XML 1.0-forbidden characters, reserved `xml*` taxonomy prefixes, empty chart-account codes on ledger-backed statements, and internally torn snapshot claims.
- Performs no taxonomy/schema fetch, DTD parsing, external-entity resolution, linkbase processing, active-content execution, or model-provider call.

## Not implemented or claimed

- No AIS-owned PostgreSQL report-run/source-authority command.
- No official IFRS Accounting Taxonomy or DART profile.
- No independent XBRL 2.1, Calculations 1.1, Formula, Inline XBRL, or jurisdiction validation.
- No accessible HTML, PDF, spreadsheet, or Inline XBRL renderer.
- No maker-checker approval, authoritative publication, regulator acceptance, audit, or assurance claim.

This fragment must be folded into the root `CHANGELOG.md` before PR #50 is made ready or released. It exists as a reviewable single-writer-safe record while other stacked PRs are concurrently modifying the root changelog.