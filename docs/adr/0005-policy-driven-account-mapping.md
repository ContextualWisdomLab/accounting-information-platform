# ADR 0005: Policy-driven account mapping

**Status:** Accepted

## Context

Billing and operational products know semantic roles such as `accounts_receivable`, `usage_revenue`, or `contract_liability`. They do not own the chart of accounts, effective dates, or book-specific identifiers. If a source system sent chart-account IDs, a customer chart change would break every upstream product and bypass controller-owned mapping.

External reporting taxonomies are a later, versioned projection of legal books, not columns in the journal core (XBRL International, 2003). Semantic roles therefore stop at the accounting boundary, where an approved policy version maps them to chart accounts.

## Decision

Source proposals use semantic account roles. The accounting boundary resolves those roles to effective-dated chart accounts under explicit accounting-policy and posting-rule versions.

## Consequences

A customer may change charts of accounts without changing every upstream product. Historical postings retain the mapping and policy versions used at posting time. Unknown account roles produce no journal.

## References

XBRL International. (2003). *XBRL 2.1 specification*. https://specifications.xbrl.org/work-product-index-group-base-spec-base-spec.html
