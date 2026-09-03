# Financial report artifact and XBRL export implementation plan

> Execute this plan with test-driven development and exact-head verification.

**Goal:** Turn the existing four-statement package into a deterministic report artifact with structured explanations and a taxonomy-profile-driven XBRL 2.1 export, without creating a second accounting truth.

**Architecture:** Add one stateless `financial_reporting` module downstream of `load_financial_statement_package`. Exact decimal facts and source paths form a canonical artifact. XBRL serialization accepts an immutable external taxonomy profile and performs no taxonomy fetch or accounting calculation.

**Technology:** Python 3.13 standard library, `Decimal`, `dataclasses`, `hashlib`, `json`, `urllib.parse`, `xml.etree.ElementTree`, `unittest`, GitHub Actions with PostgreSQL 18.4.

---

## Task 1: Lock the public contract with failing tests

**Files:**
- Create: `tests/test_financial_reporting.py`

1. Add fixtures for the complete current and comparative four-statement package.
2. Assert deterministic artifact identity, exact profit-or-loss facts, source evidence paths, and explanation records.
3. Assert malformed decimals, missing statements, broken statement equations, and incomplete comparison context fail closed.
4. Assert a supplied taxonomy profile produces deterministic XBRL contexts, unit, schema reference, facts, and digest.
5. Assert invalid profile identifiers, prefixes, namespaces, schema references, digests, duplicate mappings, missing fact mappings, and duplicate concept-context facts fail closed.
6. Open a draft pull request so the missing production module is observed as a real RED test on the exact head.

## Task 2: Implement the canonical report domain

**Files:**
- Create: `src/accounting_information_platform/financial_reporting.py`

1. Add immutable `FinancialReportContext`, `XbrlConceptMapping`, and `XbrlTaxonomyProfile` value objects.
2. Validate entity identity, currency, period ranges, decimal precision, taxonomy URIs, XML names, package digests, and mapping uniqueness.
3. Canonicalize the existing statement package and compute its SHA-256 identity.
4. Aggregate exact facts from authoritative statement lines and top-level statement totals.
5. Verify profit-or-loss, financial-position, changes-in-equity, and cash-flow invariants.
6. Build deterministic explanation records with codes, exact parameters, directions, and evidence paths.
7. Return a JSON-compatible report artifact with no clock or random dependency.

## Task 3: Implement XBRL 2.1 serialization

**Files:**
- Modify: `src/accounting_information_platform/financial_reporting.py`

1. Register stable XBRL, linkbase, XLink, ISO 4217, and taxonomy namespaces.
2. Create duration and instant contexts for current and optional comparison periods.
3. Create the reporting-currency unit.
4. Map canonical facts through the supplied profile and reject missing or duplicate concept-context facts.
5. Serialize mapped monetary facts with explicit context, unit, and decimal precision.
6. Return the XML instance, media type, profile and taxonomy provenance, source report hash, and instance hash.

## Task 4: Publish the package API

**Files:**
- Modify: `src/accounting_information_platform/__init__.py`
- Modify: `tests/test_package_api.py` if the repository has a dedicated public-surface test; otherwise cover exports in `tests/test_financial_reporting.py`.

1. Export all new value objects and functions from the package root.
2. Preserve existing import behavior and typing marker packaging.
3. Verify public APIs have complete docstrings.

## Task 5: Record the architecture decision and standards boundary

**Files:**
- Create: `docs/adr/0065-financial-report-artifact-xbrl-boundary.md`
- Modify: `docs/PRD.md`
- Modify: `docs/TRD.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/DATA_MODEL.md`
- Modify: `docs/TEST_STRATEGY.md`
- Modify: `docs/doctoring/REFERENCES.md`
- Modify: `docs/doctoring/STANDARD_TRACEABILITY.md`
- Modify: `CHANGELOG.md`

1. Preserve the existing statement package as the sole numerical source.
2. Record the injected taxonomy-profile decision and rejected alternatives.
3. Document current stable standards: XBRL 2.1, OIM 1.0, Calculations 1.1, Inline XBRL 1.1, and IFRS Accounting Taxonomy 2025 for 2026 reporting.
4. Mark Project Tavi and Taxonomy Packages 1.1 as monitored drafts, not production contracts.
5. State explicitly that this branch does not claim filing acceptance or XBRL certification.

## Task 6: Transfer successor gaps to the active product baseline owner

**Files:**
- Comment on the active `docs/product-technical-gap-baseline.md` pull request rather than creating a competing writer.
- Create one product-gap issue for persistence, official taxonomy profiles, independent validation, Inline XBRL, and accessible renderers.

1. Link the implementation pull request and exact head.
2. Provide RED/GREEN acceptance criteria for the successor.
3. Keep the current baseline writer as the single writer for its document.

## Task 7: Verify and review

**Commands executed by the repository workflow:**

```bash
PYTHONPATH=src:. python -m unittest discover -s tests -p 'test_*.py' -v
PYTHONPATH=src:. python -m coverage run --branch -m unittest discover -s tests -p 'test_*.py'
python -m coverage report --fail-under=100 --show-missing
python scripts/validate_repository.py .
python -m compileall -q src scripts tests
python -m pip wheel --no-deps --no-build-isolation -w dist .
```

1. Confirm the test-only head fails for the intended missing implementation.
2. Implement the smallest production code that satisfies the contract.
3. Inspect exact-head CI, SAST, security, dependency-diff, coverage, packaging, and provenance results.
4. Inspect CodeRabbit review and resolve every verified finding.
5. Do not mark the pull request ready or merge it until exact-head checks, independent review, and repository policy permit it.
