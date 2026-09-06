# Soft-close command-evidence pair traceability

## Problem

`accounting_book_period_control` is the authoritative per-book period state. Migration 0010 made the three soft-close evidence fields internally all-or-none, but it did not require a row whose `period_status_code` is `soft_closed` to retain that evidence. Migration 0009 can also predate that evidence model and copy a shared fiscal-period `soft_closed` projection into a book-period control row. The result is a one-sided authoritative close fact that the application later refuses to replay because the original command identity, source hash, and source journal count are absent.

This is an Accounting Information Platform DDD/database integrity control. IFRS Accounting Standards do not prescribe this PostgreSQL trigger design.

## Constraints

The supported soft-close command changes the exact book-period status and writes `soft_close_idempotency_key`, `soft_close_source_payload_hash`, and `soft_close_source_journal_count` as two statements inside one transaction. A valid repair therefore cannot require all evidence on the first statement image. It must inspect the final retained row at commit.

Historical evidence must not be invented from the current ledger population. An unverifiable pre-migration `soft_closed` row must not be silently reopened either; that would rewrite an authoritative accounting-control fact without proving the original decision.

The repair must preserve tenant FORCE RLS, the existing 64-row journal-population freshness fence, exact hard-close/snapshot pairing, ordinary journal admission, and the application-owned idempotent soft-close command.

## Alternatives considered

- **Reset legacy incomplete rows to `open`.** Rejected because it rewrites an existing close state without proving that reopening was authorized.
- **Derive missing evidence from the later journal population.** Rejected because later state cannot prove the original command identity or source population observed when soft-close occurred.
- **Immediate `BEFORE`/`AFTER` validation of the transition row image.** Rejected because the canonical command intentionally writes state and evidence in separate statements of one transaction.
- **Caller-controlled GUC or application-only validation.** Rejected because it would weaken the database single-writer/fail-closed boundary and leave direct SQL capable of retaining an incomplete authority fact.

## Decision

Migration `0037_soft_close_command_evidence_pair.sql` adds two controls:

1. An upgrade preflight temporarily grants only the migration role all-tenant SELECT visibility on the FORCE-RLS control relation and fails with `soft_close_command_evidence_pair_legacy_preflight` when any pre-existing `soft_closed` row lacks one of the three durable evidence values. The temporary policy is removed before durable trigger installation.
2. A `DEFERRABLE INITIALLY DEFERRED` constraint trigger fires on transitions into `soft_closed`. At commit it re-reads the exact tenant/book/period row and requires the final retained state to contain the complete command-evidence triplet. Incomplete state fails with `soft_close_command_evidence_pair_required` and rolls the transaction back.

The trigger function is `SECURITY DEFINER` with `search_path = pg_catalog, pg_temp`; PUBLIC execute is revoked. It grants no posting, reopening, hard-close, reporting, Billing, or policy authority.

## RED → repair → ratchet

- RED `efc191611edc8f450ac7a58023d10075d8542f93` adds a real PostgreSQL case for raw `open -> soft_closed` without command evidence and an upgrade case for a pre-existing one-sided soft-close fact.
- Repair `7279f05a6c1d067c25b78b8df10e5b7b99acad0e` adds migration 0037 with the legacy preflight and deferred final-row pair guard.
- Installer `ce91cc19339fb8bfba5fd5b9698b321c59cf5c9e` appends 0037 after the existing 0036 hard-close/snapshot pair migration.
- Static ratchet `c594c78bcfaeca5c2029a21e3ef0b581d6e15fa5` requires the fail-closed markers, deferred semantics, temporary-policy cleanup, PUBLIC revoke, and exact installer order.

Hosted exact-head execution evidence is separate from this source lineage. A predecessor run does not prove the current head.

## Recovery and release effect

If upgrade preflight finds a legacy one-sided soft-close row, stop the migration and retain the prior release/database. Inventory the exact tenant/book/period control and original command/audit/outbox evidence. Proceed only through a separately reviewed audited remediation that can prove the original soft-close command identity and source population. Do not synthesize the hash/count from current balances and do not flip the row to `open` merely to pass migration 0037.

Release evidence must show migration 0037 installs under the production-like non-superuser/non-`BYPASSRLS` migration owner, the canonical soft-close command still commits state and evidence atomically, direct incomplete soft-close rolls back, the legacy preflight is atomic, and no temporary migration policy survives either successful or aborted installation.
