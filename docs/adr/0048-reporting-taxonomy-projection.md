# ADR 0048: Reporting taxonomy as a versioned projection

**Status:** Accepted

## Context

The architecture separates journal authority from reporting projections:
versioned trial-balance and financial-statement views. Presentation and
disclosure in financial statements are not fixed ledger columns (IFRS
Foundation, 2024). External reporting taxonomies are likewise later mappings
over posted books, not identifiers that source systems may submit (XBRL
International, 2003).

## Decision

Trial-balance snapshots and financial-statement layouts are versioned
projections of the immutable journal population. Reporting taxonomy mappings
are later normalized modules that reference, and do not duplicate, journal
authority.

## Consequences

The initial milestone produces deterministic trial-balance aggregation and
retains a reporting roadmap. It does not claim production of statutory
financial statements or a filed taxonomy instance. Future statement and
taxonomy modules consume posted journals, receipts, and policy versions
without changing the append-only journal model.

## References

IFRS Foundation. (2024). *IFRS 18 presentation and disclosure in financial statements*. https://www.ifrs.org/projects/completed-projects/2024/primary-financial-statements/

XBRL International. (2003). *XBRL 2.1 specification*. https://specifications.xbrl.org/work-product-index-group-base-spec-base-spec.html
