# ADR 0029: HTTP journal-reversal list

**Status:** Accepted

## Decision

AIS exposes `lookup_journal_reversals` and `GET /journal-reversals` on the same stdlib HTTP surface as `POST /journal-reversals`. The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. Required `legal_entity_reference` scopes the list to that tenant entity. The read returns existing `accounting_core.journal_reversal` rows joined to the original and reversing `general_journal` rows (`reversal_journal_reference`, `original_journal_reference`, `reversal_date` from the reversing journal `accounting_date`, `posted_at`, and stored `reversal_reason_code`). AIS does not invent a list table or a reason that persist did not store.

Optional `original_journal_reference` keeps only that original journal's reversal. An unknown original returns `journal_reversals` [] rather than 404. Optional `fiscal_period_reference` joins the reversing journal to the existing `fiscal_period` row; a missing period fails closed. Pages are bounded (`page_limit` default 50, maximum 100) with optional `cursor` / `next_cursor` on `posted_at|journal_reference`. An empty history returns `journal_reversals` [] rather than 404. Filter keys appear on the document only when the query supplied them. `POST /journal-reversals` remains the ADR 0012 reverse command. This decision supersedes the ADR 0012 clause that `GET /journal-reversals` is 405. A tenant-header mismatch is rejected before the read and writes zero rows.

Append-only reversal lineage is derivation evidence: the reversing journal is a new posted fact linked to the original, not an update or delete (World Wide Web Consortium, 2013). Controllers and auditors reconstruct that lineage from stored rows without SQL (American Institute of Certified Public Accountants, 2017).

## Consequences

An auditor can list which journals were reversed, when, and why, from the same rows POST already writes. The reverse command, original receipt lookup, and reversing receipt at `reversal:{journal_reference}` stay unchanged.
