# ADR 0059: Accounting bounded-context map and architectural fitness

- Status: Proposed
- Date: 2026-09-01
- Scope: accounting-information-platform modular monolith and Context Fabric contract boundary

## Context

The repository grew from a compact accounting foundation into posting, reversal, period control, reporting, tax evidence and bank reconciliation. Its physical Python package remains deliberately flat in several places (`accept.py`, `core.py`, `persistence.py`, `http_api.py`) while accounting responsibilities have become materially different. Folder names alone therefore no longer communicate data authority or dependency direction.

That ambiguity is risky for an accounting system of record. In particular, a commercial source proposal must not become an authoritative journal by crossing an application-layer shortcut; reconciliation approval must not become posting authority; provider DTOs must not become accounting domain entities; persistence/HTTP adapters must not own accounting invariants; and a generic `core` or `shared` bucket must not silently become a Shared Kernel.

The Context Fabric introduces one deliberately small cross-repository contract boundary. `ContextualWisdomLab/context-graph-contracts` owns the provider-neutral canonical object/authority references, truth status/origin, valid/system time, provenance, Context Assertion, CloudEvents/schema/conformance/admission grammar. `ContextualWisdomLab/enterprise-architecture-core` is the authoritative EA Decision Plane. Accounting remains authoritative for journal, ledger, period, reconciliation and financial-control facts and must not copy that authority into either Context Fabric repository.

The repository also has to evolve without a cosmetic bulk reorganization that breaks current migrations, API/event contracts, package imports, exact-head tests or active dependency-root work.

## Decision

Maintain the accounting-information-platform as a modular monolith with the following explicit bounded contexts until a later ADR proves a stable service split:

1. `proposal_intake`
2. `policy_resolution`
3. `journal_posting`
4. `journal_reversal`
5. `close_control`
6. `trial_balance`
7. `reporting_projection`
8. `integration_outbox`
9. `tax_interface`
10. `bank_statement_registry`
11. `reconciliation_run_control`
12. `reconciliation_review`

`docs/CONTEXT_MAP.md` is the code-current relationship and physical-ownership map. `docs/UBIQUITOUS_LANGUAGE.md` is the vocabulary contract for authority-sensitive accounting terms.

### Subdomain classification

- **Core**: authoritative accounting record — policy/master-data resolution, posting, reversal/correction, period control, trial balance and authoritative receipts.
- **Supporting**: bank reconciliation/external accounting evidence and tax evidence interfaces.
- **Generic**: integration publication/technical transport mechanics such as transactional outbox delivery evidence and deployment/persistence transport concerns that do not make accounting decisions.

Classification does not change authority. Supporting and Generic contexts cannot bypass Core invariants.

### Context relationships

Operational/commercial systems, including metering-billing-platform, are upstream evidence suppliers. They communicate through published proposal/API/event contracts. `proposal_intake` acts as the Anti-Corruption Layer (ACL): upstream invoice/payment/provider concepts do not become accounting domain entities and upstream systems never select final chart-account IDs or write accounting tables.

`policy_resolution` resolves Accounting-owned policy and effective-dated chart mappings. `journal_posting` and `journal_reversal` are the only contexts that create authoritative journal facts. `close_control` owns period-state authority. `trial_balance` and `reporting_projection` are downstream projections and cannot mutate ledger truth. `integration_outbox` publishes facts atomically produced by the owning accounting transaction and cannot create accounting facts itself.

Bank/provider models terminate at `bank_statement_registry` ACLs. `reconciliation_run_control` owns immutable run scope and lifecycle evidence; `reconciliation_review` owns deterministic matching, exact allocation conservation, approval/exception evidence, book-to-bank bridge evidence and close-review packaging. A reconciliation decision is evidence only and never authorizes automatic journal posting or period close.

`tax_interface` consumes authoritative accounting evidence through implemented contracts without claiming external tax-system authority or statutory certification.

### Context Fabric Shared Kernel and EA Decision Plane

`ContextualWisdomLab/context-graph-contracts` is the **minimal cross-repository Shared Kernel** for Context Fabric interoperability. Accounting may consume only a released `cwl-context-contracts` distribution and its versioned Context Assertion, CloudEvents, schema/profile, conformance and admission contracts. An open PR head, mutable branch, copied schema, generated model-only result or predecessor conformance artifact is not a dependency release.

The Shared Kernel carries canonical reference grammar, authority/truth status and origin, valid/system time, provenance and event/schema evidence. It never owns or contains accounting journal/ledger balances, posting authority, reconciliation approval authority, accounting policy or close authority. Accounting domain facts remain in accounting-information-platform and are projected outward only as the minimum architecture/change evidence required by a released contract.

`ContextualWisdomLab/enterprise-architecture-core` consumes those released contract assertions/events as the authoritative EA Decision Plane. Accounting sends architecture/change evidence only: application/service/runtime/database/integration ownership and lifecycle changes, contract/profile versions, technology/provider versions where operationally material, risk/remediation references and source provenance. EA Core must not become a replica of accounting journal/ledger balances or other financial facts.

There is no direct source dependency on either foreign application repository. The only permitted cross-repository Python dependency is the released contract-only `cwl-context-contracts` package when integration code is added. Direct `context_graph_contracts`, `enterprise_architecture_core` or other product implementation imports and cross-service SQL remain forbidden. Contract version drift or missing conformance/admission evidence fails closed at the integration boundary rather than being silently coerced.

The accounting Python package root remains a deployment container, not a DDD Shared Kernel. Existing `core.py` likewise remains transitional debt and does not acquire Shared Kernel status merely because Context Fabric has a separately owned contract-only Shared Kernel.

### Physical-path migration rule

Existing mixed modules are explicit transitional debt, not preferred architecture. No bulk rename is authorized by this ADR.

When a material slice next touches a mixed module, first prove its bounded-context owner and map imports, consumers, schemas, migrations, tests, package metadata, workflows and compatibility. Then move or split the smallest coherent behavior together with all required references. Compatibility aliases are allowed only through an explicit deprecation window with a real consumer need.

New unrelated domain behavior must not be added to generic buckets named `utils`, `helpers`, `common`, `services`, `lib`, `shared`, `core`, `models`, `misc` or `legacy`. Existing `core.py` is debt and not precedent.

### Dependency direction

- Domain rules do not depend on HTTP/framework/provider/ORM DTO implementations.
- Persistence and transport adapters may depend on domain/application contracts; domain code does not depend on persistence/transport implementations.
- Direct SQL against another service's application tables is forbidden; all cross-service SQL is forbidden.
- Foreign service/provider models remain behind ACLs and published contracts.
- Released `cwl-context-contracts` types may be used only at the Context Fabric interoperability boundary and may not become accounting aggregate/entity implementations.
- Context Assertion/CloudEvents publication must preserve accounting authority, truth status, valid/system time and provenance and must not promote proposal/inferred evidence to authoritative accounting truth.
- EA projection carries architecture/change evidence only and excludes journal/ledger balances and other financial facts.
- New production modules must have one explicit primary bounded-context owner.
- Cross-context orchestration must be explicit and must not create a second source of accounting truth.

These rules are ratcheted where currently machine-checkable by `tests/test_ddd_architecture_fitness.py`.

## Consequences

The immediate benefit is an explicit authority model without destabilizing current accounting/reconciliation work. Reviewers can now distinguish architectural debt from intentional boundaries, and new modules cannot silently add generic ownership or direct foreign-application coupling.

The Context Fabric boundary also becomes explicit before code consumes it. Accounting can later emit provider-neutral architecture/change assertions without inventing a second schema or coupling directly to EA Core, while Context Graph Contracts remains contract-only and EA Core remains authoritative only for architecture decisions.

The cost is that the repository temporarily has an intentional mismatch between logical bounded contexts and several flat physical files, and no immutable `cwl-context-contracts` release is yet assumed by this ADR. Until an exact released contract with conformance/admission evidence exists, the runtime integration remains fail-closed/not implemented rather than pinning an open PR or copying provisional schemas.

The fitness test is a ratchet, not proof that DDD separation or Context Fabric integration is complete. It deliberately records existing exceptions and required future contract evidence instead of declaring false conformance.

## Rejected alternatives

**Bulk physical reorganization now.** Rejected because #29 remains the dependency-root accounting/reconciliation repair and broad moves would create unnecessary import, package, migration and review churn while obscuring causal repairs.

**Keep architecture only in `ARCHITECTURE.md`.** Rejected because conceptual module lists were insufficient to identify Context Map relationships, authority, ACLs, Shared Kernel policy and physical drift precisely enough for machine checks.

**Split into services immediately.** Rejected because current transaction/deployment cohesion and unresolved foundation work do not justify distributed transaction and operational complexity. Service extraction requires stable responsibility and reuse boundaries proven by a later ADR.

**Create an accounting-owned cross-repository domain package.** Rejected because it would couple independent product authorities and blur accounting ownership. The Context Fabric Shared Kernel is deliberately restricted to the separately owned provider-neutral `context-graph-contracts` contract grammar; accounting domain objects remain local.

**Pin Context Fabric to an open PR/branch or copy provisional schemas.** Rejected because mutable predecessor evidence cannot support an authoritative integration boundary. Accounting waits for a released `cwl-context-contracts` package with applicable conformance/admission evidence.

**Write financial facts directly into EA Core.** Rejected because EA Core is the architecture Decision Plane, not a financial ledger or reconciliation store. Architecture/change assertions reference the accounting system and its contracts without duplicating journal/ledger balances.

## Verification

Before this decision can reach a protected branch, the exact unchanged head must pass the repository's applicable unit/integration, PostgreSQL, exact 100% owned production statement/branch coverage, documentation/repository contract, SAST/security, package/SBOM/provenance and review gates. `tests/test_ddd_architecture_fitness.py` must pass on the same exact head.

When Context Fabric runtime integration is implemented, acceptance must additionally pin an immutable released `cwl-context-contracts` version, execute the released conformance/admission boundary, reject schema/profile/version drift, preserve authority/truth/time/provenance fields, prove no cross-service SQL or foreign implementation import, and verify that emitted EA evidence excludes journal/ledger balances and other financial facts.

No passing predecessor result, queued/skipped workflow or model-only review is evidence for this ADR.

## References

Evans, E. (2003). *Domain-driven design: Tackling complexity in the heart of software*. Addison-Wesley Professional.

ISO/IEC/IEEE. (2022). *ISO/IEC/IEEE 42010:2022 Software, systems and enterprise—Architecture description* (2nd ed.). International Organization for Standardization. https://www.iso.org/standard/74393.html
