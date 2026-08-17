# ADR 0013: HTTP account-role mapping catalog read

**Status:** Accepted

## Decision

AIS exposes `lookup_account_role_mappings` and `GET /account-role-mappings?legal_entity_reference=&book_reference=` on the same stdlib HTTP surface as proposal accept. The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. The read returns existing `account_role_mapping` rows the poster already uses (`account_role_code` → `chart_account_code`, plus stored `accounting_policy_version` and `posting_rule_version`). It does not invent columns, a mapping table, or missing catalog facts. A tenant-header mismatch is rejected before the read and writes zero rows. `POST /account-role-mappings` is 405.

## Consequences

Billing and controllers can read the semantic-role-to-statutory-chart catalog without an in-process Python import. Cross-tenant and unknown book or legal-entity reads fail closed. Chart-account authority remains in AIS.
