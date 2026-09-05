# Period Close posted chart-account identity traceability

Status: Proposed on PR #53. This note records a live repair finding and does not claim protected integration, runner GREEN, release readiness, audit assurance, or an IFRS-prescribed PostgreSQL design.

## Problem

A posted journal line retains both `journal_entry_line.chart_account_id` and `journal_entry_line.account_role_code` as immutable accounting facts. Effective-dated chart-account and policy catalogs may legitimately change after posting. Hard close must therefore preserve the identity of the historical P&L account that actually owns the posted balance.

PR #53 already stopped `_post_closing_journal()` from reclassifying historical P&L through the current `account_role_mapping`, but the current implementation still selects only `chart_account_code` plus the posted role. `_insert_journal()` then resolves that code again through `chart_account.valid_to IS NULL`. If the source chart account expires after posting but before hard close, the closing command can be stranded. If a later catalog version reuses the same code, resolving only today's code can redirect the closing offset to a different `chart_account_id`; the old posted account would not be zeroed on its own identity.

The hard-close preflight also assembles buyer-facing financial-statement projections before persistence. Those projections still use current effective chart-account / role mappings. A mutable Reporting-Export projection must not become a prerequisite authority that prevents Period Close from freezing otherwise valid immutable ledger facts.

## Constraints

- Posted journal headers and lines remain append-only; correction uses reversal/replacement rather than mutation.
- Ordinary new postings continue to require an active chart account. The repair must not globally relax `_insert_journal()` or allow callers to target expired accounts.
- The system-generated period-closing journal is purpose-limited. Its source-side contra lines exist to zero the exact historical temporary-account balances being closed.
- The retained-earnings destination is a new close-time accounting decision and therefore continues to use the current effective `retained_earnings` mapping.
- Billing or another foreign system gains no chart-account, posting, or close authority.

## Alternatives

1. Forbid chart-account expiry until every affected period hard-closes. Rejected: this couples master-data lifecycle to close timing and turns a reporting/catalog condition into ledger availability.
2. Auto-create an active alias or successor row for an expired code. Rejected: code equality does not prove account identity and may leave the historical account balance unclosed.
3. Route closing offsets to whichever active account currently has the same code. Rejected: a successor `chart_account_id` is a different Entity; posting the contra there does not zero the immutable source account.
4. Carry exact posted `chart_account_id` through the close source and use a purpose-limited historical-account insertion path only for the system closing journal. Selected direction. Ordinary posting keeps current-catalog admission, while retained earnings keeps current close-time policy resolution.

The closeability check should depend on authoritative ledger/trial-balance facts rather than on successful construction of mutable buyer-report projections. Reporting can expose its own catalog-completeness error without acquiring veto authority over the close aggregate.

## Executable evidence

Test-first real PostgreSQL RED `6faea7dc50cb2421604daf7c10f7ad3aeadfd4cb` posts `usage_revenue`, records its immutable source `chart_account_id`, expires that chart account, and requires hard close to finish with the source-side closing line bound to the same account identity and the retained snapshot zeroed on that identity.

Static RED `a612539a92bfd171b7037273858c7263e4eabc9e` simultaneously preserves the opposite boundary: ordinary `_insert_journal()` must continue requiring `valid_to IS NULL`, while `_post_closing_journal()` must carry and group the exact posted `journal_entry_line.chart_account_id` instead of reconstructing source identity from a current code lookup.

On the current RED head these requirements are not satisfied by production source. Exact-head CI must execute before the RED is called runner-observed. A later candidate must prove both the realistic PostgreSQL scenario and the static separation contract on one unchanged head.

## Standards boundary

This repair implements AIP's DDD aggregate/entity identity, temporal accounting-evidence, append-only ledger, and auditability controls. IFRS Accounting Standards do not prescribe PostgreSQL identifiers, joins, triggers, or this insertion shape. Standards traceability should cite financial-reporting requirements only for the accounting outcome they support and keep implementation controls explicitly repository-owned.

## Recovery and rollout

No migration or historical journal rewrite is authorized by this finding. If a deployment encounters a period whose current catalog no longer exposes a historical P&L account, do not reactivate or remap the account merely to make close pass. Preserve the posted identity, apply the verified close repair through the normal release path, and rerun the same immutable close command identity from a fresh transaction.

Rollback of a future candidate is safe only before it produces new hard-close evidence under that candidate. Once a hard-close snapshot and closing journal have committed, accounting correction follows the repository's reversal/correction and audited migration rules; source facts are not rewritten to emulate a code rollback.

## Downstream handoff

PR #37 owns the canonical CHANGELOG, standards table, and `docs/product-technical-gap-baseline.md`. At integration time it should reconstruct the durable invariant: effective-dated chart-account or role changes cannot retrospectively change or strand the immutable account identity used by Period Close.

PR #52 / Reporting-Export must consume retained account identity from integrated AIP evidence. It must not infer historical account identity solely from the current chart-account or role catalog, and reporting catalog incompleteness must not manufacture a second Period Close authority.
