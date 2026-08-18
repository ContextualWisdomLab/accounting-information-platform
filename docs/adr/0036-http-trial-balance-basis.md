# ADR 0036: HTTP trial-balance worksheet basis

**Status:** Accepted

## Decision

AIS extends `lookup_trial_balance` and `GET /trial-balances?legal_entity_reference=&book_reference=&fiscal_period_reference=` with optional `balance_basis_code`. The only request identity header remains purpose-limited `X-CWL-Tenant-Reference`. This slice does not add a route, table, or migration. Account-balance, account-rollforward, and financial-statement reads stay on their current live-versus-snapshot rules.

Required query keys stay `legal_entity_reference`, `book_reference`, and `fiscal_period_reference`. Omit `balance_basis_code` to keep today's document: live posted totals through period end when the period is open or `soft_closed`, including AIS adjusting journals; the stored `trial_balance_snapshot` when the period is `hard_closed`. The omit document does not include `balance_basis_code`.

When supplied, `balance_basis_code` is exactly `unadjusted`, `adjusted`, or `post_close`, and the document includes that key. An unknown value is 400. `unadjusted` is the live as-of sum of posted lines through the period end, excluding `journal_entry_line.account_role_code=adjusting` and excluding the AIS period-closing journal (`journal_reference` prefix `urn:cwl:accounting:general_journal:period_closing:`). `adjusted` is the same live sum including adjusting journals and still excluding that closing journal. Both live bases work on open, `soft_closed`, and `hard_closed` periods and do not read the snapshot. `post_close` returns the stored `trial_balance_snapshot` only. A missing snapshot (open or `soft_closed`) is 409; AIS does not invent a post-close trial balance from live journals.

Empty books return the existing empty trial-balance shape (`lines` []) rather than 404. Unknown legal entity, book, or period remains 404. A tenant-header mismatch is rejected before the read and writes zero rows.

IAS 10 requires events after the reporting period that provide evidence of conditions that existed at period end to be adjusting, and those entries are recorded before the books are locked (IFRS Foundation, 2022). ADR 0023 already made soft-close that adjusting window and hard-close the durable lock. ADR 0031 added the AIS adjusting write. This read is the worksheet that window was missing: unadjusted before those entries, adjusted after them, and post-close only from the stored snapshot.

## Consequences

Controllers can show the trial-balance worksheet before versus after adjusting journals, and the locked post-close snapshot after hard-close, without a second route or a second numerical truth. Soft-close plus `POST /journals` stay the write path. Account-balance, rollforward, and statement GETs are unchanged.
