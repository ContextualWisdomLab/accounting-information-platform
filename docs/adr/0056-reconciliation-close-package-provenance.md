# ADR 0056: Tamper-evident reconciliation close-package provenance

- Status: Accepted in this integration candidate; not a protected-branch release fact until lawful integration.
- Date: 2026-08-28

## Context

The bank-reconciliation vertical produces a read-only close-review projection with exact `Decimal` values, immutable accounting scope, statement/book population identity, exception state, and separately controlled reconciliation approval snapshots. A controller or diligence reviewer also needs a portable evidence manifest that proves which immutable source populations and approvals were assembled for period-close review.

A close package must not become a second approval mechanism or a shortcut around the accounting authority model. Hashing a projection cannot post or reverse a journal, approve reconciliation, close a fiscal period, alter accounting policy, or make model output authoritative. A digest is useful only when the rendered bytes remain bound to the digest and the source evidence named by the manifest is the same evidence held by the accounting system of record.

The authoritative reconciliation run already exists as durable PostgreSQL evidence. `accounting_core.reconciliation_run.knowledge_cutoff_at` is immutable after creation, while the `reconciliation_run_command` table (`accounting_core.reconciliation_run_command`) binds the accepted run command to its database-owned canonical digest, source payload hash/reference, and retained bank-statement record. A close package that trusted caller-supplied copies of those values could misstate the run's knowledge boundary or archive an unrelated source artifact while remaining internally self-consistent. Authority-bearing package construction therefore has to re-read and bind those values from PostgreSQL.

Reconciliation approval snapshots are also database-owned. PostgreSQL computes their canonical digest with the internal `tenant_account_id` identity, while public close-review projections carry the externally stable `tenant_account_reference`. Treating those identifiers as interchangeable makes a genuine database approval unverifiable whenever the public reference differs from the internal UUID. The close package therefore has to retain a technical identity binding sufficient to replay the database snapshot without turning that identity into an authorization credential or accounting-scope substitute.

RFC 3339 permits fractional seconds and uses `Z` for UTC. This repository deliberately narrows the close-package representation to the canonical form emitted for persisted reconciliation runs: uppercase `T`/`Z`, with either whole-second or six-digit microsecond precision. This preserves an exact persisted instant without accepting alternative spellings. NIST FIPS 180-4 specifies SHA-256 as part of the Secure Hash Standard; this ADR uses SHA-256 only as a content-integrity primitive and does not claim FIPS validation or certification. The repository's W3C PROV-O traceability convention remains the conceptual model for source and review provenance.

## Decision

Use deterministic `ReconciliationClosePackage` evidence manifest schema version 4. Version 4 carries the complete structured approval-evidence population, immutable approval-command source hashes, database-owned reconciliation-run provenance, the retained bank-statement artifact, population evidence, and the exact package knowledge cutoff. The projection export is schema version 3 when reviewed allocation evidence carries authoritative source capacity and schema version 2 for the legacy single-source shape without source-capacity fields. Reviewed multi-source matches spanning multiple statement or journal sources use reconciliation snapshot version 2 and bind every normalized allocation with its authoritative source capacity; single-source matches use reconciliation snapshot version 1 as the canonical digest shape. The integration candidate has not been released, so no released artifact is migrated or grandfathered.

The close-review input requires every matching `ReconciliationDecision` to carry a canonical durable match identity and carries that identity in a structured `ReconciliationReviewedMatch` containing candidate facts and the complete normalized statement/journal allocation populations. The read model requires every allocation and decision to align with the corresponding durable match. The package independently revalidates the public projection, exact allocation conservation, population-wide source capacity, and the exact book-to-bank equation:

`reconciled_balance + outstanding_book_items - outstanding_bank_items - bank_closing_balance == unexplained_difference`

A projection is package-eligible only when `suitable_for_period_close_review` is true, the exception count and exception-reference population are empty, and the unexplained difference is exact `Decimal("0")`. Bridge and package arithmetic use dedicated exact-decimal contexts rather than caller ambient precision or exponent bounds. Eligibility is evidence suitability only; it does not authorize reconciliation approval, period close, posting, reversal, or accounting-policy change.

Every authority-built package binds:

- the complete deterministic close-review projection, including exact decimal-string values and tenant/entity/book/bank-assignment/run/population scope;
- the complete `ReconciliationReviewedMatch` population, with candidate facts, normalized statement/journal allocations, exact amounts, and authoritative source capacity when snapshot version 2 requires it;
- exactly one structured `approved` approval-evidence record for every reviewed match, carrying tenant/run/match identity, immutable approval-command `source_payload_hash`, database-owned reconciliation snapshot, and durable evidence reference;
- exactly one database-owned `reconciliation_run` evidence reference whose identity equals the projection run, whose digest is the persisted `reconciliation_run_command.reconciliation_command_hash`, and whose `knowledge_cutoff` is the canonical UTC rendering of `reconciliation_run.knowledge_cutoff_at`;
- exactly one `statement_artifact` evidence reference whose object-store reference and SHA-256 digest are resolved from the run command's retained bank-statement record and artifact;
- exactly one `reconciliation_snapshot_tenant` technical evidence reference derived from PostgreSQL's internal tenant identity and cryptographically bound to the projection's public tenant reference, so offline verification can reproduce the database-owned approval snapshot bytes;
- exactly one `statement_population` evidence reference matching the projection's statement-population identity;
- exactly one `book_population` evidence reference matching the projection's book-population identity;
- a package-level canonical UTC RFC 3339 `knowledge_cutoff` at whole-second or six-digit microsecond precision that exactly equals the database-owned run evidence cutoff; and
- an operator-facing next action requiring the separately authorized reconciliation/period-close decision before any accounting action.

Only `reconciliation_run` evidence may carry `knowledge_cutoff`. Evidence identities are unique by `(evidence_kind_code, evidence_reference)` and sorted before serialization. Approval evidence is unique by reconciliation-match identity and sorted by tenant/run/match/evidence identity.

## Authoritative database state at construction

`build_reconciliation_close_package()` is the authority-bearing construction path. It requires a tenant-bound PostgreSQL session and does not treat caller-shaped run, statement-artifact, match-state, or snapshot-tenant evidence as authoritative.

For the reconciliation run, the builder reads `accounting_core.reconciliation_run` joined to `accounting_core.reconciliation_run_command`, `accounting_integration.bank_statement_record`, and `accounting_integration.bank_statement_artifact` under shared locks. It requires exactly one run command and one retained bank-statement artifact, requires the command source hash to match the statement record and artifact hash, and requires the command source reference to match the retained artifact-store reference. It then derives the package's run digest, cutoff, statement-artifact digest, and statement-artifact reference from those database-owned rows. Caller-provided `knowledge_cutoff`, `reconciliation_run` evidence, or `statement_artifact` evidence must match those values exactly or construction fails closed.

For every reviewed match, the builder reads the current `reconciliation_match` joined to its immutable `reconciliation_approval` under a shared lock. The current match status and approval decision must both remain `approved`, and the approval payload hash/reference and reconciliation snapshot must match the packaged approval evidence. Caller-supplied `reconciliation_match_state` evidence is discarded; the package derives a deterministic state digest from the database-owned row. A match that has moved to `superseded` therefore cannot be newly repackaged from its historical immutable approval.

The builder separately resolves the tenant through the tenant-bound PostgreSQL session. It discards any caller-supplied `reconciliation_snapshot_tenant` evidence and emits a database-owned replacement whose digest binds the public `tenant_account_reference` to the internal `tenant_account_id`. Snapshot verification uses the internal identity only for replaying the PostgreSQL canonical approval-snapshot framing. The internal identity is not accepted as a bearer secret, purpose authorization, tenant-routing override, accounting scope, posting authority, or approval authority.

These reads preserve the existing authority boundary. Statement evidence remains non-posting input, LLM output remains non-authoritative, and package creation cannot approve a reconciliation or write accounting facts.

## Canonical verification and export

The package serializes stable UTF-8 JSON with sorted object keys and exact decimal strings. `package_sha256` is SHA-256 over the canonical unsigned manifest, prefixed with `sha256:`. Changing any approval snapshot, approval source hash, reviewed allocation, source capacity, snapshot-tenant identity binding, current-state evidence, run cutoff/digest, retained statement artifact, population identity, projection fact, or bound source digest changes the package digest.

`verify_reconciliation_close_package()` rebuilds the canonical manifest from an already constructed package and compares the committed digest before bytes are exported. When an authority-built package carries `reconciliation_snapshot_tenant` evidence, verification validates that evidence's public/internal identity binding and uses the bound internal identity to reproduce the database snapshot digest. Pure canonical test fixtures that predate this unreleased identity evidence continue to replay against the public tenant reference; that compatibility path is not an authority-bearing package-construction alternative. `render_reconciliation_close_package_json()` always performs package verification. Pure canonical rebuilding is a verification primitive for frozen package contents; it cannot prove that mutable current database state still agrees with a historical package.

The package is a manifest rather than a digital signature. Authenticity and non-repudiation remain deployment/release evidence concerns; a bare SHA-256 digest does not authenticate an actor or prove possession of a private key.

## Consequences

Controllers and acquirers receive deterministic close evidence whose source artifact, immutable run boundary, complete approved-match population, exact tenant snapshot identity binding, and exact book-to-bank facts can be rehashed and compared. A caller cannot relabel an otherwise valid package with another run cutoff or command digest, substitute an unrelated statement artifact, provide a forged internal tenant identity binding, repurpose a superseded approval as currently approved evidence, silently swap statement/book populations, or hide an internally inconsistent bridge behind a zero unexplained-difference field.

Construction fails closed when the database-owned run or source-artifact population is absent or ambiguous; run command, statement record, and retained artifact hashes/references disagree; caller run/cutoff/artifact evidence differs from PostgreSQL; current match state is not approved; approval evidence is missing, rejected, duplicated, incomplete, outside the projection scope, or cannot be reproduced with the database-owned tenant identity; required population evidence is absent or mismatched; exact allocation/capacity/bridge invariants fail; or identifiers, timestamps, digests, and money values are non-canonical.

A later persistence/API slice may store or transmit the manifest only through an authority-preserving boundary. Any future digital signature or external attestation must wrap the exact package bytes/digest rather than redefine the accounting evidence inside the package.

## References

Klyne, G., & Newman, C. (2002). *Date and time on the Internet: Timestamps* (RFC 3339). RFC Editor. https://doi.org/10.17487/RFC3339

National Institute of Standards and Technology. (2015). *Secure Hash Standard (SHS)* (FIPS PUB 180-4). https://doi.org/10.6028/NIST.FIPS.180-4

National Institute of Standards and Technology. (2023, March 7). *Decision to revise FIPS 180-4, Secure Hash Standard (SHS)*. https://csrc.nist.gov/news/2023/decision-to-revise-fips-180-4

World Wide Web Consortium. (2013). *PROV-O: The PROV ontology*. https://www.w3.org/TR/prov-o/
