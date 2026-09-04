# Financial report proposal and XBRL export implementation plan

> Execute this plan with test-driven development, repair-first review, and exact-head verification.

**Goal:** Turn a supplied four-statement-shaped package into a deterministic, explicitly unverified report proposal with structured explanations and taxonomy-profile-driven XBRL 2.1 serialization, while reserving authoritative report identity for a later PostgreSQL owner command.

**Architecture:** Add one stateless `financial_reporting` package. The pure functions verify internal arithmetic and preserve exact values, supplied source paths, and hashes. They do not query PostgreSQL or prove source authority. Every output remains `proposed`, `caller_supplied_statement_package`, and `unverified`. A successor AIS command must derive currency/dates/source population from PostgreSQL, persist provenance, validate, approve, and publish.

**Technology:** Python 3.13 standard library, `Decimal`, `dataclasses`, `hashlib`, `json`, `urllib.parse`, `xml.etree.ElementTree`, `unittest`, GitHub Actions with PostgreSQL 18.4.

---

## Task 1: Lock the public contract with failing tests

**Files:**
- Create: `tests/financial_reporting_fixtures.py`
- Create: `tests/test_financial_reporting.py`
- Create: `tests/test_financial_reporting_context.py`
- Create: `tests/test_financial_reporting_artifact_validation.py`
- Create: `tests/test_xbrl_reporting_validation.py`

1. Add current and comparative four-statement fixtures.
2. Assert deterministic proposal identity, exact profit-or-loss facts, supplied source paths, and explanation records.
3. Assert malformed decimals, missing statements, broken equations, and incomplete comparison context fail closed.
4. Assert a supplied taxonomy profile produces deterministic XBRL contexts, unit, schema reference, facts, and digest.
5. Assert invalid profile identity, URI, digest, mapping, period type, and artifact tampering fail closed.
6. Open a draft PR with the test-first missing-API state. Do not misstate queued hosted jobs as a recorded RED failure.

## Task 2: Implement proposal contracts and arithmetic controls

**Files:**
- Create: `src/accounting_information_platform/financial_reporting/__init__.py`
- Create: `src/accounting_information_platform/financial_reporting/contracts.py`
- Create: `src/accounting_information_platform/financial_reporting/primitives.py`
- Create: `src/accounting_information_platform/financial_reporting/statements.py`
- Create: `src/accounting_information_platform/financial_reporting/artifact.py`

1. Add immutable `FinancialReportContext`, `XbrlConceptMapping`, and `XbrlTaxonomyProfile` value objects.
2. Validate context shape, taxonomy URIs/names/digests, and mapping uniqueness.
3. Canonicalize the supplied package and compute content identities.
4. Aggregate exact facts from supplied statement lines and totals.
5. Verify profit-or-loss, financial-position, changes-in-equity, cash-flow, and cross-statement arithmetic.
6. Build deterministic explanation records with codes, exact parameters, directions, and supplied source paths.
7. Unconditionally classify output as:

```text
truth_status_code = proposed
source_authority_code = caller_supplied_statement_package
publication_readiness_code = unverified
authoritative_report = false
report_artifact_reference = urn:cwl:accounting:financial_report_proposal:{sha256}
```

8. Add an adversarial test showing a balanced unrecorded tenant/entity package with relabelled caller currency/dates cannot receive authoritative identity.

## Task 3: Implement XBRL 2.1 proposal serialization

**Files:**
- Create: `src/accounting_information_platform/financial_reporting/xbrl.py`

1. Register stable XBRL, linkbase, XLink, ISO 4217, and taxonomy namespaces.
2. Create duration and instant contexts for current and optional comparison periods.
3. Create the reporting-currency unit.
4. Verify source/proposal hashes and rebuild the complete proposal from embedded supplied evidence.
5. Map proposal facts through the supplied profile and reject missing facts or period-type disagreement.
6. Serialize mapped monetary facts with context, unit, and decimal precision.
7. Preserve non-authoritative proposal classification and return:

```text
xbrl_validation_status_code = not_run
filing_readiness_code = not_ready
authoritative_report = false
```

8. Return deterministic XML, proposal-specific file name, report/taxonomy metadata, and instance digest.

## Task 4: Publish the package API

**Files:**
- Modify: `src/accounting_information_platform/__init__.py`

1. Export all new value objects and functions from the package root.
2. Preserve existing import behavior and typing marker packaging.
3. Verify public APIs have complete docstrings.

## Task 5: Record the authority and standards decisions

**Files:**
- Create: `docs/adr/0067-financial-report-artifact-xbrl-boundary.md`
- Create: `docs/FINANCIAL_REPORTING.md`
- Create: `docs/doctoring/XBRL_STANDARD_TRACEABILITY.md`
- Create: `docs/testing/FINANCIAL_REPORTING_TEST_MATRIX.md`
- Modify: `docs/PRD.md`
- Modify: `docs/TRD.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/DATA_MODEL.md`
- Modify: `CHANGELOG.md` before readiness

1. Preserve the existing statement package as the supported numerical source when called by AIS.
2. Record that the pure functions do not prove AIS database origin.
3. Record the injected taxonomy-profile decision and rejected alternatives.
4. Document XBRL 2.1, OIM 1.0, Calculations 1.1, Inline XBRL 1.1, and IFRS Accounting Taxonomy 2025 for 2026 reporting.
5. Mark Project Tavi and Taxonomy Packages 1.1 as monitored drafts.
6. State explicitly that the branch does not claim authoritative report origin, filing acceptance, taxonomy conformance, certification, or assurance.
7. Avoid duplicating general standard references already owned by repository-wide doctoring; the XBRL-specific crosswalk owns this slice.

## Task 6: Transfer successor gaps to the active baseline owner

**Actions:**
- Comment on the active `docs/product-technical-gap-baseline.md` PR rather than creating a competing writer.
- Create one product-gap issue for the authoritative report round trip.

1. Link PR #50, ADR 0067, and exact head.
2. Transfer the owner command, 3NF registry, object storage, official IFRS/DART profiles, independent validation, Inline XBRL, accessible renderers, localized explanations, approval, publication, and withdrawal gaps.
3. Provide RED/GREEN acceptance criteria.
4. Keep the current baseline PR as the single writer.

## Task 7: Implement the authoritative owner path in a successor PR

**Not part of this branch.**

1. Accept tenant/entity/book/period/purpose/profile identifiers and idempotency evidence, never report amounts.
2. Authenticate through the established product identity boundary.
3. Derive currency and date ranges from AIS-owned legal-entity/book/calendar/reporting-policy facts.
4. Load the four statements and source population under PostgreSQL `REPEATABLE READ`.
5. Retain journal or close-snapshot population, knowledge cutoff, close/live/provisional state, and package digest.
6. Persist report run, source, proposal, artifact, and outbox atomically under forced tenant RLS.
7. Run independent XBRL/Calculations/Formula/jurisdiction validation.
8. Obtain maker-checker approval.
9. Issue authoritative report/publication identity only after required gates pass.

## Task 8: Verify and review

**Commands executed by the repository workflow:**

```bash
PYTHONPATH=src:. python -m unittest discover -s tests -p 'test_*.py' -v
PYTHONPATH=src:. python -m coverage run --branch -m unittest discover -s tests -p 'test_*.py'
python -m coverage report --fail-under=100 --show-missing
python scripts/validate_repository.py .
python -m compileall -q src scripts tests
python -m pip wheel --no-deps --no-build-isolation -w dist .
```

1. Preserve the test-first commit history.
2. Re-run focused isolated verification after the authority repair; earlier-head results are not current-head evidence.
3. Inspect exact-head CI, SAST, security, dependency, package, SBOM, and provenance results.
4. Request CodeRabbit review after code/doc stabilization and resolve every verified finding.
5. Keep the PR Draft and do not merge until exact-head checks, independent review, and repository policy permit it.
