# ADR 0040: Reporting taxonomy as a versioned projection

**Status:** Accepted

## Context

The architecture separates journal authority from `reporting_projection`: versioned trial-balance and financial-statement views. Presentation and disclosure in financial statements are not stored as fixed ledger columns (IFRS Foundation, 2024). External reporting taxonomies are likewise a later mapping over posted books, not identifiers that source systems may submit (XBRL International, 2003).

Folding statement layouts or taxonomy concepts into `general_journal` or `journal_entry_line` would force a chart or taxonomy change to rewrite posted facts.

This record was drafted as ADR 0006 on the documentation branch. The posting-foundation line already used 0006 for fiscal-period close snapshots, so this decision is recorded as 0040.

## Decision

Trial-balance snapshots and financial-statement layouts are versioned projections of the immutable journal population. Reporting taxonomy mappings are later normalized modules that reference, and do not duplicate, journal authority.

## Consequences

HTTP statement reads project income statement, balance sheet, changes in equity, and cash flow from posted journals and close snapshots. Those projections do not write taxonomy identifiers onto journal lines. A filed XBRL taxonomy instance remains out of scope. Future taxonomy modules consume posted journals, receipts, and policy versions without changing the append-only journal model.

## References

IFRS Foundation. (2024). *IFRS 18 presentation and disclosure in financial statements*. https://www.ifrs.org/projects/completed-projects/2024/primary-financial-statements/

XBRL International. (2003). *XBRL 2.1 specification*. https://specifications.xbrl.org/work-product-index-group-base-spec-base-spec.html
