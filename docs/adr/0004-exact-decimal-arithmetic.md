# ADR 0004: Exact decimal arithmetic

**Status:** Accepted

## Decision

Amounts use canonical decimal strings at interfaces, `decimal.Decimal` in the Python reference core, and PostgreSQL `numeric(38, 6)` in the initial schema. Binary floating point is prohibited for accounting arithmetic.

## Consequences

Rounding, scale, foreign exchange, and reporting currency treatment require explicit versioned policy rather than implicit language or database defaults.
