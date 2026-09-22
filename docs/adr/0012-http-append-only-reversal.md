# ADR 0012: HTTP append-only journal reversal

**Status:** Accepted

## Context

A reversal is a new accounting fact, not a mutation of the original journal. Treating only the original `journal_reference` as the retry identity is insufficient: a second command could reuse that journal while changing the reversal date or reason and accidentally receive the first receipt.

## Decision

AIS exposes `accept_journal_reversal` and `POST /journal-reversals` on the same stdlib HTTP surface as proposal accept. The request identifies the original journal by `journal_reference`, the Billing `idempotency_key` that produced the original receipt, or both. When both are supplied they must resolve to the same original journal; otherwise the command fails closed. `reversal_date` and `reversal_reason_code` are material command evidence.

`journal_reference` is executable target identity. The in-memory reversal boundary requires the canonical opaque CWL URN reference contract before reason validation, command-key/hash construction, replay lookup, journal lookup, or retained reversal construction. A malformed identifier therefore fails as invalid command identity rather than being hashed and then reported as an unknown journal.

`reversal_date` is executable accounting command evidence, not an annotation-only value. Before immutable command hashing, cached replay lookup, temporal ordering, policy-period comparison, or retained reversal construction, the reference core requires an exact built-in `datetime.date`. ISO-looking strings, `datetime.datetime`, and date subclasses are rejected through repository-owned accounting validation so caller-defined date behavior cannot participate in reversal hashing or replay identity.

Public reversal commands accept a tenant-scoped `reversal_idempotency_key`; when it is omitted, AIS derives the reserved command identity `reversal:{journal_reference}` after resolving the original journal. The optional Billing `idempotency_key` remains only an original-journal locator and is never reused as the reversal command identity. Direct persistence callers use the same explicit-or-reserved command-key rule. Its immutable command hash binds all of the following together:

- `tenant_reference`;
- reversal command idempotency identity;
- original `journal_reference`;
- `reversal_date`;
- `reversal_reason_code`.

Exact replay is permitted only when all of those values match the retained reversal evidence. Reusing the same command identity with a changed date, changed reason, changed original journal, or changed immutable command hash raises an idempotency conflict and writes no second journal. The retained reversing journal must carry enough command identity and hash evidence to make the same decision even when an in-memory receipt cache is absent.

The reversing journal is equal-and-opposite and append-only. Its accounting date cannot precede the original journal accounting date. The original journal and original posting receipt remain unchanged. `{journal_reference}:reversal` is reserved for the reversing journal; an unrelated occupant at that reference fails closed as an immutable-journal collision.

The HTTP handler resolves catalog policy from the original journal and delegates to the PostgreSQL adapter. `GET /journal-reversals` remains a read surface; `GET /posting-receipts?idempotency_key=` continues to return the original Billing receipt, while the reversing receipt is addressed through its reversal posting identity. No reversal path may update or delete posted journal facts.

## Runtime evidence

RED `65976748662a175fe4b43e28433827117f41f7a2` keeps the posted journal, policy scope, command reason, tenant identity and in-period calendar value constant while varying only the runtime domain of `reversal_date`. An ISO-looking string, `datetime.datetime`, and a hostile `date` subclass whose `isoformat()` raises must all fail through `AccountingValidationError`; after one valid reversal, a datetime-shaped replay must fail date admission before cached receipt or immutable-hash conflict handling. An exact built-in `date` remains the positive control.

Causal repair `e8263eb7fe5ecef092d50b6b8bb0c20c6efb9e44` reuses the repository's `_require_calendar_date()` at the start of `PostingLedger.reverse()`, before reversal-reason handling, command-key hashing, cache lookup, temporal comparison, policy-period evaluation, or any mutation. The production delta is one line and does not change accepted exact-date semantics, reversal arithmetic, idempotency identity, policy scope, persistence, or Period Close authority.

Reversal-target RED `0e3c80e1085a5cc25864107e3636fd690e6c6890` keeps one posted journal, policy scope, in-period reversal date, reason code, and command idempotency key fixed while supplying a malformed target reference. The command must fail through `AccountingValidationError` with journal-reference identity guidance and must not append a reversal; the retained canonical journal reference remains the positive control. Causal repair `c93de887719437950eb57b047d3673e32f79fb83` reuses `_require_reference()` at the start of `PostingLedger.reverse()` after date admission and before reason handling, command hashing, replay lookup, or journal lookup. The production delta is one line.

## Consequences

Controllers can reverse a posted journal without editing history. Cross-tenant, cross-book, unknown-journal, invalid-target-reference, invalid-period, temporal-order and occupied-reference cases fail closed before an authoritative second reversal is created. Soft-closed reversal still requires the purpose-limited database capability described in `docs/SECURITY.md` and `docs/OPERABILITY.md`; hard-closed periods reject a new reversal into the locked period.

The in-memory `PostingLedger` is the executable reference oracle for exact command replay. The PostgreSQL adapter must preserve the same command-key plus immutable-hash semantics on durable rows before PR #2 can leave its non-release-ready state. A passing cache-only replay or predecessor-head test is not sufficient release evidence. Exact calendar-date and canonical CWL reference admission are repository runtime invariants, not claims that IFRS/IASB prescribes Python types or identifier syntax.
