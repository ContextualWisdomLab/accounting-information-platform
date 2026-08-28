# ADR 0056: Tamper-evident reconciliation close-package provenance

- Status: Accepted in this integration candidate; not a protected-branch release fact until lawful integration.
- Date: 2026-08-28

## Context

The bank-reconciliation vertical already produces a read-only close-review projection with exact `Decimal` values, immutable accounting scope, statement/book population identity, exception state, and a separately controlled reconciliation approval snapshot. A controller or diligence reviewer still needs a portable evidence manifest that proves which immutable source populations and which approval snapshot were assembled for period-close review.

A close package must not become a second approval mechanism or a shortcut around the accounting authority model. In particular, hashing a projection cannot post or reverse a journal, approve reconciliation, close a fiscal period, alter accounting policy, or make model output authoritative.

NIST FIPS 180-4 specifies SHA-256 among the Secure Hash Standard algorithms and describes message digests as a means to detect whether messages have changed. NIST decided in 2023 to revise FIPS 180-4, principally to remove SHA-1 and incorporate updated guidance; current NIST algorithm-validation material still lists SHA-256 among the FIPS 180-4 SHA-2 functions. This ADR therefore uses SHA-256 as a content-integrity primitive without claiming FIPS validation or certification. The repository's W3C PROV-O traceability convention remains the conceptual provenance model for binding source entities and review evidence.

## Decision

Introduce a deterministic `ReconciliationClosePackage` evidence manifest with schema version 1.

The package accepts only a close-review projection that is already eligible for period-close review: `suitable_for_period_close_review` must be true, the exception count must be zero, and the unexplained difference must be exact `Decimal("0")`. Eligibility is evidence suitability only; it is not approval or close authority.

Every package binds:

- the complete deterministic close-review projection, including exact decimal-string values and immutable tenant/entity/book/bank-assignment/run/population scope;
- one non-empty reconciliation approval evidence reference;
- one canonical database-owned approval snapshot digest in `sha256:<64 lowercase hex>` form;
- a canonical UTC RFC 3339 second-precision knowledge cutoff;
- immutable source-evidence references with canonical SHA-256 digests, including at least one statement artifact and one book population; and
- an operator-facing next action that explicitly requires the separately authorized reconciliation/period-close decision.

Evidence references are unique by `(evidence_kind_code, evidence_reference)` and sorted before serialization. The package serializes a stable UTF-8 JSON mapping with sorted object keys and exact decimal strings. `package_sha256` is SHA-256 over the canonical unsigned manifest, prefixed with `sha256:`. Changing the approval snapshot or any bound source digest therefore changes the package digest.

The package is intentionally a manifest rather than a signature. Authenticity/non-repudiation remains a deployment and release evidence concern; this slice does not claim that a bare SHA-256 digest authenticates an actor or proves possession of a private key.

## Consequences

Controllers and acquirers gain a deterministic reconciliation close artifact whose source and approval bindings can be rehashed and compared without reconstructing mutable application state. Evidence ordering cannot change the package digest, and exact money does not round-trip through binary floating point.

The package is fail-closed if close-review eligibility is false, required source evidence is absent, an evidence identity is duplicated, a digest is non-canonical, or the knowledge cutoff is malformed. No package field can invoke journal posting, reconciliation approval, or fiscal-period close.

A later persistence/API slice may store or transmit this manifest only through an authority-preserving boundary. If digital signatures or external attestations are added, they must wrap the exact package bytes/digest rather than redefine the accounting evidence inside the package.

## References

National Institute of Standards and Technology. (2015). *Secure Hash Standard (SHS)* (FIPS PUB 180-4). https://doi.org/10.6028/NIST.FIPS.180-4

National Institute of Standards and Technology. (2023, March 7). *Decision to revise FIPS 180-4, Secure Hash Standard (SHS)*. https://csrc.nist.gov/news/2023/decision-to-revise-fips-180-4

World Wide Web Consortium. (2013). *PROV-O: The PROV ontology*. https://www.w3.org/TR/prov-o/
