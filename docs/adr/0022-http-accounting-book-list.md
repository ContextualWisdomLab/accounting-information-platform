# ADR 0022: HTTP accounting-book list

**Status:** Accepted

## Decision

AIS exposes `lookup_accounting_books` and `GET /accounting-books?legal_entity_reference=` on the same stdlib HTTP surface as chart-account and mapping catalog reads. The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. The read returns existing `accounting_core.accounting_book` rows for that tenant legal entity. `book_name` is the durable book identity already returned as `accounting_book_reference` and `book_reference` on trial-balance, chart-account, and financial-statement documents. `book_role_code` is returned as `intended_book_role_code`, the name already used on proposals and policy. `book_name` is also returned because that column exists. The list does not invent a book-code column, a list table, or paging; the catalog is small and ordered by `book_name`. An empty entity returns `accounting_books` [] rather than 404. A missing legal entity fails closed. `POST /accounting-books` is 405. A tenant-header mismatch is rejected before the read and writes zero rows.

## Consequences

Controllers can discover `book_reference` without SQL before calling `GET /trial-balances`, `GET /chart-accounts`, `GET /account-role-mappings`, or `GET /financial-statements`. Book authority remains the existing `accounting_book` population.
