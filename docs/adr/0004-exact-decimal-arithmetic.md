# ADR 0004: Exact decimal arithmetic

**Status:** Accepted

## Decision

Amounts use canonical decimal strings at interfaces, `decimal.Decimal` in the Python reference core, and PostgreSQL `numeric(38, 6)` in the initial schema. Binary floating point is prohibited for accounting arithmetic.

The published Billing proposal contract keeps amounts as strings. `ingest_journal_proposal` rejects JSON numbers, including integers and floats such as `25000.5`, before `str(value)` can satisfy the canonical-decimal regex. A missing amount key is a validation error, not a `KeyError`.

## Consequences

Rounding, scale, foreign exchange, and reporting currency treatment require explicit versioned policy rather than implicit language or database defaults. Billing cannot smuggle a binary float through HTTP accept into the ledger.
