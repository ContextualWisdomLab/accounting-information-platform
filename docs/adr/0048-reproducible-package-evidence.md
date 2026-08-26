# ADR 0048: Reproducible package evidence and signed attestations

**Status:** Accepted

## Context

A passing test suite is not sufficient acquisition or release evidence if the built wheel cannot be tied back to one exact source revision, reproduced byte-for-byte, accompanied by a machine-readable software bill of materials (SBOM), and verified at the integrated release boundary. A wheel hash alone does not describe which source revision produced the artifact or whether the SBOM refers to that same artifact.

SLSA 1.2 treats provenance as information describing where, when, and how an artifact was produced and binds artifact identity by cryptographic digest. GitHub artifact attestations use OIDC-backed signing context to create verifiable provenance and SBOM attestations. On a `pull_request` workflow, however, GitHub's workflow identity can refer to the synthetic pull-request merge ref/commit even when the repository workflow explicitly checks out and builds the immutable PR head. Under this repository's exact-head acceptance rule, such a signed statement is not accepted as proof that the attested build identity is the PR head.

GitHub also grants `GITHUB_TOKEN` permissions at workflow or job scope to every action and command in that job, and `id-token: write` permits that job to request an OIDC token. Therefore merely putting `if: github.event_name == 'push'` on attestation **steps** is not a least-privilege boundary when the same job executes pull-request-controlled tests and build code. Signing authority belongs in a distinct push-only job that cannot execute on a pull-request event.

Dependency-review evidence has a similar execution-boundary problem. A green aggregate organization security workflow is not evidence that its dependency-review control ran when a support/permission probe fails and the actual review step is skipped. OSV-Scanner supports direct scanning of Python `requirements.txt` lockfiles and returns a failing status for vulnerability findings or scanner errors. The repository therefore needs an exact-head, live-base-bound equivalent hard gate that does not convert unavailable or skipped dependency evidence into success.

SPDX is the project SBOM interchange format; this repository emits deterministic SPDX 2.3 JSON for the current dependency-free Python package.

## Decision

The Accounting Foundation CI separates **premerge exact-source evidence** from **integrated protected-head signed attestations**.

1. Every pull-request and `develop`/`main` push build explicitly checks out the expected source SHA and asserts `git rev-parse HEAD == EXPECTED_SHA` before build work.
2. The build/test job has `contents: read` only. It does not receive OIDC, attestation, or artifact-metadata write authority while executing repository-controlled tests, validation, package build code, or smoke tests.
3. The job derives `SOURCE_DATE_EPOCH` from that verified commit timestamp and builds the wheel twice from clean build metadata. The two SHA-256 digests must be identical. A non-reproducible wheel fails the job.
4. `scripts/generate_supply_chain_evidence.py` emits deterministic `sbom.spdx.json` and `source-provenance.json`. The source-provenance manifest binds the exact verified source SHA and source timestamp to the wheel file/digest, SPDX SBOM file/digest, repository, and build definition. The generator fails closed if runtime dependencies are introduced before explicit dependency relationships are represented in the SBOM.
5. `SHA256SUMS` covers the wheel, the SPDX SBOM, and `source-provenance.json`. The workflow verifies those checksums, then installs the rebuilt wheel with `--require-hashes` from a requirements line that carries the measured `--hash=sha256:` digest (pip does not accept `--hash` as a CLI option) before the isolated smoke test. All four files are retained together in the same-head Actions artifact.
6. On `pull_request`, `exact-head-dependency-diff` checks out `pull_request.head.sha`, independently fetches the live base branch tip, records the live base/head identities, dependency-manifest diff and manifest SHA-256 values, and fails if the live base is not an ancestor of the exact head. It scans the complete hash-locked `requirements-quality.txt` with a digest-pinned OSV-Scanner container. A vulnerability finding, scanner failure, unavailable scanner/evidence path, wrong head identity or missing expected evidence is non-passing. Because the bootstrap base has no dependency manifests, the foundation PR records those files as additions and requires the complete exact-head dependency set to be vulnerability-free rather than treating an empty base scan as evidence. The SHA-named artifact retains the identity/diff record and OSV JSON result.
7. On `pull_request`, the deterministic source-provenance manifest is the authoritative artifact-to-exact-PR-head evidence. No job with `id-token: write`, `attestations: write`, or `artifact-metadata: write` executes for that event.
8. On `push` to `develop` or `main`, a distinct `integrated-attestations` job runs only after `accounting-foundation` succeeds. It downloads the immutable SHA-named package-evidence artifact, re-verifies `SHA256SUMS`, verifies `source-provenance.json.source_sha == github.sha`, and only then receives the OIDC/attestation permissions required by the full-SHA-pinned `actions/attest` action to create build-provenance and SPDX-SBOM attestations. Those integrated-head attestations are mandatory release evidence.
9. The workflow does not use `COPILOT_GITHUB_TOKEN`, release credentials, or reviewer credentials. Attestation permissions are not evidence by themselves; only a successful applicable protected-head attestation is passing release evidence.

This decision does **not** claim a SLSA level, SOC 2 certification, CSAP certification, or release certification. The deterministic premerge manifest is deliberately described as source provenance evidence, not as an independently signed SLSA attestation. Release tags remain prohibited until the complete integrated-head release gate, including the protected-head signed attestations, passes.

## Consequences

A wheel whose bytes change between identical source/timestamp inputs cannot become passing package evidence. A future runtime dependency intentionally breaks the current SBOM generator until the dependency graph is represented rather than silently shipping a partial SBOM. Reviewers and acquirers can verify the PR artifact against its exact source SHA, wheel digest, SBOM digest, checksum bundle, live-base dependency-diff evidence and workflow run without accepting a synthetic merge identity as the PR head.

A repository-owned dependency gate is intentionally stricter than accepting a skipped organization control: the current exact-head dependency set must actually be scanned and vulnerability-free. Organization dependency review remains supplemental when it executes. A forbidden/unsupported probe, skipped review step, stale base, wrong checkout SHA or unavailable scanner remains non-passing even when some aggregate workflow reports success.

Pull-request-controlled repository code executes without signing authority. After integration, the protected branch rebuilds from its own exact commit and the separate push-only job signs the already-validated artifact for that same commit. A predecessor PR artifact or predecessor signed attestation is not transferred to the integrated branch. GitHub attestation-service failure on an applicable protected-head push is non-passing release evidence; it must not be relabeled as product success.

## References

GitHub. (2026). *Artifact attestations*. https://docs.github.com/en/actions/concepts/security/artifact-attestations

GitHub. (2026). *OpenID Connect reference*. https://docs.github.com/en/actions/reference/security/oidc

GitHub. (2026). *Workflow syntax for GitHub Actions*. https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax

Google Open Source Security Team. (2026a). *OSV-Scanner: Project source scanning*. https://google.github.io/osv-scanner/usage/scan-source/

Google Open Source Security Team. (2026b). *OSV-Scanner: Supported artifacts and manifests*. https://google.github.io/osv-scanner/supported-languages-and-lockfiles/

SPDX Workgroup. (n.d.). *SPDX specifications*. https://spdx.dev/use/specifications/

Supply-chain Levels for Software Artifacts. (2026). *SLSA specification, version 1.2*. https://slsa.dev/spec/v1.2/