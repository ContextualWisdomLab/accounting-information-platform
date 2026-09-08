# ADR 0057: Exact bank-statement balance evidence

## Status

Accepted in the current integration tree; protected-branch integration remains governed by the repository merge controls.

## Context

The camt.053.001.14 registry already retained opening and closing balance hashes,
but discarded the exact amount, currency, direction, and source locator after
normalization. A later book-to-bank bridge therefore could not calculate its
statement-side opening and closing equations from relational evidence. Reusing
the raw artifact at bridge time would make the bridge depend on reparsing an
external object and would not make the numeric fact part of the immutable
database evidence chain.

## Decision

Migration `0018_bank_statement_balance_evidence.sql` adds the tenant-scoped,
immutable `accounting_integration.bank_statement_balance` table. The parser
records every statement `Bal` in sequence with its optional type code and
`balance_type_source_code` discriminator (`cd` or `prtry`), exact `Decimal`
amount, ISO currency, `CRDT`/`DBIT` direction, typed effective date/time,
canonical source locator, and source hash. The effective date/time
is distinct from the statement period and system `recorded_at` time. The
existing opening and closing hash fields remain on `bank_statement_record` for
compatibility, and the normalized payload hash now includes the complete
balance facts.

The table has composite tenant foreign keys, forced row-level security,
database immutability, exact numeric storage, and no journal relationship that
could grant posting authority. A bridge may select standard `Cd` `OPBD` and
`CLBD` facts and convert direction under its own explicit accounting contract;
proprietary `Prtry` values remain distinct evidence even when their text matches
a standard code. The bridge must fail closed when required numeric facts are
missing or out of scope. Existing statements recorded before this additive
migration are not backfilled by inference or mutation. Their pre-0018 normalized
hashes remain replay-compatible only when the legacy balance hash shape matches
and the retained source artifact re-parses to the complete same normalized
evidence; unavailable or mismatched artifact retrieval fails closed. All accepted
balance, entry, and detail amounts must fit PostgreSQL `numeric(38,6)` before
artifact or relational persistence.

## Consequences

Reconciliation can consume exact persisted statement balances without treating
an artifact hash as an amount. Existing callers keep the hash fields and gain a
`balances` read projection, including whether a balance type came from standard
`Cd` or proprietary `Prtry` content. A historical statement with only hash evidence
remains valid immutable history but is not sufficient for a new exact bridge
until a separate, explicit evidence-repair contract exists.

This decision follows the vendored ISO 20022 camt.053.001.14 adapter evidence
and the repository's exact-decimal and evidence-only authority boundary. It
does not make ISO 20022 prescribe the reconciliation algorithm or grant the
bank-statement registry authority to post, reverse, approve, close, or select
chart accounts.

## References

See `docs/doctoring/REFERENCES.md` and the ISO 20022 / PostgreSQL entries in
`docs/doctoring/STANDARD_TRACEABILITY.md`.
