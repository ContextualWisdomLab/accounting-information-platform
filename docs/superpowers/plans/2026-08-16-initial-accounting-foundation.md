# Initial Accounting Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Establish an executable proposal-to-posting, reversal, and trial-balance foundation with explicit billing and accounting authority boundaries.

**Architecture:** Keep monetary and accounting rules in a dependency-free domain core. Use closed JSON Schema contracts and normalized PostgreSQL records as public boundaries. Treat the in-memory ledger as a reference oracle that future PostgreSQL and API adapters must match.

**Tech Stack:** Python 3.13 standard library, `decimal.Decimal`, JSON Schema Draft 2020-12, PostgreSQL 18.4-compatible SQL, GitHub Actions with full-SHA pins.

## Global Constraints

- All database object names contain at least two `snake_case` words.
- Authoritative database records are third-normal-form oriented and tenant scoped.
- Binary floating-point accounting arithmetic is prohibited.
- Posted journals are immutable and corrections use reversal lineage.
- Production statement and branch coverage are 100%.
- Every public API has a docstring.
- GitHub Actions use full commit SHAs and quality dependencies use hashes.

---

### Task 1: Executable accounting invariants

**Files:**
- Create: `tests/test_accounting_core.py`
- Create: `src/accounting_information_platform/core.py`
- Create: `src/accounting_information_platform/__init__.py`

**Interfaces:**
- Produces: `JournalLineProposal`, `JournalProposal`, `AccountingPolicy`, `PostingLedger`, `PostingReceipt`, and `AccountBalance`.

- [x] **Step 1: Write failing tests for balance, line sidedness, policy scope, open period, idempotency, posting, reversal, and trial balance.**
- [x] **Step 2: Run the tests and confirm import failure because the public accounting API does not exist.**
- [x] **Step 3: Implement canonical decimal parsing and immutable domain objects.**
- [x] **Step 4: Implement deterministic posting, replay, conflict detection, reversal, and trial balance.**
- [x] **Step 5: Run tests and branch coverage to 100%.**

### Task 2: Contract and persistence baseline

**Files:**
- Create: `schemas/accounting-journal-proposal.schema.json`
- Create: `schemas/accounting-posting-receipt.schema.json`
- Create: `schemas/accounting-policy-manifest.schema.json`
- Create: `database/migrations/0001_accounting_foundation.sql`

**Interfaces:**
- Consumes: semantic account roles and source hashes from the billing proposal contract.
- Produces: authoritative receipt and policy manifest contracts plus normalized PostgreSQL records.

- [x] **Step 1: Write repository tests that require explicit contract authority and posting-status ownership.**
- [x] **Step 2: Confirm tests fail while schemas and migration are absent.**
- [x] **Step 3: Add closed Draft 2020-12 schemas with exact decimal strings and immutable source identity.**
- [x] **Step 4: Add tenant-scoped PostgreSQL records, composite foreign keys, RLS, reversal lineage, trial-balance snapshots, and transactional outbox.**
- [x] **Step 5: Run repository validation and fix every naming or authority violation.**

### Task 3: Governance and operational evidence

**Files:**
- Create: `README.md`, `AGENTS.md`, `CLAUDE.md`, `CHANGELOG.md`
- Create: `docs/PRD.md`, `docs/TRD.md`, `docs/ARCHITECTURE.md`, `docs/DATA_MODEL.md`
- Create: `docs/ACCOUNTING_BOUNDARY.md`, `docs/SECURITY.md`, `docs/TEST_STRATEGY.md`, `docs/OPERABILITY.md`
- Create: `docs/adr/*.md`, `docs/doctoring/*.md`

**Interfaces:**
- Produces: authority, security, development, standards, and next-action contracts.

- [x] **Step 1: Require every governance document in repository tests.**
- [x] **Step 2: Add the focused documents without unresolved placeholders.**
- [x] **Step 3: Record APA 7th references and decision-to-standard traceability.**
- [x] **Step 4: Re-run placeholder, schema, SQL naming, and source-scan validation.**

### Task 4: Exact-head CI and publish

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.coveragerc`, `.gitignore`, `requirements-quality.txt`, `pyproject.toml`

**Interfaces:**
- Produces: `Accounting foundation` exact-head check and a Draft pull request.

- [x] **Step 1: Add tests rejecting mutable action tags and unhashed quality dependencies.**
- [x] **Step 2: Add minimum-permission, concurrency-controlled CI with full-SHA action pins.**
- [x] **Step 3: Run unit tests, complete branch coverage, repository validation, and compile checks locally.**
- [x] **Step 4: Commit the isolated branch and open a Draft PR for independent review.**
