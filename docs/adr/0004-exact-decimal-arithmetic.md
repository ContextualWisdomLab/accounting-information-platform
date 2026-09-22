# ADR 0004: Exact decimal arithmetic

**Status:** Accepted

## Decision

Amounts use canonical decimal strings at interfaces, `decimal.Decimal` in the Python reference core, and PostgreSQL `numeric(38, 6)` in the initial schema. Binary floating point is prohibited for accounting arithmetic.

A debit or credit may have at most six digits after the decimal point. The published `positive_decimal` / `non_negative_decimal` patterns and `_parse_amount` use `^(0|[1-9][0-9]*)(\\.[0-9]{1,6})?$`. A longer value such as `0.0000010` is rejected; AIS does not coerce or round it to fit `numeric(38, 6)`. Integers and typical two-place Billing KRW amounts still pass. `positive_decimal` also excludes an all-zero string so the non-zero side of a line stays positive.

The published Billing proposal contract keeps amounts as strings. `ingest_journal_proposal` rejects JSON numbers, including integers and floats such as `25000.5`, before `str(value)` can satisfy the canonical-decimal regex. A missing amount key is a validation error, not a `KeyError`.

General Ledger double-entry validation is mathematical equality over the admitted canonical decimals and must not depend on the process-wide or caller-local `decimal` precision context. Journal proposal debit/credit totals therefore sum exact base-10 coefficients rather than using context-sensitive `Decimal` addition. The same exact sum is returned by the public `debit_total` and `credit_total` properties.

## Runtime evidence

RED `6d37be8cbce89dc2a65e5e2e5d6e6f2e8d54cc1d` keeps proposal identity, currency, source provenance and line semantics fixed while varying only magnitude and ambient precision. It requires a one-unit imbalance (`10000000000000000000000000000 + 1` debit versus `10000000000000000000000000000` credit) to remain invalid at precision 28, and requires the genuinely balanced `10000000000000000000000000001` credit case to remain valid and report exact totals at precision 2.

Causal repair `3635196856ba56b44168102ad85b145cfa4c80d6` introduces `_exact_decimal_sum()` in the General Ledger reference core and uses it only for `JournalProposal` balance admission and its debit/credit total properties. It does not change posting, reversal, period, chart-account, idempotency, Billing, or reconciliation authority. Trial-balance accumulation is a separate control surface and is not silently claimed repaired by this change.

## Consequences

Rounding, scale, foreign exchange, and reporting currency treatment require explicit versioned policy rather than implicit language or database defaults. Billing cannot smuggle a binary float through HTTP accept into the ledger.

A journal cannot become apparently balanced, or apparently unbalanced, solely because an embedding caller changed Python's active Decimal precision. Future monetary aggregate paths must either reuse an exact context-independent arithmetic primitive or document an explicit versioned rounding/scale policy before arithmetic is used as accounting authority.
