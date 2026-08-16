# ADR 0004: Exact decimal arithmetic

**Status:** Accepted

## Context

Every amount that affects a journal, balance, report, or reconciliation must be
reproducible. Binary floating-point types represent many decimal fractions as
approximations, so the same proposal could hash, balance, or aggregate
differently across languages and databases. PostgreSQL documents `numeric` as
the exact, user-specified precision type for monetary amounts (PostgreSQL
Global Development Group, n.d.).

## Decision

Amounts use canonical decimal strings at interfaces, `decimal.Decimal` in the Python reference core, and PostgreSQL `numeric(38, 6)` in the initial schema. Binary floating point is prohibited for accounting arithmetic.

A debit or credit may have at most six digits after the decimal point. The published `positive_decimal` / `non_negative_decimal` patterns and `_parse_amount` use `^(0|[1-9][0-9]*)(\\.[0-9]{1,6})?$`. A longer value such as `0.0000010` is rejected; AIS does not coerce or round it to fit `numeric(38, 6)`. Integers and typical two-place Billing KRW amounts still pass. `positive_decimal` also excludes an all-zero string so the non-zero side of a line stays positive.

The published Billing proposal contract keeps amounts as strings. `ingest_journal_proposal` rejects JSON numbers, including integers and floats such as `25000.5`, before `str(value)` can satisfy the canonical-decimal regex. A missing amount key is a validation error, not a `KeyError`.

## Consequences

Rounding, scale, foreign exchange, and reporting currency treatment require explicit versioned policy rather than implicit language or database defaults. Billing cannot smuggle a binary float through HTTP accept into the ledger. Foreign-exchange accounting remains rejected until rate source, rate type, date, rounding, remeasurement, and translation policy are implemented.

## References

PostgreSQL Global Development Group. (n.d.). *Numeric types*. https://www.postgresql.org/docs/18/datatype-numeric.html

PostgreSQL Global Development Group. (2026). *PostgreSQL 18.4 release notes*. https://www.postgresql.org/docs/release/18.4/
