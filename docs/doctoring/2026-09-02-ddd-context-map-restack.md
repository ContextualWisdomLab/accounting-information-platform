# DDD context-map restack doctoring — 2026-09-02

## Decision under repair

PR #41 makes bounded-context ownership machine-checkable for the accounting modular monolith. During the reconciliation authority work, dependency-root PR #29 advanced from the parent on which #41 had originally been written and introduced a new top-level production module, `src/accounting_information_platform/reconciliation_completion.py`. The architecture fitness test deliberately enumerates top-level production modules and requires every module to have an explicit physical owner in `docs/CONTEXT_MAP.md`; therefore retaining the old #41 parent would have made the architecture description stale even though its four original files remained internally consistent.

The branch was restacked non-destructively on exact #29 head `843f0e3bbe10f3bb989292b5bdf9eeee35b0316d` with a two-parent merge commit. The existing #41 history was retained, the current dependency-root tree was incorporated, and no force push or destructive rebase was used. The Context Map now assigns `reconciliation_completion.py` to `reconciliation_run_control` and states that the evidence-derived completion command must not acquire journal-posting or period-close authority.

This repair changes architecture description and fitness evidence only. It does not grant accounting runtime authority, alter a database migration, change a reconciliation decision, post/reverse a journal, close a fiscal period, or consume mutable foreign-repository implementation code.

## Decision-status consistency repair

A second evidence defect remained after ADR 0059 itself was restored to `Status: Proposed`: `docs/CONTEXT_MAP.md` still began with `Status: accepted architecture`. That presentation could cause a reviewer or a later Agent to treat an unintegrated Draft decision as accepted despite the parent dependency still being under repair and the exact-head verification gate being incomplete. The contradiction is governance-significant because the Context Map is the code-current ownership authority used by the architecture fitness test.

The repair was test-first. Commit `b3123838185bfc2e0f8084499ff229dfdbf0f8d5` added a regression requiring ADR 0059 to remain `Status: Proposed`, the Context Map to say `Status: proposed architecture`, and the Context Map not to claim `Status: accepted architecture`. On the predecessor tree that contract is RED because the stale accepted claim is still present. Successor commit `c126219308f44f873285c02cb2dda26530f3215a` changes only the Context Map status claim to `proposed architecture`; it does not change bounded contexts, data authority, Shared Kernel scope, or runtime behavior.

Concrete failure scene: an implementation Agent preparing a new accounting module reads the Context Map before the ADR and interprets `accepted architecture` as protected-branch evidence. It then treats a proposed cross-repository boundary as settled and builds against it before released conformance evidence exists. Keeping both the ADR and its Context Map explicitly Proposed prevents that evidence escalation. Promotion to Accepted requires the same exact protected integration evidence described by ADR 0059; a queued workflow, predecessor check, Draft PR, or source inspection is insufficient.

## Primary-owner fitness repair

CodeRabbit review of exact head `342f98408f201599f8ac8506edae1b9d8a3120a6` found that the physical-ownership table could list several bounded contexts in one owner cell while the fitness test checked only that a path string appeared somewhere in the Context Map. The check therefore proved presence, not single-writer ownership: `accept.py`, `core.py`, `persistence.py`, `http_api.py`, and `reconciliation_read_model.py` could retain or gain multiple nominal owners without failing the architecture gate.

The repair is test-first. Commit `f7d63332fd1c002dc5a12f3168020a6ad7f5e69f` changes the fitness contract to require a parseable `Primary owner` column, exactly one ownership row per production path, and exactly one primary-owner token per row. Domain/application paths must name one of the declared bounded contexts. The sole existing technical exception is pinned to the exact path `src/accounting_information_platform/migration_install.py` with technical owner `deployment_infrastructure`; it is not a reusable generic exemption and cannot own an accounting decision. On the predecessor Context Map this contract is deterministically RED because the table exposes only `Current owner(s)` and several rows contain multiple owners.

Successor commit `9ebe709d1ea5e650af3e30920354212eb4d67a9a` makes ownership accountability explicit without pretending the flat modules are already separated. Each physical path now has one primary owner. Co-located behavior that still serves another context is recorded only under `Transitional responsibilities`, so it cannot silently become a second source of journal, period, reconciliation, policy, reporting, or integration authority. For mixed transport/persistence modules, the primary owner is accountability for the next split, not permission to make decisions owned by the transitional contexts.

This changes architecture description and its executable fitness contract only. It does not move code, change imports, alter database/API/event behavior, grant `deployment_infrastructure` domain authority, or convert ADR 0059 from Proposed to Accepted.

## Current-root ownership and infrastructure-import repair

After the ordinary restack reached exact `346df07043cb9bd25595b59eacbc7a5b0cba12e7`, the hosted architecture fitness suite exposed exactly two remaining code-to-map failures. First, the current dependency root contains `src/accounting_information_platform/reconciliation_lifecycle.py` but the physical-ownership table did not name a most-specific owner. Second, the deny-by-default absolute-import fitness rule treated the already-used PostgreSQL driver `psycopg` as though it were an undeclared foreign application implementation.

These findings have different meanings and are repaired separately. `reconciliation_lifecycle.py` is assigned exactly once to `reconciliation_run_control` because it coordinates run lifecycle admission, session/advisory locking and the fresh `REPEATABLE READ` authority transaction. That assignment does not transfer reconciliation-review, journal-posting or period-close decisions into run control. The architecture import gate now admits only the existing `psycopg` root as PostgreSQL infrastructure. It remains forbidden for driver objects to become domain entities or to own accounting invariants, and every other non-stdlib absolute import remains fail-closed until separately reviewed and documented.

The repair lineage is ordinary and non-destructive: `4f86b1b38340cfc461f0b1dca208923d5308d247` narrows the executable import exception and adds a ratchet that both Context Map and ADR 0059 must describe `psycopg` as PostgreSQL infrastructure; `3c32b4d7c6a00acb585421523a2e447e7f171ec3` adds the missing lifecycle physical owner and documents the infrastructure boundary in the Context Map; `61b1ccbcaa3268a38f6b3aa2b9b1147e69d18136` aligns ADR 0059 with the same ownership and dependency decision. No runtime source, migration, API/event contract, shared changelog, standards traceability or product-gap baseline byte changes in this repair.

The predecessor RED is not transferred. The successor architecture head must run the focused fitness tests and the repository-owned Foundation/coverage/contracts/security/package gates on the unchanged exact SHA before this section can be treated as GREEN evidence.

## Evidence boundary

The relevant architecture-description authority was rechecked against the publisher on 2026-09-02. ISO lists **ISO/IEC/IEEE 42010:2022, Software, systems and enterprise — Architecture description, Edition 2** as the currently published International Standard; the 2011 edition is withdrawn and replaced by the 2022 edition. The standard specifies requirements for architecture descriptions and their concepts/relationships, viewpoints, frameworks and languages; it does not prescribe Domain-Driven Design, a directory structure, a microservice split, or a specific implementation method. Accordingly, ADR 0059 and the Context Map use it only as architecture-description guidance and make no standards-conformance claim.

DDD remains the modeling method used to express responsibility and authority boundaries. In this repository, a bounded-context identifier is not inferred from a Python package name. The Context Map, Ubiquitous Language and machine-checkable fitness test jointly establish the code-current ownership claim, while provider or sibling-product concepts terminate at Published Language / Anti-Corruption Layer boundaries. The local `core.py` remains transitional debt and is explicitly not a Shared Kernel.

## Regression contract

The architecture slice remains GREEN only when all of the following are true on one unchanged exact head:

1. every listed production path has exactly one physical-ownership row and exactly one primary owner in `docs/CONTEXT_MAP.md`; domain/application paths name one declared bounded context, while any technical exception is exact-path scoped and cannot own accounting decisions;
2. no new generic domain bucket hides responsibility behind names such as `utils`, `helpers`, `common`, `services`, `shared` or `core`;
3. accounting domain/application source does not import foreign ContextualWisdomLab application implementations; the sole currently admitted non-stdlib root is `psycopg`, explicitly classified as PostgreSQL infrastructure rather than domain or Shared Kernel code;
4. the only declared Context Fabric Shared Kernel is a later immutable released provider-neutral contract grammar, never mutable open-PR bytes;
5. journal/ledger balances, reconciliation monetary populations, policy, posting authority and close authority remain Accounting-owned and are not promoted into the EA Decision Plane;
6. an upstream restack that adds, removes or materially reassigns a production module must update the Context Map and rerun the architecture fitness gate before merge;
7. ADR 0059 and the Context Map must not claim Accepted architecture before the exact protected integration evidence exists.

Queued, skipped, stale, predecessor or model-only workflow results are not GREEN evidence.

## References

Evans, E. (2003). *Domain-driven design: Tackling complexity in the heart of software*. Addison-Wesley Professional.

International Organization for Standardization, International Electrotechnical Commission, & Institute of Electrical and Electronics Engineers. (2022). *ISO/IEC/IEEE 42010:2022 Software, systems and enterprise—Architecture description* (2nd ed.). International Organization for Standardization. https://www.iso.org/standard/74393.html