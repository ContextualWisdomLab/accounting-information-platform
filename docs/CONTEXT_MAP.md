# Accounting Context Map

Status: proposed architecture; physical package migration is in progress and must not be mistaken for shipped package separation.

Decision record: [ADR 0059 — Accounting bounded-context map and architectural fitness](adr/0059-accounting-bounded-context-map.md).

This document makes accounting responsibility, context relationships, Context Fabric integration and current physical ownership explicit. It is an architecture description for the modular monolith; it does not authorize another service to write accounting tables or make a statutory accounting decision.

## Subdomains

| Subdomain | Classification | Product responsibility |
|---|---|---|
| Authoritative accounting record | Core | Legal entity/book scope, policy resolution, journal posting, reversal/correction, fiscal-period control, trial balance and authoritative accounting receipts |
| Bank reconciliation and external accounting evidence | Supporting | Immutable bank evidence, deterministic reconciliation, reviewed approvals/exceptions, exact book-to-bank evidence and close-review packages |
| Tax evidence interface | Supporting | VAT/HomeTax evidence preparation and receipt history without claiming tax filing authority outside implemented contracts |
| Integration publication and technical transport | Generic | Transactional outbox delivery evidence, HTTP/serialization adapters and persistence mechanics that must not own accounting rules |

A classification describes product differentiation, not data authority. Supporting and Generic code may not bypass Core accounting invariants.

## Bounded Contexts

The identifiers below are stable architecture vocabulary. Customer-facing API copy may use ordinary accounting language instead of these internal names.

| Context | Owns | Does not own |
|---|---|---|
| `proposal_intake` | Published journal-proposal boundary, source-system authority declaration, immutable source payload identity, replay/conflict admission | Final chart-account choice, period override, journal posting truth |
| `policy_resolution` | Tenant/legal-entity/book scope, chart accounts, semantic account-role mapping, effective accounting policy | Commercial pricing/invoice semantics, provider settlement truth |
| `journal_posting` | Balanced immutable general journals, exact Decimal validation, authoritative posting receipt | Billing invoice/payment state, reconciliation approval |
| `journal_reversal` | Equal-and-opposite correction, reversal lineage and reversal-command replay/conflict semantics | Mutation or deletion of posted facts |
| `close_control` | Accounting-book fiscal-period state, soft/hard close authority, reopen/close evidence and purpose-bound closing authorization | Journal balance calculation as an alternate posting path |
| `trial_balance` | Deterministic debit/credit aggregation and trial-balance equality from authoritative posted facts | Independent source-of-truth ledger facts |
| `reporting_projection` | Read-only ledgers, balances, financial-statement/reporting projections and close-package reads | Posting or policy mutation |
| `integration_outbox` | Transactional event publication evidence and delivery acknowledgement | Accounting decisions independent of the aggregate transaction that produced the event |
| `tax_interface` | VAT and HomeTax-facing evidence contracts implemented in this repository | National Tax Service transport guarantees or tax-law certification claims |
| `bank_statement_registry` | Immutable bank statement/artifact/entry/balance evidence and bank-account assignment; ISO 20022 anti-corruption boundary | Posting journals from statement lines |
| `reconciliation_run_control` | Immutable reconciliation scope, command idempotency, knowledge/book cutoffs and run lifecycle evidence | Match approval, journal posting or period close |
| `reconciliation_review` | Deterministic candidate/match evidence, exact allocation conservation, human approval/exception evidence, exact book-to-bank bridge and tamper-evident close-review package | Automatic accounting posting, policy alteration or close authorization |

## Context relationships

```text
metering-billing-platform and other operational sources
        | Published Language: accounting_journal_proposal
        | Anti-Corruption Layer at proposal_intake
        v
proposal_intake --> policy_resolution --> journal_posting --> integration_outbox
                         |                    |
                         |                    +--> trial_balance --> reporting_projection
                         |                                      |
                         +--> close_control <--------------------+
                                  ^
                                  |
journal_reversal ----------------+

bank/provider evidence
        | versioned ISO 20022/provider ACL
        v
bank_statement_registry --> reconciliation_run_control --> reconciliation_review
             |                                               |
             +---------------- read-only evidence ------------+
                                                             |
        journal_posting / trial_balance ----------------------+
                                                             |
                                                             +--> close-review evidence only

tax_interface <--- authoritative journal/reporting evidence

accounting architecture/change evidence
        | released cwl-context-contracts
        | Context Assertion + CloudEvents + schema/conformance/admission
        v
ContextualWisdomLab/context-graph-contracts
        | provider-neutral minimal cross-repository Shared Kernel
        v
ContextualWisdomLab/enterprise-architecture-core
        | authoritative EA Decision Plane
        +--> architecture/change evidence only; never journal/ledger balances
```

Relationship rules:

- Operational/commercial systems are upstream suppliers of evidence. `proposal_intake` is the accounting Anti-Corruption Layer. Published proposal schemas are the integration language; external DTOs do not become accounting domain entities.
- `policy_resolution` is authoritative for final accounting roles and chart-account resolution. Upstream services submit semantic roles only.
- `journal_posting` and `journal_reversal` are the only contexts that create authoritative journal facts, and both remain subject to `close_control` policy and database-owned invariants.
- `trial_balance` and `reporting_projection` are downstream read models. They never become alternate write authorities.
- `bank_statement_registry` is an Anti-Corruption Layer over bank/provider formats. Canonical evidence is repository-owned; raw provider models stop at the adapter boundary.
- `reconciliation_review` consumes immutable statement and posted-book evidence. An approved reconciliation is evidence, not an accounting journal. Any adjustment returns through a separately authorized proposal/posting command.
- `integration_outbox` publishes facts produced atomically with their owning accounting transaction. It cannot manufacture a domain event after a failed accounting commit.
- `tax_interface` consumes authoritative accounting evidence but does not weaken journal, period, maker-checker or provenance controls.
- `ContextualWisdomLab/context-graph-contracts` is the minimal cross-repository Shared Kernel for provider-neutral Context Fabric grammar only. Accounting may consume only a released `cwl-context-contracts` package and its versioned Context Assertion, CloudEvents, schema/profile, conformance and admission contracts.
- Shared Kernel records may carry canonical object/authority references, truth status/origin, valid/system time and provenance. They do not own or replicate accounting journal/ledger balances, posting/reconciliation approval, close authority or accounting policy.
- `ContextualWisdomLab/enterprise-architecture-core` is the authoritative EA Decision Plane. Accounting publishes architecture/change evidence only: application/runtime/database/integration identity and lifecycle, released contract/profile/version, material technology/provider version, risk/remediation references and provenance. Financial facts stay in accounting-information-platform.
- An open Context Graph PR head, copied provisional schema or predecessor conformance artifact is not a runtime dependency. Until an immutable released `cwl-context-contracts` distribution exists with applicable conformance/admission evidence, the accounting integration is fail-closed/not implemented.
- Direct foreign application imports and all cross-service SQL are forbidden. A future adapter may import the released contract-only `cwl_context_contracts` package at the interoperability boundary; it may not import Context Graph or EA Core product implementations.
- The accounting Python package root is a deployment container, not a DDD Shared Kernel. Existing `core.py` is local transitional debt, not the Context Fabric Shared Kernel.

## Context Fabric authority projection

The Context Fabric projection exists to make enterprise architecture changes observable without creating a second accounting source of truth.

| Accounting change | Contract assertion/event may carry | Must remain local to accounting-information-platform |
|---|---|---|
| application/API/worker/database topology or owner change | canonical component/authority reference, change kind, valid/system time, provenance, released contract/profile version | accounting rows and credentials |
| billing/ERP/bank integration change | endpoint/contract identity, source authority, lifecycle state, risk/remediation reference | invoice/payment commercial facts and bank statement financial contents |
| PostgreSQL/object-storage/queue/runtime provider or version change | technology/provider/version reference, effective interval, operational provenance | journal/ledger balances and reconciliation monetary populations |
| reconciliation/accounting contract release | schema/profile/admission version and source artifact provenance | approval decisions, journal facts, financial balances |
| security/ownership remediation | risk/control/remediation references and accountable authority | secrets, raw PII, financial transaction contents |

Every published assertion/event preserves the accounting source authority and truth status instead of promoting proposed/inferred context. Valid/system time and provenance are mandatory where the released Context Fabric profile requires them. No LLM interpretation, EA projection or Context Graph admission result can post a journal, approve reconciliation, change accounting policy or close a period.

## Aggregate and invariant ownership

| Aggregate boundary | Context | Required invariant |
|---|---|---|
| Proposal admission | `proposal_intake` | One idempotency identity binds exactly one immutable source payload; changed replay fails closed |
| Accounting policy mapping | `policy_resolution` | Tenant/book/effective-time scope resolves one valid semantic role mapping or fails closed |
| General journal | `journal_posting` | Persisted journal is balanced, tenant/book scoped, exact-decimal and append-only |
| Reversal command | `journal_reversal` | Reversal is equal-and-opposite, linked to the original, idempotent and never backdates before the original accounting fact |
| Book-period control | `close_control` | Ordinary posting cannot cross a closed-period boundary; exceptional closing authority is explicit and purpose bound |
| Reconciliation run | `reconciliation_run_control` | Scope and cutoffs are immutable after evaluation begins; state transitions require durable evidence |
| Reconciliation review | `reconciliation_review` | Source allocations conserve exact amounts; terminal decisions bind immutable snapshots; unresolved exceptions fail close-review eligibility |
| Outbox record | `integration_outbox` | Domain fact and publication evidence commit atomically |

Domain services may coordinate calculations that do not naturally belong to one entity, but an adapter, HTTP handler, ORM row or provider DTO must not own these invariants.

## Current physical ownership and correction plan

The current package predates the explicit context map. The table below is therefore deliberately candid: several files contain more than one bounded context. `transitional-debt` means the path is accepted only as an existing migration source, not as a destination for new unrelated domain behavior.

| Physical path | Current owner(s) | DDD status | Next correction |
|---|---|---|---|
| `src/accounting_information_platform/ingest.py` | `proposal_intake` | transitional | Keep source-contract parsing isolated from posting decisions |
| `src/accounting_information_platform/accept.py` | `proposal_intake`, `journal_posting`, `journal_reversal` | `transitional-debt` | Split by command/application boundary when a touched slice can move with all imports/tests/contracts |
| `src/accounting_information_platform/core.py` | `policy_resolution`, `journal_posting` domain types | `transitional-debt` | Do not add a new unrelated rule; move the next touched coherent domain type set into its owning context |
| `src/accounting_information_platform/persistence.py` | multiple accounting contexts, persistence adapters | `transitional-debt` | Extract repository adapters context-by-context; database adapters must remain downstream of domain rules |
| `src/accounting_information_platform/http_api.py` | application/API adapters for multiple contexts | `transitional-debt` | Split routes/application orchestration by context without moving domain invariants into HTTP code |
| `src/accounting_information_platform/billing_pull.py` | `proposal_intake` ACL | transitional | Keep Billing/provider models outside journal domain entities |
| `src/accounting_information_platform/bank_statement.py` | `bank_statement_registry` | transitional | Preserve provider/camt parsing as an ACL; move only as a coherent adapter/evidence slice |
| `src/accounting_information_platform/iso20022/` | `bank_statement_registry` ACL | aligned | Version provider-format adapters independently from accounting domain types |
| `src/accounting_information_platform/reconciliation_run.py` | `reconciliation_run_control` | aligned | Keep lifecycle scope/evidence distinct from matching decisions |
| `src/accounting_information_platform/reconciliation_completion.py` | `reconciliation_run_control` | transitional | Preserve the evidence-derived completion command as run lifecycle authority; do not let it acquire posting or period-close authority |
| `src/accounting_information_platform/reconciliation.py` | `reconciliation_review` | transitional | Retain deterministic policy/domain behavior; no provider DTO dependency |
| `src/accounting_information_platform/allocation.py` | `reconciliation_review` | transitional | Keep exact allocation conservation in the domain boundary |
| `src/accounting_information_platform/reconciliation_bridge.py` | `reconciliation_review` | transitional | Keep exact book-to-bank arithmetic independent of HTTP/provider DTOs |
| `src/accounting_information_platform/reconciliation_read_model.py` | `reconciliation_review`, `reporting_projection` | transitional | Separate projection mechanics when the next material UI/read slice requires it |
| `src/accounting_information_platform/reconciliation_close_package.py` | `reconciliation_review` | transitional | Keep evidence packaging read-only and PostgreSQL-authority checks explicit |
| `src/accounting_information_platform/migration_install.py` | Generic deployment infrastructure | aligned | No accounting decision logic |

Physical moves are made only with a bounded behavior slice: map all imports/consumers, migration/API/event/schema contracts, tests and release compatibility first; then move the smallest coherent unit and update references in one change. No bulk rename is justified merely to make the directory tree resemble this document.

## Dependency fitness rules

New work must satisfy all of the following:

1. Do not create new generic domain buckets named `utils`, `helpers`, `common`, `services`, `lib`, `shared`, `core`, `models`, `misc` or `legacy`. Existing `core.py` is explicit debt, not precedent.
2. Do not import another ContextualWisdomLab application repository as a domain implementation dependency. The released contract-only `cwl_context_contracts` package is the sole Context Fabric Shared Kernel exception and remains an interoperability grammar, not a product implementation.
3. Do not query another service's application tables; all cross-service SQL is forbidden. Database SQL in this repository targets owned accounting schemas and PostgreSQL infrastructure only.
4. Domain calculations must remain executable without HTTP/framework/provider DTOs.
5. Persistence and transport code may depend on domain/application contracts; domain code must not depend on persistence/transport implementations.
6. A context may read a downstream projection only when the dependency is explicitly documented and cannot create a circular source of accounting truth.
7. Context Fabric contract consumption requires a released `cwl-context-contracts` version plus applicable conformance/admission evidence. Schema/profile/version drift fails closed; open-PR or branch bytes are not pinned as production dependencies.
8. Context Assertion/CloudEvents publication must preserve canonical authority references, truth status, valid/system time and provenance and must exclude journal/ledger balances and other financial facts from EA architecture projections.
9. Every newly created production module must be assignable to exactly one primary bounded context in this map. Cross-context application orchestration must be explicit rather than hidden in a generic module.

`tests/test_ddd_architecture_fitness.py` ratchets these rules that are mechanically checkable today. The test intentionally does not pretend that the transitional files above are already separated or that unreleased Context Fabric runtime integration has shipped.

## Architecture evidence

This context map records concerns, viewpoints, relationships and ownership so architectural descriptions can be evaluated against implementation rather than inferred from folder names. ISO/IEC/IEEE 42010:2022 is used as the architecture-description reference; it does not prescribe DDD or certify this repository.

### References

Evans, E. (2003). *Domain-driven design: Tackling complexity in the heart of software*. Addison-Wesley Professional.

ISO/IEC/IEEE. (2022). *ISO/IEC/IEEE 42010:2022 Software, systems and enterprise—Architecture description* (2nd ed.). International Organization for Standardization. https://www.iso.org/standard/74393.html