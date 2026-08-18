# ADR 0003: Append-only journals and reversals

**Status:** Accepted

## Decision

A posted general journal is immutable. Correction creates a linked equal-and-opposite reversal and, when necessary, a separately approved replacement.

The in-memory `PostingLedger` is the reference oracle PostgreSQL must preserve. Its idempotency and reversal caches store and look up by the composite `(tenant_reference, idempotency_key)` or `(tenant_reference, journal_reference)`, matching the durable `UNIQUE (tenant_account_id, idempotency_key)` and `UNIQUE (tenant_account_id, journal_reference)` keys. A cache hit still compares the stored receipt `tenant_reference` before returning; a tenant mismatch is not a hit. The same `idempotency_key` string may therefore post independently for two tenants.

When a `journal_reference` for an existing `proposal_id` is already posted in that tenant and the incoming `idempotency_key` differs, the oracle fails closed and writes no second journal. Matching tenant, matching idempotency key, and matching source payload still return the original receipt. AIS does not invent a void journal key.

Checked-in PostgreSQL migrations cannot `UPDATE` or `DELETE` `general_journal` or `journal_entry_line`. `scripts/validate_repository.py` rejects those statements so later schema work cannot rewrite posted journals and still pass CI. `UPDATE` or `DELETE` of other tables remains valid. This gate does not add deferred balance triggers or `FORCE ROW LEVEL SECURITY` to the foundation migration.

`0005_closed_period_guard.sql` adds `guard_period_insert` / `closed_period_guard` so an `INSERT` into `general_journal` for a `soft_closed` or `hard_closed` period fails at the database. Ordinary privileged SQL cannot land a later journal after close. AIS sets a transaction-local `accounting_core.journal_write_role` (`period_closing`, `adjusting`, or `reversal`) before the closer, adjusting, or reversal insert; that role is not a Billing `journal_type`. Hard-closed periods reject every insert, including reversal-shaped rows. The AIS closer still posts the period-closing journal first, then flips `period_status_code`.

## Consequences

Historical audit evidence remains intact. APIs, database permissions, and migration CI must not expose journal update or delete operations. Reporting reconstructs effects from the complete population. A later command that reuses a posted `proposal_id` with a new idempotency key must reverse the existing journal, then post a replacement.
