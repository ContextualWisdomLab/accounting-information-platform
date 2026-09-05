# Period Close Posted-Role Traceability

Status: RED / Proposed production repair

## Problem

`journal_entry_line.account_role_code` is persisted with every posted journal line and is part of the immutable posted accounting fact. `PostgresPostingLedger._post_closing_journal()` currently ignores that historical role when selecting revenue and expense lines for the AIS-owned closing journal. Instead, it joins `account_role_mapping` and requires the mapping to be current (`valid_to IS NULL`).

A role mapping may legitimately expire or be superseded after a journal was posted and before the fiscal period is hard-closed. In that state the posted journal still contains the original role, but the hard-close query can omit or reclassify the historical P&L population. That can suppress the closing journal and retained-earnings transfer even though the journal and its role evidence are unchanged.

## Authority and invariant

The historical classification of an already-posted journal line is `accounting_core.journal_entry_line.account_role_code`. The effective-dated `account_role_mapping` is an Accounting Policy catalog used when a proposal is resolved for posting. It is not an authority for retrospectively reclassifying immutable posted facts.

Period Close must therefore classify historical source P&L directly from the persisted journal-line role. The close-time lookup of the destination `retained_earnings` account remains a separate current-policy decision and is not changed by this repair.

This is an AIP DDD, temporal-data, and audit-evidence control. It is not a claim that IFRS prescribes a PostgreSQL column, join shape, or implementation mechanism.

## Test-first evidence

Real PostgreSQL RED `ba6be58c3ce4f2dfbe3e6b27f2f3418cd0f71548` posts a `usage_revenue` line, expires the catalog mapping after posting, verifies the immutable journal line still carries `usage_revenue`, then hard-closes the period. The acceptance requires one closing journal and exact transfer from `410100` into retained earnings `310100`.

Static RED `8dabaee5b43c24810f54479d5180df54220abb0d` pins the causal source boundary: `_post_closing_journal()` must select/filter/group by `journal_entry_line.account_role_code` and must not join `account_role_mapping` for historical P&L classification.

Neither RED is GREEN evidence. Exact-head PostgreSQL/Accounting Foundation execution is required after the production source repair.

## Selected repair

Within `_post_closing_journal()` only:

- select `journal_entry_line.account_role_code`;
- filter `journal_entry_line.account_role_code IN ('usage_revenue', 'write_off_expense')`;
- group by `chart_account.chart_account_code, journal_entry_line.account_role_code`;
- remove the `account_role_mapping` join from that historical source query.

Do not change the posting-time effective-dated resolver, the immutable journal-line schema, reversal role preservation, or the close-time retained-earnings destination lookup.

## Rejected alternatives

Keeping the current catalog join is rejected because later master-data changes can rewrite hard-close semantics without changing the posted journal. Keeping expired mappings artificially current is rejected because it corrupts effective-dated policy resolution and can introduce multiple simultaneously effective mappings. Reconstructing the historical role from the present chart of accounts is rejected because the posted line already contains the authoritative fact. Weakening close or snapshot invariants is rejected because it would hide the misclassification rather than remove it.

## Follow-up boundary

Reporting paths that project historical journal semantics from current `account_role_mapping` are a separate Reporting-Export repair lane. They must consume immutable posted roles or immutable close snapshots rather than importing the Period Close fix as a second authority. Canonical product-gap, CHANGELOG, and standard-traceability wording remains the PR #37 single-writer responsibility.
