# ADR 0006: Reporting taxonomy as a versioned projection

**Status:** Accepted

## Context

The architecture already separates journal authority from `reporting_projection`: versioned trial-balance and financial-statement views. Presentation and disclosure in financial statements are not stored as fixed ledger columns (IFRS Foundation, 2024). External reporting taxonomies are likewise a later mapping over posted books, not identifiers that source systems may submit (XBRL International, 2003).

Folding statement layouts or taxonomy concepts into `general_journal` or `journal_entry_line` would force a chart or taxonomy change to rewrite posted facts.

## Decision

Trial-balance snapshots and financial-statement layouts are versioned projections of the immutable journal population. Reporting taxonomy mappings are later normalized modules that reference, and do not duplicate, journal authority.

## Consequences

The initial milestone produces deterministic trial-balance aggregation and retains a reporting roadmap. It does not claim production of statutory financial statements or a filed taxonomy instance. Future statement and taxonomy modules consume posted journals, receipts, and policy versions without changing the append-only journal model.

## References

IFRS Foundation. (2024). *IFRS 18 presentation and disclosure in financial statements*. https://www.ifrs.org/projects/completed-projects/2024/primary-financial-statements/

XBRL International. (2003). *XBRL 2.1 specification*. https://specifications.xbrl.org/work-product-index-group-base-spec-base-spec.html
