# ADR 0031: HTTP adjusting journal write

**Status:** Accepted

## Decision

AIS exposes `accept_adjusting_journal` and `POST /journals` on the same stdlib HTTP surface as Billing `POST /journal-proposals` and `GET /journals`. The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. A missing header is 400. A header or body tenant mismatch is 403 and writes zero rows.

This write is AIS-owned. Billing ingest stays on `POST /journal-proposals`. Adjusting lines identify statutory `chart_account_code` directly because period-end accruals, prepaids, and depreciation have no Billing semantic role. AIS does not require `account_role_code` on the request. Unknown chart codes fail closed (422). AIS does not invent chart codes.

The JSON body keys are `tenant_reference`, `legal_entity_reference`, `accounting_book_reference`, `fiscal_period_reference` or the existing `period_code` alias used by period open and close, `journal_date`, `idempotency_key`, `journal_description`, and `journal_lines` (`chart_account_code`, `debit_credit_code` of `debit` or `credit`, exact decimal `amount`, and `currency_code`). Currency must match the book. The journal must balance in exact decimal. `journal_date` must fall inside the named period.

The period must be `open` or `soft_closed`. `hard_closed` is 409 and writes zero rows. Unknown legal entity, book, or fiscal period is 404. Persist reuses the ordinary `PostgresPostingLedger` post tables in one transaction, including `journal_proposal_record`, `general_journal`, `journal_entry_line`, `posting_receipt`, and `outbox_event` (`posting_receipt`). No new table or migration is added. The response is the existing `accounting_posting_receipt` already returned by `POST /journal-proposals`. Replay of the same tenant plus `idempotency_key` returns that receipt and writes no second journal. `GET /journals` query keys stay unchanged and return the adjusting journal after accept.

This decision supersedes the ADR 0014 and ADR 0016 clauses that `POST /journals` is 405.

IAS 10 requires events after the reporting period that provide evidence of conditions that existed at period end to be adjusting, and those entries are recorded before the books are locked (IFRS Foundation, 2022). ADR 0023 already made soft-close that adjusting window and hard-close the durable lock. This write is the operator path that window was missing.

## Consequences

Controllers can post period-end adjusting journals after soft-close and before hard-close without sending a Billing proposal or inventing a semantic role. Ordinary Billing posts remain rejected in a closed period. Journal inquiry, Billing ingest, and two-step close stay on their existing routes.
