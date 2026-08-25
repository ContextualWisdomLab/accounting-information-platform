# ADR 0051: Accounting-book-scoped fiscal-period control

## Status
Accepted for the accounting posting foundation.

## Decision
A fiscal calendar period keeps shared dates and an aggregate compatibility status, while posting and close authority are controlled independently by `accounting_book_period_control` for each accounting book. A close command materializes controls for active books, locks only the selected book-period row, and changes only that book's authoritative close state. The legacy `fiscal_period.period_status_code` is maintained as an aggregate (`open` while any active book is open, `soft_closed` when none are open but at least one is not hard closed, and `hard_closed` only when all active books are hard closed).

The PostgreSQL journal-insert guard reads the book-period control first and falls back to the calendar period only for a book-period that predates control materialization. This prevents a statutory close from blocking a management book and prevents an open sibling book from bypassing a selected book's hard close.

Close idempotency, command locking, and AIS closing-journal identity include the accounting-book scope. Trial-balance snapshots remain immutable evidence keyed by accounting book and fiscal period.

## Consequences
Controllers may close statutory and management books on different schedules without changing shared calendar dates. Existing single-book behavior remains compatible through the aggregate calendar status. New book creation inside an already closed calendar remains fail-closed until its book-period state is explicitly established through the controlled close/open lifecycle.

No claim of statutory compliance is implied; this ADR defines the system-of-record isolation invariant and its database enforcement.
