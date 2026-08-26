# ADR 0053: Reporting taxonomy as a versioned projection

**Status:** Accepted

## Context

The accounting platform's authoritative facts are immutable posted journals, reversals, close evidence, and the exact trial-balance populations derived from them. Financial-statement presentation and external digital-reporting taxonomies are downstream views of those facts, not fields that source systems may submit or identifiers that may rewrite journal authority.

IFRS 18 sets presentation and disclosure requirements and is effective for annual reporting periods beginning on or after 1 January 2027, with earlier application permitted (IFRS Foundation, 2024). XBRL 2.1 defines the base representation for facts, instance documents, concepts, and taxonomies used in digital business reporting (XBRL International, 2013). Neither authority requires the ledger core to embed presentation-taxonomy identifiers in posted journal rows.

## Decision

Financial-statement layouts and reporting-taxonomy mappings are a **versioned projection** over the immutable accounting population. A projection identifies its taxonomy/presentation version and knowledge cutoff, references the authoritative accounting scope it was derived from, and can be regenerated without changing `general_journal`, `journal_entry_line`, reversal lineage, or posting receipts.

The core chart of accounts remains the book authority. External taxonomy concepts may map to chart accounts or reporting groups through separately versioned policy/mapping records, but they do not become source-system posting roles and cannot bypass accounting policy resolution.

## Consequences

The platform can evolve statutory and management presentation independently of the journal schema while preserving reproducibility and audit lineage. Existing financial-statement APIs remain projections of authoritative posted books; a later XBRL serializer or jurisdiction-specific taxonomy adapter must consume those versioned projections rather than mutate the ledger.

This ADR **does not claim** that the current product produces a filing-ready XBRL instance, implements every IFRS 18 presentation/disclosure requirement, or is certified/compliant for any jurisdiction. Those claims require separate taxonomy, validation, disclosure, review, and filing evidence.

## References

IFRS Foundation. (2024). *IFRS 18 presentation and disclosure in financial statements*. https://www.ifrs.org/issued-standards/list-of-standards/ifrs-18-presentation-and-disclosure-in-financial-statements/

XBRL International. (2013). *XBRL 2.1 specification*. https://specifications.xbrl.org/work-product-index-group-base-spec-base-spec.html
