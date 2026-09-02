# Reconciliation outbox retention authority — 2026-09-02

## Problem

Migration `0021_reconciliation_exception_resolution_outbox_pair.sql` made command, terminal accounting state, and the matching outbox event commit atomically. That commit-time constraint did not protect the relationship afterward. A privileged SQL writer could delete the already-committed reconciliation outbox row, re-key its tenant/type/reference/hash identity, insert a second exact match, or update an unrelated row into the same authority identity. The first two operations detach immutable command authority from its delivery/audit evidence; the latter two leave one immutable command with ambiguous duplicate evidence.

The supported outbox publication boundary does not require any of those operations. Publication changes only `published_at`; the tenant, event type, aggregate reference, payload reference, and payload hash remain the event identity.

## Constraints

- Preserve ordinary `publish_outbox_event` semantics and idempotent `published_at` updates.
- Do not make every accounting outbox event globally immutable as an incidental child-PR policy expansion.
- Protect both maker-checker exception-resolution authority and reconciliation-run lifecycle authority.
- Allow an explicitly controlled same-transaction replacement only when the final database state still contains exactly one matching event.
- Fail a forward migration over a database whose reconciliation authority was already detached or duplicated after migration 0021; a new trigger must not bless damaged provenance.
- Keep PostgreSQL, not application convention, as the final invariant owner for direct SQL and privileged maintenance paths.

## Alternatives considered

1. **Application-only delete/update/insert checks.** Rejected because privileged SQL and future maintenance jobs can bypass the application boundary.
2. **Block all updates to `accounting_integration.outbox_event`.** Rejected because publication legitimately updates `published_at` and ADR 0017 defines that operation as the supported delivery acknowledgement.
3. **Make the entire outbox append-only in this slice.** Rejected as a broader accounting-platform decision that needs its own repository-wide lifecycle/retention ADR and migration plan.
4. **Only add future-row triggers.** Rejected because a database damaged between migrations 0021 and 0022 would install successfully while already violating the intended authority contract.
5. **Use only a global outbox uniqueness constraint.** Rejected for this bounded repair because the invariant is scoped to reconciliation authority linkage; imposing a new repository-wide uniqueness policy would change unrelated event families without an ADR proving that broader ownership contract.

## Selected repair

The original RED regression commit `a82e5c20eb4df07521d628d1b39e119ce7dd2ac5` added real PostgreSQL tests requiring a committed exception-resolution outbox row to survive later DELETE and identity re-key attempts. Current-head review then identified a second source-real gap: migration 0022 inspected only `OLD` rows and therefore did not prevent a duplicate exact INSERT or an unrelated row being UPDATEd into a committed reconciliation authority identity.

RED commit `657d0284e23930f5bcf9a2318c722e05c3b49cac` extends the real PostgreSQL boundary before production repair. It requires duplicate exact outbox INSERTs to fail for both exception-resolution and lifecycle command families, requires an unrelated row re-keyed into a committed resolution identity to fail, preserves the existing delete/re-key regressions, and proves a `published_at`-only update remains allowed.

Production commit `fe8d7d036fbd139a7fd58e24d0964a73750f1fc5` keeps the invariant bounded to reconciliation authority. Migration `0022_reconciliation_authority_outbox_retention.sql` now factors reconciliation identity validation into `accounting_core.assert_reconciliation_authority_outbox_identity`. The deferred trigger boundary validates `OLD` authority identity on DELETE and identity UPDATE, and validates `NEW` authority identity on INSERT and identity UPDATE. It therefore rejects both losing the sole matching event and creating a second exact match. `published_at` remains outside the identity-trigger column set, so publication is unchanged.

The same migration performs a forward preflight over all existing reconciliation exception-resolution and lifecycle commands. Installation fails with `reconciliation_authority_outbox_retention_preflight` if any command lacks exactly one matching event. Operators must restore or reconstruct verified provenance before continuing; the migration does not synthesize evidence.

The canonical public installer requires 0022 after the parent database-authority overlay, 0020, and 0021. Loader tests model that complete chain. Lifecycle retention coverage is included so both command families exercise the database invariant.

## Failure and operations scenarios

- **Normal publisher:** updating only `published_at` succeeds; accounting authority and event identity are unchanged.
- **Accidental cleanup DELETE:** commit fails when the row is bound to a reconciliation authority command.
- **Identity re-key away from authority:** changing linkage fields fails unless the same transaction leaves exactly one event matching the immutable command.
- **Duplicate exact INSERT:** adding a second row with the same reconciliation authority linkage fails at commit.
- **Re-key into authority:** an unrelated outbox row cannot be changed into a duplicate reconciliation authority identity.
- **Damaged upgrade source:** migration 0022 refuses installation when a pre-existing command has zero or multiple matching events. Restore verified provenance or stop the upgrade; do not fabricate an event merely to satisfy the check.
- **Unrelated outbox rows:** this migration does not grant new semantics or retention policy to posting, reversal, period-close, or other event families.

## Evidence boundary

Relevant commits in the current writer lineage are:

- `a82e5c20eb4df07521d628d1b39e119ce7dd2ac5` — original RED PostgreSQL delete/re-key retention regression.
- `b754a620240e7a3cc101be0f834b711be652fe54` — original production outbox retention migration.
- `f0932938e4ab8f4cffb755f11cf1a16916a23ffd` — canonical installer includes migration 0022.
- `6e5bf8e2d212b82d17547ebfb10e2233eaf7232d` and `ccbdff4696e4d486c1003ff2f54c3621bfa8e214` — migration-loader contract repairs.
- `80a68036347a5aa9b22bd886347a306e77f72819` — lifecycle-side retention regression coverage.
- `3ce802d59b9cc46b40fa0e43f5faba6ecbd219f5` — existing-authority migration preflight.
- `657d0284e23930f5bcf9a2318c722e05c3b49cac` — RED duplicate INSERT, re-key-into-authority, and publication-metadata regressions.
- `fe8d7d036fbd139a7fd58e24d0964a73750f1fc5` — PostgreSQL `OLD` + `NEW` authority identity enforcement with deferred INSERT/DELETE/identity-UPDATE guards.

These commits are mutable PR evidence, not protected-branch or release evidence. Exact-head PostgreSQL, coverage, security, dependency, review, package/SBOM/provenance, documentation alignment, and parent-stack integration gates must still pass before this authority can be described as shipped.

## Follow-up

A future repository-wide outbox retention/archival design should decide whether non-reconciliation event families require the same immutable linkage or a separate durable archive table. That decision must preserve audit lineage, tenant isolation, publication state, backup/restore behavior, and any statutory retention obligations without turning this reconciliation repair into an undocumented global policy change.
