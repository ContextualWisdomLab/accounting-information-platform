# ADR 0004: Exact decimal arithmetic

**Status:** Accepted

## Decision

Amounts use canonical decimal strings at interfaces, `decimal.Decimal` in the Python reference core, and PostgreSQL `numeric(38, 6)` in the initial schema. Binary floating point is prohibited for accounting arithmetic.

A debit or credit may have at most six digits after the decimal point. The published `positive_decimal` / `non_negative_decimal` patterns and `_parse_amount` use `^(0|[1-9][0-9]*)(\\.[0-9]{1,6})?$`. A longer value such as `0.0000010` is rejected; AIS does not coerce or round it to fit `numeric(38, 6)`. Integers and typical two-place Billing KRW amounts still pass. `positive_decimal` also excludes an all-zero string so the non-zero side of a line stays positive.

The published Billing proposal contract keeps amounts as strings. `ingest_journal_proposal` rejects JSON numbers, including integers and floats such as `25000.5`, before `str(value)` can satisfy the canonical-decimal regex. A missing amount key is a validation error, not a `KeyError`.

General Ledger double-entry validation is mathematical equality over the admitted canonical decimals and must not depend on the process-wide or caller-local `decimal` precision context. Journal proposal debit/credit totals therefore sum exact base-10 coefficients rather than using context-sensitive `Decimal` addition. The same exact sum is returned by the public `debit_total` and `credit_total` properties.

Construction-time validation is not the posting authority boundary. A frozen Python value object can still be altered through low-level deserialization or `object.__setattr__` before it reaches `PostingLedger.post()`. The posting command therefore recomputes the proposal's current exact debit and credit totals before idempotent replay lookup or line resolution and fails closed if the proposal no longer balances. A proposal that was balanced only at construction time is not sufficient evidence for posting.

Aggregate balance alone is also insufficient posting evidence. The posting command snapshots the current proposal lines into freshly validated `JournalLineProposal` values before idempotent replay lookup, then rechecks minimum line population, unique line numbers, per-line semantic role, canonical debit/credit amounts, exactly-one-positive-side semantics, and exact aggregate balance on that snapshot. A caller cannot preserve total debits and credits while mutating individual lines into two-sided, zero-sided, duplicate-number, or undersized populations and still obtain a posting or cached replay receipt.

Trial-balance arithmetic is the same accounting-control domain. Per-account debit and credit accumulation and `AccountBalance.net_balance` must preserve every admitted base-10 unit independently of the active `decimal` precision context. The reference core therefore reuses `_exact_decimal_sum()` for both trial-balance sides and for debit-minus-credit rather than relying on ambient-context `Decimal` addition or subtraction. This is exact mathematical aggregation, not a rounding or presentation policy.

## Runtime evidence

RED `6d37be8cbce89dc2a65e5e2e5d6e6f2e8d54cc1d` keeps proposal identity, currency, source provenance and line semantics fixed while varying only magnitude and ambient precision. It requires a one-unit imbalance (`10000000000000000000000000000 + 1` debit versus `10000000000000000000000000000` credit) to remain invalid at precision 28, and requires the genuinely balanced `10000000000000000000000000001` credit case to remain valid and report exact totals at precision 2.

Causal repair `3635196856ba56b44168102ad85b145cfa4c80d6` introduces `_exact_decimal_sum()` in the General Ledger reference core for `JournalProposal` balance admission and its debit/credit total properties. It does not change posting, reversal, period, chart-account, idempotency, Billing, or reconciliation authority.

Posting-boundary RED `150615433fe1ea8ce6c06e046092c49e3ee2b52a` constructs a valid balanced proposal, mutates one already-normalized line amount after constructor validation, and requires `PostingLedger.post()` to reject the now-unbalanced proposal without retaining a journal. The unchanged balanced control still posts normally. Causal repair `197ddcbb67ae2a17bbff0bfa6acea5d1d5f3a646` compares the current `debit_total` and `credit_total` through the existing context-independent exact-sum properties before replay lookup or posting-side effects. Review-test descendant `e9ad50c3ea643affb13f72f4c2705f98cf8e64e0` posts the balanced proposal once, mutates the same object, then requires the replay attempt to fail closed while the already-posted journal count remains one, directly proving that revalidation precedes cached idempotent replay.

Posting-line RED `2c08112de6d7ee02c45633d5a9a96d4ea65c0e03` keeps an exact balanced journal and policy constant while mutating only already-validated line state after construction. It requires a balanced-but-two-sided line pair, a duplicate line number, and a one-line shrunken population to fail before persistence; a replay attempt with the same idempotency key must also reject the now-invalid line semantics rather than return the cached receipt. Causal repair `e511aba1af74419d64d85e33cd705cbc75f8b301` snapshots the current population through fresh `JournalLineProposal` construction, rechecks minimum cardinality and line-number uniqueness, computes exact balance from that validated snapshot, and resolves only the snapshot after those controls pass.

Trial-balance RED `681860c47083b6766621ad5b5e41be7ed2a221f3` holds tenant/entity/book scope, posting provenance, chart-account mapping and canonical monetary inputs constant while lowering the active Decimal precision. It requires the shared cash account to retain a low-order debit unit and requires `AccountBalance.net_balance` to return the exact mathematical difference. Production repair `f80b8ea1dcb7800eb5c4b75bc7ae52ede6b0ce7a` reuses `_exact_decimal_sum()` for debit/credit accumulation and net balance. Review repair `23d8fc12c09491c932426c2eb1a0b16db35aada9` removes an unrelated policy-load guidance drift without altering the arithmetic fix. Test-only descendant `96f9fada493f8fd23c0e302258b36fe5e60ea62f` adds the symmetric shared-account credit accumulation oracle so both accumulation sides are directly exercised under low precision.

## Consequences

Rounding, scale, foreign exchange, and reporting currency treatment require explicit versioned policy rather than implicit language or database defaults. Billing cannot smuggle a binary float through HTTP accept into the ledger.

A journal, trial balance, or account net balance cannot become apparently balanced, unbalanced, or lose a low-order unit solely because an embedding caller changed Python's active Decimal precision. Posting cannot rely on stale constructor-time aggregate balance or stale per-line semantics when executable line state has changed before the posting boundary. Future monetary aggregate paths must either reuse an exact context-independent arithmetic primitive or document an explicit versioned rounding/scale policy before arithmetic is used as accounting authority.
