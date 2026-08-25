# ADR 0014: HTTP posted-journal inquiry

**Status:** Accepted

## Decision

AIS exposes `lookup_posted_journal` and `GET /journals?idempotency_key=` or `GET /journals?journal_reference=` on the same stdlib HTTP surface as proposal accept. The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. The read returns the existing `general_journal` row plus its `journal_entry_line` rows (`line_number`, `chart_account_code`, `account_role_code`, exact decimal debit and credit). A posting receipt remains a header and does not replace this inquiry. If the key or reference identifies a reversing journal, that reversing journal is returned. A missing journal is not invented. A tenant-header mismatch is rejected before the read and writes zero rows. `POST /journals` is 405.

## Consequences

Controllers can inspect posted and reversing lines without an in-process Python import. Cross-tenant and unknown-key reads fail closed. Journal authority remains the existing append-only `general_journal` population.
