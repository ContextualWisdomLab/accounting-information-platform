# Period Close posted chart-account identity traceability

Status: Proposed on PR #53. This note records a live repair finding and does not claim protected integration, runner GREEN, release readiness, audit assurance, or an IFRS-prescribed PostgreSQL design.

## Problem

A posted journal line retains both `journal_entry_line.chart_account_id` and `journal_entry_line.account_role_code` as immutable accounting facts. Effective-dated chart-account and policy catalogs may legitimately change after posting. Hard close must therefore preserve the identity of the historical P&L account that actually owns the posted balance.

PR #53 already stopped `_post_closing_journal()` from reclassifying historical P&L through the current `account_role_mapping`, but the current implementation still selects only `chart_account_code` plus the posted role. `_insert_journal()` then resolves that code again through `chart_account.valid_to IS NULL`. If the source chart account expires after posting but before hard close, the closing command can be stranded. If a later catalog version reuses the same code, resolving only today's code can redirect the closing offset to a different `chart_account_id`; the old posted account would not be zeroed on its own identity.

The retained snapshot hash has the same identity blind spot in a more explicit form. `_canonical_snapshot_hash()` accepts each line as `(chart_account_id, chart_account_code, debit, credit)` but currently omits `chart_account_id` from the serialized hash payload. Two different effective-dated account Entities with the same code and amounts therefore produce the same retained-evidence digest even though `trial_balance_line.chart_account_id` distinguishes them. Provenance must bind the exact Entity that owns the balance, not merely its reusable display/business code.

The hard-close preflight also assembles buyer-facing financial-statement projections before persistence. Those projections still use current effective chart-account / role mappings. A mutable Reporting-Export projection must not become a prerequisite authority that prevents Period Close from freezing otherwise valid immutable ledger facts.

## Constraints

- Posted journal headers and lines remain append-only; correction uses reversal/replacement rather than mutation.
- Ordinary new postings continue to require an active chart account. The repair must not globally relax `_insert_journal()` or allow callers to target expired accounts.
- The system-generated period-closing journal is purpose-limited. Its source-side contra lines exist to zero the exact historical temporary-account balances being closed.
- The retained-earnings destination is a new close-time accounting decision and therefore continues to use the current effective `retained_earnings` mapping.
- Retained snapshot provenance must remain deterministic for exact replay but change when the authoritative account Entity changes even if code and monetary values are identical.
- Billing or another foreign system gains no chart-account, posting, or close authority.

## Alternatives

1. Forbid chart-account expiry until every affected period hard-closes. Rejected: this couples master-data lifecycle to close timing and turns a reporting/catalog condition into ledger availability.
2. Auto-create an active alias or successor row for an expired code. Rejected: code equality does not prove account identity and may leave the historical account balance unclosed.
3. Route closing offsets to whichever active account currently has the same code. Rejected: a successor `chart_account_id` is a different Entity; posting the contra there does not zero the immutable source account.
4. Carry exact posted `chart_account_id` through the close source and use a purpose-limited historical-account insertion path only for the system closing journal. Selected direction. Ordinary posting keeps current-catalog admission, while retained earnings keeps current close-time policy resolution.

The closeability check should depend on authoritative ledger/trial-balance facts rather than on successful construction of mutable buyer-report projections. Reporting can expose its own catalog-completeness error without acquiring veto authority over the close aggregate.

## Executable evidence

Test-first real PostgreSQL RED `6faea7dc50cb2421604daf7c10f7ad3aeadfd4cb` posts `usage_revenue`, records its immutable source `chart_account_id`, expires that chart account, and requires hard close to finish with the source-side closing line bound to the same account identity and the retained snapshot zeroed on that identity.

The exact RED head `06342053f2937e94748b40ed9182b20cfbf0ef74` subsequently reached a real hosted runner. Accounting Foundation run `33979966465`, job `101343324928`, completed `failure` in the behavior/repository-test step while its exact-head dependency-diff, SAST, and security sibling jobs completed successfully. Raw step logs are not available through the current repository connector, so this note does not attribute that failure to one assertion beyond the checked-in RED contracts. It is nevertheless runner-observed failure evidence for the unchanged RED head, not a queued or synthetic result.

Successor RED `c5266ce29c181474331e8a4b035f6d57d185ed2d` strengthens the temporal Entity boundary with an actual code-reuse transition. It posts revenue to one `chart_account_id`, ends that account and its role mapping, creates a successor account that legally reuses `410100` with a later `valid_from`, installs the successor mapping, and then requires hard close to put the historical contra on the original account rather than the successor. This separates code equality from Entity identity and prevents a repair that merely makes the current code lookup succeed again.

Hash-provenance RED `ae2aaa1ad9c75068e5db4dc9850f40ebba991137` isolates the retained-evidence digest. It supplies identical tenant/entity/book/period/currency/journal-count/code/amount inputs with two different deterministic `chart_account_id` values. Exact replay of one Entity must hash identically, while substituting only the account Entity must change the digest. The current implementation fails that contract because it discards the UUID while serializing the line payload.

Static RED `a612539a92bfd171b7037273858c7263e4eabc9e` simultaneously preserves the opposite boundary: ordinary `_insert_journal()` must continue requiring `valid_to IS NULL`, while `_post_closing_journal()` must carry and group the exact posted `journal_entry_line.chart_account_id` instead of reconstructing source identity from a current code lookup.

Static authority-separation RED `0d3caba036c370f93deacc0e8208314cd9df9731` adds the second causal boundary exposed by the same PostgreSQL scenario: `close_fiscal_period()` must not require `_assemble_period_close_package()` before the authoritative hard-close write. The supported close still has to reach `_persist_period_close()` and preserve ledger/trial-balance invariants; buyer Reporting-Export projection completeness is not close authorization.

The current successor head is intentionally RED until a production candidate satisfies both realistic PostgreSQL scenarios, the account-identity-sensitive snapshot hash, and the static separation contracts on one unchanged exact head. Predecessor runner evidence does not transfer to that successor head.

## Standards boundary

This repair implements AIP's DDD aggregate/entity identity, temporal accounting-evidence, append-only ledger, and auditability controls. IFRS Accounting Standards do not prescribe PostgreSQL identifiers, joins, triggers, hashes, or this insertion shape. Standards traceability should cite financial-reporting requirements only for the accounting outcome they support and keep implementation controls explicitly repository-owned.

## Recovery and rollout

No migration or historical journal rewrite is authorized by this finding. If a deployment encounters a period whose current catalog no longer exposes a historical P&L account, do not reactivate or remap the account merely to make close pass. Preserve the posted identity, apply the verified close repair through the normal release path, and rerun the same immutable close command identity from a fresh transaction.

A future hash fix changes newly produced retained-evidence digests and therefore requires explicit compatibility/recovery evidence before release. Existing committed hard-close evidence must not be silently rehashed in place. If historical digest versioning is required, introduce it as a versioned evidence contract with migration/verification rules rather than mutating already-retained facts.

Rollback of a future candidate is safe only before it produces new hard-close evidence under that candidate. Once a hard-close snapshot and closing journal have committed, accounting correction follows the repository's reversal/correction and audited migration rules; source facts are not rewritten to emulate a code rollback.

## Downstream handoff

PR #37 owns the canonical CHANGELOG, standards table, and `docs/product-technical-gap-baseline.md`. At integration time it should reconstruct the durable invariant: effective-dated chart-account or role changes cannot retrospectively change or strand the immutable account identity used by Period Close, and retained evidence hashes must bind that exact Entity identity.

PR #52 / Reporting-Export must consume retained account identity from integrated AIP evidence. It must not infer historical account identity solely from the current chart-account or role catalog, and reporting catalog incompleteness must not manufacture a second Period Close authority.
