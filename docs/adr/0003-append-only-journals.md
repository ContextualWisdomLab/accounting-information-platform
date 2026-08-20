# ADR 0003: Append-only journals and reversals

**Status:** Accepted

## Context

Posted journals are legal-book facts. Updating or deleting one would destroy
the evidence path from trial balance through journal lines, posting receipts,
source proposals, and payload hashes. Corrections must remain reconstructable:
the original journal stays an entity, a reversal is a later activity, and a
replacement is separately approved and attributed (World Wide Web Consortium,
2013).

## Decision

A posted general journal is immutable. Correction creates a linked equal-and-opposite reversal and, when necessary, a separately approved replacement.

The in-memory `PostingLedger` is the reference oracle PostgreSQL must preserve. Its idempotency and reversal caches store and look up by the composite `(tenant_reference, idempotency_key)` or `(tenant_reference, journal_reference)`, matching the durable `UNIQUE (tenant_account_id, idempotency_key)` and `UNIQUE (tenant_account_id, journal_reference)` keys. A cache hit still compares the stored receipt `tenant_reference` before returning; a tenant mismatch is not a hit. The same `idempotency_key` string may therefore post independently for two tenants.

When a `journal_reference` for an existing `proposal_id` is already posted in that tenant and the incoming `idempotency_key` differs, the oracle fails closed and writes no second journal. Matching tenant, matching idempotency key, and matching source payload still return the original receipt. AIS does not invent a void journal key.

`PostingLedger.reverse` and `PostgresPostingLedger.reverse` also fail closed when a journal already occupies `{journal_reference}:reversal`. They do not overwrite that posted journal. A reversal replay is valid only when the tenant, reversal command idempotency key, original journal reference, and immutable reversal-command payload hash all match the stored reversal command; any mismatch fails closed. Commercial `proposal_id` is a hyphenated UUID (the published Billing identifier charset) so a proposal cannot construct that reversal key with a `:` or `:reversal` suffix.

Checked-in PostgreSQL migrations cannot `UPDATE`, `DELETE`, `TRUNCATE`, or `DROP TABLE` `general_journal` or `journal_entry_line`, including schema-qualified and quoted identifiers. `scripts/validate_repository.py` rejects those statements so later schema work cannot rewrite posted journals and still pass CI. Destructive statements against unrelated tables remain valid.

`0005_closed_period_guard.sql` owns three database-level accounting invariants. First, `guard_period_insert` / `closed_period_guard` rejects an `INSERT` into `general_journal` for a `soft_closed` or `hard_closed` period. A soft-close exception requires both an AIS transaction classification in `accounting_core.journal_write_role` (`period_closing`, `adjusting`, or `reversal`) and membership of the session login in the NOLOGIN `accounting_closing_writer` database role. A caller cannot authorize itself by setting the GUC alone. Hard-closed periods reject every insert, including reversal-shaped rows. The AIS closer still posts the period-closing journal first, then flips `period_status_code`.

Second, deferred constraint triggers on `general_journal` and `journal_entry_line` recompute the complete persisted line population at transaction commit. A durable journal must contain at least one line and the exact `numeric(38, 6)` debit and credit totals must agree. Direct SQL that would leave a journal empty or unbalanced fails closed with `journal_unbalanced`; application-level proposal validation is therefore defense in depth rather than the only balance control.

Third, database mutation guards reject `UPDATE` or `DELETE` of posted journal headers, lines, source references, reversal lineage, posting receipts, and proposal source evidence. After a `posting_receipt` identifies a journal as finalized, later `INSERT` into that journal's `journal_entry_line` or `journal_source_reference` population also fails closed with `ledger_immutable`. The initial posting transaction remains valid because it writes lines and source references before issuing the authoritative receipt. A reversal remains a separate appended journal and lineage row rather than an edit of the original population.

## Consequences

Historical audit evidence remains intact. APIs, database permissions, migrations, and runtime roles must not expose an ungoverned journal mutation or close-bypass path. Reporting reconstructs effects from the complete population. A later command that reuses a posted `proposal_id` with a new idempotency key must reverse the existing journal, then post a replacement. Database migration tests and real PostgreSQL integration tests must prove commit-boundary balance, finalized-population immutability, and soft-close authorization before this decision is considered release-ready.

## References

World Wide Web Consortium. (2013). *PROV-O: The PROV ontology*. https://www.w3.org/TR/prov-o/
