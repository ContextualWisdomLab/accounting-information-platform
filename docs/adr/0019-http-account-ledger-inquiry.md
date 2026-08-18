# ADR 0019: HTTP account-ledger inquiry

**Status:** Accepted

## Decision

AIS exposes `lookup_account_ledger` and `GET /account-ledgers?legal_entity_reference=&chart_account_code=` on the same stdlib HTTP surface as posted-journal inquiry. The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. The read returns existing `journal_entry_line` rows for that tenant, legal entity, and statutory `chart_account_code`, joined through `general_journal`, `chart_account`, `legal_entity_record`, and optional `fiscal_period`. Line keys copy GET `/journals` (`line_number`, `chart_account_code`, `account_role_code`, `debit_amount`, `credit_amount`) and add `journal_reference` and `posted_at` from the journal header. `period_debit_total` and `period_credit_total` are exact decimal strings for the full filtered scope, not only the current page. Optional `fiscal_period_reference` keeps lines whose journal is in that period. An empty activity set returns `ledger_lines` [] rather than 404. Missing legal entity, chart account, or supplied period fails closed. Pages are bounded (`page_limit` default 50, maximum 100) with optional `cursor` / `next_cursor` on `posted_at|journal_reference|line_number`. `POST /account-ledgers` is 405. A tenant-header mismatch is rejected before the read and writes zero rows. This decision is numbered 0019 because ADR 0015 already records HTTP fiscal-period open.

## Consequences

Controllers can inspect a statutory account's posted activity without SQL and without knowing every journal identity. Journal and chart authority remain the existing `general_journal`, `journal_entry_line`, and `chart_account` rows.
