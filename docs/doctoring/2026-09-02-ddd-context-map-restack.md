# DDD context-map restack doctoring — 2026-09-02

## Decision under repair

PR #41 makes bounded-context ownership machine-checkable for the accounting modular monolith. During the reconciliation authority work, dependency-root PR #29 advanced from the parent on which #41 had originally been written and introduced a new top-level production module, `src/accounting_information_platform/reconciliation_completion.py`. The architecture fitness test deliberately enumerates top-level production modules and requires every module to have an explicit physical owner in `docs/CONTEXT_MAP.md`; therefore retaining the old #41 parent would have made the architecture description stale even though its four original files remained internally consistent.

The branch was restacked non-destructively on exact #29 head `843f0e3bbe10f3bb989292b5bdf9eeee35b0316d` with a two-parent merge commit. The existing #41 history was retained, the current dependency-root tree was incorporated, and no force push or destructive rebase was used. The Context Map now assigns `reconciliation_completion.py` to `reconciliation_run_control` and states that the evidence-derived completion command must not acquire journal-posting or period-close authority.

This repair changes architecture description and fitness evidence only. It does not grant accounting runtime authority, alter a database migration, change a reconciliation decision, post/reverse a journal, close a fiscal period, or consume mutable foreign-repository implementation code.

## Evidence boundary

The relevant architecture-description authority was rechecked against the publisher on 2026-09-02. ISO lists **ISO/IEC/IEEE 42010:2022, Software, systems and enterprise — Architecture description, Edition 2** as the currently published International Standard; the 2011 edition is withdrawn and replaced by the 2022 edition. The standard specifies requirements for architecture descriptions and their concepts/relationships, viewpoints, frameworks and languages; it does not prescribe Domain-Driven Design, a directory structure, a microservice split, or a specific implementation method. Accordingly, ADR 0059 and the Context Map use it only as architecture-description guidance and make no standards-conformance claim.

DDD remains the modeling method used to express responsibility and authority boundaries. In this repository, a bounded-context identifier is not inferred from a Python package name. The Context Map, Ubiquitous Language and machine-checkable fitness test jointly establish the code-current ownership claim, while provider or sibling-product concepts terminate at Published Language / Anti-Corruption Layer boundaries. The local `core.py` remains transitional debt and is explicitly not a Shared Kernel.

## Regression contract

The architecture slice remains GREEN only when all of the following are true on one unchanged exact head:

1. every top-level production module has an explicit current owner in `docs/CONTEXT_MAP.md`;
2. no new generic domain bucket hides responsibility behind names such as `utils`, `helpers`, `common`, `services`, `shared` or `core`;
3. accounting domain/application source does not import foreign ContextualWisdomLab application implementations;
4. the only declared Context Fabric Shared Kernel is a later immutable released provider-neutral contract grammar, never mutable open-PR bytes;
5. journal/ledger balances, reconciliation monetary populations, policy, posting authority and close authority remain Accounting-owned and are not promoted into the EA Decision Plane;
6. an upstream restack that adds, removes or materially reassigns a production module must update the Context Map and rerun the architecture fitness gate before merge.

Queued, skipped, stale, predecessor or model-only workflow results are not GREEN evidence.

## References

Evans, E. (2003). *Domain-driven design: Tackling complexity in the heart of software*. Addison-Wesley Professional.

International Organization for Standardization, International Electrotechnical Commission, & Institute of Electrical and Electronics Engineers. (2022). *ISO/IEC/IEEE 42010:2022 Software, systems and enterprise—Architecture description* (2nd ed.). International Organization for Standardization. https://www.iso.org/standard/74393.html
