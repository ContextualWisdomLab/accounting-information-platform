# ADR 0005: Policy-driven account mapping

**Status:** Accepted

## Context

External reporting taxonomies are a later projection of legal books, not
identifiers that source systems may submit. A semantic role boundary lets a
customer change charts of accounts without changing every upstream product.

## Decision

Source proposals use semantic account roles. The accounting boundary resolves those roles to effective-dated chart accounts under explicit accounting-policy and posting-rule versions.

A published policy manifest lists those pairs in `account_mappings`. JSON Schema `uniqueItems` compares whole objects, so the same `account_role_code` mapped to two `chart_account_code` values would otherwise be valid. AIS requires at most one mapping per `account_role_code` before `AccountingPolicy` is constructed. `load_chart_account_mapping` and `load_accounting_policy` fail closed. The schema records `x-cwl-unique-items-by: account_role_code`. This slice keeps the existing seven catalog roles and does not invent a withholding role.

## Consequences

A customer may change charts of accounts without changing every upstream product. Historical postings retain the mapping and policy versions used at posting time. Durable posting reads those mappings from `account_role_mapping`; Billing proposals carry only semantic roles. A duplicate role in a policy manifest cannot load and cannot post.

## References

XBRL International. (2003). *XBRL 2.1 specification*. https://specifications.xbrl.org/work-product-index-group-base-spec-base-spec.html
