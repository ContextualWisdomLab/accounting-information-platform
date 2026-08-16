# ADR 0004: Exact decimal arithmetic

**Status:** Accepted

## Context

Every amount that affects a journal, balance, report, or reconciliation must be reproducible. Binary floating-point types store many decimal fractions as approximations, so the same proposal could hash, balance, or aggregate differently across languages and databases.

PostgreSQL documents `numeric` as the exact, user-specified precision type recommended for monetary amounts, and documents `real` and `double precision` as inexact (PostgreSQL Global Development Group, n.d.). The initial schema pins PostgreSQL 18.4 and uses that exact numeric type (PostgreSQL Global Development Group, 2026). This is a product invariant, not a claim of compliance with a numeric-format standard beyond the types named here.

## Decision

Amounts use canonical decimal strings at interfaces, `decimal.Decimal` in the Python reference core, and PostgreSQL `numeric(38, 6)` in the initial schema. Binary floating point is prohibited for accounting arithmetic.

## Consequences

Rounding, scale, foreign exchange, and reporting currency treatment require explicit versioned policy rather than implicit language or database defaults. Foreign-exchange accounting is rejected until rate source, rate type, date, rounding, remeasurement, and translation policy are implemented.

## References

PostgreSQL Global Development Group. (n.d.). *Numeric types*. https://www.postgresql.org/docs/18/datatype-numeric.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18.4 release notes*. https://www.postgresql.org/docs/release/18.4/
