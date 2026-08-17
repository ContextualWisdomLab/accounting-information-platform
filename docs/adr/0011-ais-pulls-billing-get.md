# ADR 0011: AIS pulls Billing GET and does not flip proposal status

**Status:** Accepted

## Decision

AIS is a Billing GET consumer. `pull_validated_journal_proposals` calls `GET {BILLING_BASE_URL}/v1/journal-proposals` with purpose-limited `X-CWL-Tenant-Reference` and the same `tenant_reference` query value, `proposal_status=validated`, and the published optional `proposed_after`, `cursor`, and `page_limit` fields. The list envelope keys are the Billing #15 contract: `journal_proposals` and `next_cursor`. Each item is the published `accounting_journal_proposal`. AIS drops any wire item whose `proposal_status` is not `validated`. `pull_journal_proposal` calls `GET /v1/journal-proposals/{proposal_id}` for a same-tenant hit and treats HTTP 404 as unknown or cross-tenant without retrying as another tenant. `accept_pulled_proposals` pages until `next_cursor` is empty and posts each validated item through `accept_journal_proposal`. Replay of the same tenant, Billing `idempotency_key`, and `source_payload_hash` returns the same `accounting_posting_receipt` and writes no second journal. A failed item fails closed for that item and does not abort the page. A failed Billing GET writes zero AIS rows. After ingest and post, AIS does not call Billing to mark a proposal `exported`, `posted`, or consumed. `POST /billing-proposal-pulls` exposes the same command to operators.

## Consequences

Cash and invoice proposals share Billing's `journal_proposal` store and enter AIS through one pull. Semantic roles remain `accounts_receivable`, `usage_revenue`, and `cash_receipt`; AIS catalog mapping still selects 110100, 410100, and 110200. Billing remains the proposal-status authority. AIS remains the posting-receipt authority.
