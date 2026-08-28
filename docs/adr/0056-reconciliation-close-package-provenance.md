# ADR 0056: Tamper-evident reconciliation close-package provenance

- Status: Accepted in this integration candidate; not a protected-branch release fact until lawful integration.
- Date: 2026-08-28

## Context

The bank-reconciliation vertical already produces a read-only close-review projection with exact `Decimal` values, immutable accounting scope, statement/book population identity, exception state, and a separately controlled reconciliation approval snapshot. A controller or diligence reviewer still needs a portable evidence manifest that proves which immutable source populations and which approval snapshot were assembled for period-close review.

A close package must not become a second approval mechanism or a shortcut around the accounting authority model. In particular, hashing a projection cannot post or reverse a journal, approve reconciliation, close a fiscal period, alter accounting policy, or make model output authoritative. A digest is useful only if the bytes being rendered remain bound to that digest and if the population evidence named by the manifest is the same population named by the close-review projection.

The authoritative reconciliation run is already durable database evidence. `accounting_core.reconciliation_run.knowledge_cutoff_at` is required and immutable after creation, so a package-level cutoff that is merely caller supplied would be weaker than the source-of-record contract: the same projection and source hashes could otherwise be repackaged under different plausible UTC instants. The package therefore has to carry a digest-bound `reconciliation_run` evidence reference and require the package cutoff to equal the immutable run-evidence cutoff exactly.

RFC 3339 defines an Internet date/time profile of ISO 8601 and permits `Z` to denote a UTC offset of `00:00`; this repository deliberately narrows that profile to uppercase `T`/`Z` and second precision for one canonical wire representation. RFC 3339 has been updated by RFC 9557, but that update does not require this evidence contract to admit additional timezone metadata. NIST FIPS 180-4 specifies SHA-256 among the Secure Hash Standard algorithms and describes message digests as a means to detect whether messages have changed. NIST decided in 2023 to revise FIPS 180-4, principally to remove SHA-1 and incorporate updated guidance; current NIST algorithm-validation material still lists SHA-256 among the FIPS 180-4 SHA-2 functions. This ADR therefore uses SHA-256 as a content-integrity primitive without claiming FIPS validation or certification. The repository's W3C PROV-O traceability convention remains the conceptual provenance model for binding source entities and review evidence.

## Decision

Introduce deterministic `ReconciliationClosePackage` evidence manifest schema version 2. Version 2 makes reconciliation-run cutoff provenance a required part of the canonical digest-bound payload; the integration candidate has not been released, so no released version-1 artifact is migrated or grandfathered.

The package revalidates the public close-review projection before hashing it. All accounting/run/population identities must be canonical non-empty strings; monetary and delta values must be finite `Decimal` values; exception identities must be canonical and unique; and the projection must be internally close-review eligible. `suitable_for_period_close_review` must be true, the exception count and exception-reference population must both be empty, and the unexplained difference must be exact `Decimal("0")`. Eligibility is evidence suitability only; it is not approval or close authority.

Every package binds:

- the complete deterministic close-review projection, including exact decimal-string values and immutable tenant/entity/book/bank-assignment/run/population scope;
- one non-empty reconciliation approval evidence reference;
- one canonical database-owned approval snapshot digest in `sha256:<64 lowercase hex>` form;
- one `reconciliation_run` evidence reference whose identity exactly equals the projection's `reconciliation_run_reference`, whose digest identifies immutable exported run evidence, and whose `knowledge_cutoff` is the canonical UTC representation of that run's immutable `knowledge_cutoff_at`;
- a package-level canonical UTC RFC 3339 second-precision `knowledge_cutoff` that must exactly equal the `reconciliation_run` evidence cutoff;
- one `statement_population` evidence reference whose identity exactly equals the projection's `statement_population_reference`;
- one `book_population` evidence reference whose identity exactly equals the projection's `book_population_reference`;
- at least one immutable `statement_artifact` evidence reference with a canonical SHA-256 digest; and
- an operator-facing next action that explicitly requires the separately authorized reconciliation/period-close decision.

Only `reconciliation_run` evidence may carry `knowledge_cutoff`; source-artifact and population evidence cannot introduce competing cutoff claims. The calling adapter must derive the run reference, digest, and cutoff from immutable reconciliation-run evidence rather than accept an arbitrary operator/model timestamp. A mismatch between package and run cutoffs fails closed before hashing.

Evidence references are unique by `(evidence_kind_code, evidence_reference)` and sorted before serialization. The package serializes a stable UTF-8 JSON mapping with sorted object keys and exact decimal strings. `package_sha256` is SHA-256 over the canonical unsigned manifest, prefixed with `sha256:`. The run-evidence cutoff is inside that canonical manifest. Changing the approval snapshot, projection, run cutoff/evidence, population identity, or any bound source digest therefore changes the package digest.

`verify_reconciliation_close_package()` rebuilds the canonical manifest from the package fields and compares the committed digest before bytes are exported. `render_reconciliation_close_package_json()` always invokes that verification and therefore fails closed if a caller directly constructs or mutates a dataclass-shaped payload with a stale digest, non-canonical evidence order, a cutoff that differs from immutable run evidence, or a different next action. The package is intentionally a manifest rather than a signature: authenticity/non-repudiation remains a deployment and release evidence concern, and this slice does not claim that a bare SHA-256 digest authenticates an actor or proves possession of a private key.

## Consequences

Controllers and acquirers gain a deterministic reconciliation close artifact whose source, immutable run cutoff, and approval bindings can be rehashed and compared without reconstructing mutable application state. Statement/book population evidence cannot be silently swapped under an otherwise plausible projection, and an identical evidence population cannot be relabelled with a caller-selected knowledge cutoff. Evidence ordering cannot change the package digest, and exact money does not round-trip through binary floating point.

The package is fail-closed if close-review eligibility is false, required or projection-bound source/run evidence is absent/mismatched, package and immutable-run cutoffs differ, an evidence identity is duplicated, a monetary value is non-finite, an identity/digest/cutoff is non-canonical, or rendered payload content no longer matches `package_sha256`. No package field can invoke journal posting, reconciliation approval, or fiscal-period close.

A later persistence/API slice may store or transmit this manifest only through an authority-preserving boundary. If digital signatures or external attestations are added, they must wrap the exact package bytes/digest rather than redefine the accounting evidence inside the package.

## References

Klyne, G., & Newman, C. (2002). *Date and time on the Internet: Timestamps* (RFC 3339). RFC Editor. https://doi.org/10.17487/RFC3339

National Institute of Standards and Technology. (2015). *Secure Hash Standard (SHS)* (FIPS PUB 180-4). https://doi.org/10.6028/NIST.FIPS.180-4

National Institute of Standards and Technology. (2023, March 7). *Decision to revise FIPS 180-4, Secure Hash Standard (SHS)*. https://csrc.nist.gov/news/2023/decision-to-revise-fips-180-4

World Wide Web Consortium. (2013). *PROV-O: The PROV ontology*. https://www.w3.org/TR/prov-o/
