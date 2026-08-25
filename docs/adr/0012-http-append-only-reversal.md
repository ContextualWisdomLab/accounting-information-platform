# ADR 0012: HTTP append-only journal reversal

**Status:** Accepted

## Context

A reversal is a new accounting fact, not a mutation of the original journal. Treating only the original `journal_reference` as the retry identity is insufficient: a second command could reuse that journal while changing the reversal date or reason and accidentally receive the first receipt.

## Decision

AIS exposes `accept_journal_reversal` and `POST /journal-reversals` on the same stdlib HTTP surface as proposal accept. The request identifies the original journal by `journal_reference`, the Billing `idempotency_key` that produced the original receipt, or both. When both are supplied they must resolve to the same original journal; otherwise the command fails closed. `reversal_date` and `reversal_reason_code` are material command evidence.

Public reversal commands accept a tenant-scoped `reversal_idempotency_key`; when it is omitted, AIS derives the reserved command identity `reversal:{journal_reference}` after resolving the original journal. The optional Billing `idempotency_key` remains only an original-journal locator and is never reused as the reversal command identity. Direct persistence callers use the same explicit-or-reserved command-key rule. Its immutable command hash binds all of the following together:

- `tenant_reference`;
- reversal command idempotency identity;
- original `journal_reference`;
- `reversal_date`;
- `reversal_reason_code`.

Exact replay is permitted only when all of those values match the retained reversal evidence. Reusing the same command identity with a changed date, changed reason, changed original journal, or changed immutable command hash raises an idempotency conflict and writes no second journal. The retained reversing journal must carry enough command identity and hash evidence to make the same decision even when an in-memory receipt cache is absent.

The reversing journal is equal-and-opposite and append-only. Its accounting date cannot precede the original journal accounting date. The original journal and original posting receipt remain unchanged. `{journal_reference}:reversal` is reserved for the reversing journal; an unrelated occupant at that reference fails closed as an immutable-journal collision.

The HTTP handler resolves catalog policy from the original journal and delegates to the PostgreSQL adapter. `GET /journal-reversals` remains a read surface; `GET /posting-receipts?idempotency_key=` continues to return the original Billing receipt, while the reversing receipt is addressed through its reversal posting identity. No reversal path may update or delete posted journal facts.

## Consequences

Controllers can reverse a posted journal without editing history. Cross-tenant, cross-book, unknown-journal, invalid-period, temporal-order and occupied-reference cases fail closed before an authoritative second reversal is created. Soft-closed reversal still requires the purpose-limited database capability described in `docs/SECURITY.md` and `docs/OPERABILITY.md`; hard-closed periods reject a new reversal into the locked period.

The in-memory `PostingLedger` is the executable reference oracle for exact command replay. The PostgreSQL adapter must preserve the same command-key plus immutable-hash semantics on durable rows before PR #2 can leave its non-release-ready state. A passing cache-only replay or predecessor-head test is not sufficient release evidence.
