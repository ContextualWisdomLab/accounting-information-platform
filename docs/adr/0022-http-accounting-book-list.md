# ADR 0022: HTTP accounting-book list

**Status:** Accepted historical catalog contract; active durable-reference uniqueness amendment Proposed in #58

## Historical decision

AIS exposes `lookup_accounting_books` and `GET /accounting-books?legal_entity_reference=` on the same stdlib HTTP surface as chart-account and mapping catalog reads. The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. The read returns existing `accounting_core.accounting_book` rows for that tenant legal entity. `book_name` is the durable book identity already returned as `accounting_book_reference` and `book_reference` on trial-balance, chart-account, and financial-statement documents. `book_role_code` is returned as `intended_book_role_code`, the name already used on proposals and policy. `book_name` is also returned because that column exists. The list does not invent a book-code column, a list table, or paging; the catalog is small and ordered by `book_name`. An empty entity returns `accounting_books` [] rather than 404. A missing legal entity fails closed. `POST /accounting-books` is 405. A tenant-header mismatch is rejected before the read and writes zero rows.

Controllers can discover `book_reference` without SQL before calling `GET /trial-balances`, `GET /chart-accounts`, `GET /account-role-mappings`, or `GET /financial-statements`. Book authority remains the existing `accounting_book` population.

## Review finding

Protected migration 0001 does not enforce the identity implied by that public contract. `accounting_core.accounting_book` is unique on `(tenant_account_id, legal_entity_id, book_role_code, valid_from)` and UUID-based keys, but not on one active `(tenant_account_id, legal_entity_id, book_name)` reference. Two simultaneously active Accounting Book Entities may therefore expose the same durable `book_reference` while carrying different `accounting_book_id` and role/effective-time identity.

A consumer that resolves `book_reference` with `.fetchone()`, implicit row order, a default `book_role_code`, or current account mappings can consequently bind an arbitrary Accounting Book Entity. PR #57 exposed the immediate General Ledger risk while preparing book-scoped ledger reads; Issue #58 owns the underlying catalog invariant.

This is an AIS accounting-master-data identity defect. IFRS does not prescribe the PostgreSQL key or index used to repair it. The database mechanism remains an implementation control supporting deterministic accounting-book identity, tenant/legal-entity scope, effective-time history, and downstream financial-control traceability.

## Proposed amendment

Within one tenant and legal entity, an externally durable `book_name` / `book_reference` must identify at most one **active** Accounting Book Entity. Historical effective-dated rows may retain the same reference after expiry; preserving historical rows is required so posted facts and prior evidence are not rewritten merely because the current book catalog changes.

The canonical repair should therefore:

- enforce one active `(tenant_account_id, legal_entity_id, book_name)` identity in PostgreSQL;
- retain expired historical rows with the same `book_name` when their validity intervals do not make them active simultaneously;
- make public resolvers fail closed on zero or multiple active matches rather than selecting a row by ordering or role inference;
- preserve `accounting_book_id` as the immutable relational Entity key used by journals, chart accounts, close evidence, reconciliation and reporting sources;
- preserve tenant RLS/composite-FK boundaries, reporting-currency facts and effective/system-time evidence;
- reject upgrade if existing active duplicate references cannot be reconciled without an explicit accounting-master-data decision; migration code must not silently rename, merge or delete books.

A partial unique index or equivalent database-owned active-row invariant is preferred over application-only validation because concurrent catalog writes must not create duplicate authority. Resolver cardinality checks remain defense in depth and provide a stable fail-closed error if legacy/corrupt state is encountered.

## Alternatives rejected

Making `book_role_code` part of the public identity is rejected because ADR 0022 already exposes `book_name` as the durable reference and callers should not have to infer an accounting role to disambiguate one identifier. Selecting the first active row is rejected because result identity would depend on query order. Making `book_name` globally unique for all history is rejected because it would prevent lawful effective-dated history. Rewriting posted journals or retained evidence to a newly chosen book is rejected because posted accounting facts are immutable.

## Implementation and evidence boundary

The amendment remains **Proposed**. Exact RED `a600d61b4fb5a92294964a33d4a497175c302e0d` in Draft #59 adds a real-PostgreSQL regression requiring a second active row with the same durable reference to fail while an expired historical row with that reference remains lawful. Accounting Foundation `34335332652` reached behavior tests and failed on that exact RED head; its exact-head SAST, security and dependency-diff jobs were GREEN.

Do not allocate an apparently free migration number from protected `develop`, which currently ends at migration 0014, while #29/#47/#53 own the live unreleased forward migration stack. After those prerequisites integrate, non-force reconcile #59 on the exact protected parent, allocate the next canonical migration identity, add upgrade/preflight/rollback/recovery evidence, and reacquire all exact-head gates.

Shared `CHANGELOG.md`, `docs/doctoring/STANDARD_TRACEABILITY.md`, and `docs/product-technical-gap-baseline.md` remain PR #37 single-writer surfaces and are updated only from protected integrated evidence.
