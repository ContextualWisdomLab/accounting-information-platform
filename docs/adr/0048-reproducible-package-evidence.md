# ADR 0048: Reproducible package evidence and signed attestations

**Status:** Accepted

## Context

A passing test suite is not sufficient acquisition or release evidence if the built wheel cannot be tied back to one exact source revision, reproduced byte-for-byte, accompanied by a machine-readable software bill of materials (SBOM), and verified with independently signed build provenance. The foundation already builds and smoke-tests a wheel, but a wheel hash alone does not describe how the artifact was produced or whether an SBOM was bound to that same artifact.

SLSA 1.2 defines provenance as verifiable information describing where, when, and how a software artifact was produced and requires artifact identity by cryptographic digest for the build track. GitHub artifact attestations bind workflow artifacts to SLSA provenance using an OIDC-backed Sigstore signature. SPDX is the project SBOM interchange format; this repository emits SPDX 2.3 JSON because it is accepted by GitHub's SBOM attestation mode and is stable for the current dependency-free Python package.

## Decision

The exact-head Accounting Foundation CI is the package-evidence builder for this milestone.

1. It checks out and verifies the exact PR head or protected-branch push SHA before build work.
2. It derives `SOURCE_DATE_EPOCH` from that commit's timestamp and builds the wheel twice from clean build metadata. The two SHA-256 digests must be identical. A non-reproducible wheel fails the job.
3. `scripts/generate_supply_chain_evidence.py` emits `SHA256SUMS` plus deterministic `sbom.spdx.json` for that exact wheel and source SHA. The generator fails closed if runtime dependencies are introduced before explicit dependency relationships are represented in the SBOM; an incomplete dependency inventory is not accepted as a green SBOM.
4. The workflow verifies `SHA256SUMS`, then uses the full-SHA-pinned `actions/attest` action twice: once for SLSA build provenance and once to bind the SPDX SBOM to the same wheel subject.
5. The wheel, checksum file, and SPDX document are retained together as one same-head Actions artifact for review evidence. Attestation records are stored through GitHub's artifact-attestation service.
6. Job token permissions are scoped to `contents: read` plus the OIDC/attestation permissions required by GitHub. The build does not use `COPILOT_GITHUB_TOKEN`, release credentials, or reviewer credentials.

This evidence gate does **not** claim a SLSA level, SOC 2 certification, CSAP certification, or release certification. Those claims require the corresponding governance and operational evidence beyond one CI job. Release tags remain prohibited until the complete integrated-head release gate passes.

## Consequences

A wheel whose build output changes between identical source/timestamp inputs cannot become passing package evidence. A future runtime dependency intentionally breaks the current SBOM generator until the dependency graph is represented rather than silently shipping a partial SBOM. Reviewers and acquirers can compare the wheel digest, SPDX document, exact source SHA, workflow run, and signed attestations without relying on predecessor-head status.

The CI runner needs GitHub artifact-attestation support and the documented `id-token`, `attestations`, and `artifact-metadata` permissions. Infrastructure failure in that service is non-passing evidence; it must not be relabeled as product success.

## References

GitHub. (2026). *Artifact attestations*. https://docs.github.com/en/actions/concepts/security/artifact-attestations

SPDX Workgroup. (n.d.). *SPDX specifications*. https://spdx.dev/use/specifications/

Supply-chain Levels for Software Artifacts. (2026). *SLSA specification, version 1.2*. https://slsa.dev/spec/v1.2/
