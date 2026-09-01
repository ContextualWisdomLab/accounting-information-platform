# ADR 0059: Accounting bounded-context map and architectural fitness

- Status: Accepted
- Date: 2026-09-01
- Scope: accounting-information-platform modular monolith

## Context

The repository grew from a compact accounting foundation into posting, reversal, period control, reporting, tax evidence and bank reconciliation. Its physical Python package remains deliberately flat in several places (`accept.py`, `core.py`, `persistence.py`, `http_api.py`) while accounting responsibilities have become materially different. Folder names alone therefore no longer communicate data authority or dependency direction.

That ambiguity is risky for an accounting system of record. In particular, a commercial source proposal must not become an authoritative journal by crossing an application-layer shortcut; reconciliation approval must not become posting authority; provider DTOs must not become accounting domain entities; persistence/HTTP adapters must not own accounting invariants; and a generic `core` or `shared` bucket must not silently become a Shared Kernel.

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

### Shared Kernel

No Shared Kernel is declared between ContextualWisdomLab repositories. The Python package root is a deployment container, not a Shared Kernel. Cross-repository reuse requires a separately owned, versioned published package/schema/API/event contract. A future Shared Kernel requires an ADR naming joint owners, compatibility rules, release policy and an exit strategy.

### Physical-path migration rule

Existing mixed modules are explicit transitional debt, not preferred architecture. No bulk rename is authorized by this ADR.

When a material slice next touches a mixed module, first prove its bounded-context owner and map imports, consumers, schemas, migrations, tests, package metadata, workflows and compatibility. Then move or split the smallest coherent behavior together with all required references. Compatibility aliases are allowed only through an explicit deprecation window with a real consumer need.

New unrelated domain behavior must not be added to generic buckets named `utils`, `helpers`, `common`, `services`, `lib`, `shared`, `core`, `models`, `misc` or `legacy`. Existing `core.py` is debt and not precedent.

### Dependency direction

- Domain rules do not depend on HTTP/framework/provider/ORM DTO implementations.
- Persistence and transport adapters may depend on domain/application contracts; domain code does not depend on persistence/transport implementations.
- Direct SQL against another service's application tables is forbidden.
- Foreign service/provider models remain behind ACLs and published contracts.
- New production modules must have one explicit primary bounded-context owner.
- Cross-context orchestration must be explicit and must not create a second source of accounting truth.

These rules are ratcheted where currently machine-checkable by `tests/test_ddd_architecture_fitness.py`.

## Consequences

The immediate benefit is an explicit authority model without destabilizing current accounting/reconciliation work. Reviewers can now distinguish architectural debt from intentional boundaries, and new modules cannot silently add generic ownership or direct foreign-application coupling.

The cost is that the repository temporarily has an intentional mismatch between logical bounded contexts and several flat physical files. This is accepted only as migration debt. The Context Map must stay current, and touched coherent slices should reduce rather than increase that debt.

The fitness test is a ratchet, not proof that DDD separation is complete. It deliberately records existing exceptions instead of declaring false conformance.

## Rejected alternatives

**Bulk physical reorganization now.** Rejected because #29 remains the dependency-root accounting/reconciliation repair and broad moves would create unnecessary import, package, migration and review churn while obscuring causal repairs.

**Keep architecture only in `ARCHITECTURE.md`.** Rejected because conceptual module lists were insufficient to identify Context Map relationships, authority, ACLs, Shared Kernel policy and physical drift precisely enough for machine checks.

**Split into services immediately.** Rejected because current transaction/deployment cohesion and unresolved foundation work do not justify distributed transaction and operational complexity. Service extraction requires stable responsibility and reuse boundaries proven by a later ADR.

**Create a cross-repository shared domain package now.** Rejected because it would couple independent owners and blur accounting authority. Published contracts are the integration boundary.

## Verification

Before this decision can reach a protected branch, the exact unchanged head must pass the repository's applicable unit/integration, PostgreSQL, exact 100% owned production statement/branch coverage, documentation/repository contract, SAST/security, package/SBOM/provenance and review gates. `tests/test_ddd_architecture_fitness.py` must pass on the same exact head.

No passing predecessor result, queued/skipped workflow or model-only review is evidence for this ADR.

## References

Evans, E. (2003). *Domain-driven design: Tackling complexity in the heart of software*. Addison-Wesley Professional.

ISO/IEC/IEEE. (2022). *ISO/IEC/IEEE 42010:2022 Software, systems and enterprise—Architecture description* (2nd ed.). International Organization for Standardization. https://www.iso.org/standard/74393.html
