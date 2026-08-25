# ADR 0038: HTTP period journal list by source

**Status:** Accepted

## Decision

AIS extends `lookup_period_journals`, `load_period_journals`, and `GET /journals?legal_entity_reference=&fiscal_period_reference=&book_reference=` with optional `journal_source_code`. The only request identity header remains purpose-limited `X-CWL-Tenant-Reference`. This slice does not add a route, table, or migration. Single-journal GET by `idempotency_key` or `journal_reference` stays the ADR 0014 line inquiry.

Required list keys stay `legal_entity_reference`, `book_reference`, and `fiscal_period_reference`. Omit `journal_source_code` to keep today's period list and omit that envelope key. When supplied, the value is exactly `billing`, `adjusting`, `period_closing`, or `reversal`, and the list envelope includes `journal_source_code`. An unknown value is 400.

Classification is inferred from existing rows. `adjusting` is a journal whose `journal_entry_line.account_role_code` is `adjusting` (ADR 0031 `POST /journals`) and that is not a reversal. `period_closing` is the AIS hard-close journal (`journal_reference` prefix `urn:cwl:accounting:general_journal:period_closing:`). `reversal` is a journal that appears as `reversal_journal_id` in existing `journal_reversal` lineage (the same population as `GET /journal-reversals`). `billing` is a posted journal that is none of those. Matching journals are returned whole (`journal_reference`, `idempotency_key`, `journal_status_code`, `accounting_date`, `line_count`, and `reversal_of_journal_reference`); AIS does not return a filtered line subset.

An empty match returns `journals` [] rather than 404. Unknown legal entity, book, or period remains 404. A tenant-header mismatch is rejected before the read and writes zero rows.

IAS 10 requires events after the reporting period that provide evidence of conditions that existed at period end to be adjusting, and those entries are recorded before the books are locked (IFRS Foundation, 2022). ADR 0031 added the AIS adjusting write. This list is the worksheet population that write was missing: controllers can isolate adjusting journals from Billing posts, the period-closing journal, and reversals without SQL.

## Consequences

Controllers can take the adjusting worksheet, the Billing posting population, the hard-close journal, or the reversal lineage from the existing period list. Line detail stays on the single-journal GET. Journal authority remains the append-only `general_journal` population.
