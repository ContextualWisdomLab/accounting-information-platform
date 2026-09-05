# Period Close journal-population fence traceability

## Decision scope

This note traces the concurrency control used when an authoritative journal population approaches fiscal-period hard close. It does not create accounting policy, reopen a closed period, grant Billing posting authority, or make PostgreSQL locking semantics an IFRS requirement.

IAS 10 distinguishes adjusting events that provide evidence about conditions existing at the reporting-period end from non-adjusting events arising later. The product-level implication already adopted by ADR 0006/ADR 0023 is that a governed close must not certify retained balances while an admissible accounting adjustment for that period is concurrently being committed. IAS 10 does **not** prescribe PostgreSQL row locks, MVCC isolation, retry codes, or the `journal_population_revision` design; those are implementation controls chosen by AIP.

## Authoritative technical basis

PostgreSQL 18 row-level `FOR SHARE` is compatible with another `FOR SHARE`/`FOR KEY SHARE`, while blocking `UPDATE`, `DELETE`, `FOR UPDATE`, and `FOR NO KEY UPDATE` on the same row. Row locks are held through transaction end. PostgreSQL also states that a row-lock request in `REPEATABLE READ`/`SERIALIZABLE` fails if the row changed since the transaction began. Serialization failures use SQLSTATE `40001`, and the complete transaction must be retried rather than resuming from the failed statement.

The current PostgreSQL documentation line is PostgreSQL 18, whose current supported minor at this decision date is 18.6. Minor-version operability evidence remains owned by the PostgreSQL runtime-baseline lane; this control relies on PostgreSQL 18 semantics rather than claiming a clean-image bump proves an existing-cluster upgrade.

## Control mapping

| Concern | Chosen control | Rejected alternative | Executable evidence |
|---|---|---|---|
| Ordinary `open` posting throughput | `accounting_core.guard_period_insert()` takes `FOR SHARE` on the exact `accounting_book_period_control` row and does not change `journal_population_revision` | Increment the revision for every journal; this makes one book-period row an exclusive UPDATE hotspot | `tests/test_postgres_open_period_journal_fence.py::test_open_period_posting_does_not_version_close_control_row` |
| Compatible open-path coordination | Multiple open journals may hold the shared fence concurrently; a state-changing UPDATE must wait | No period fence; soft close could overtake a journal admitted under the prior `open` state | `tests/test_postgres_open_period_journal_fence.py::test_open_period_posting_can_progress_while_peer_holds_share_fence` |
| State changes while an open journal waits | Fail with SQLSTATE `40001` (`serialization_failure`) and retry the whole journal command from a fresh transaction | Continue after wait using stale open-state authority | `database/migrations/0032_period_close_journal_population_fence.sql`; static contract in `tests/test_trial_balance_snapshot_immutability_contract.py` |
| `soft_closed` adjusting/reversal/closing journal freshness | Purpose-limited close-window journals increment `journal_population_revision` on the exact tenant/book/period row in the journal transaction | Advisory-lock wait only; a pre-existing `REPEATABLE READ` snapshot would remain stale after lock grant | `tests/test_postgres_period_close_journal_serialization_red.py` |
| Stale hard close | PostgreSQL serialization failure rolls the close transaction back before retained population/hard-close state becomes authoritative; retry the same close idempotency key from a fresh snapshot | Freeze older live totals or normalize the conflict into success | `tests/test_postgres_period_close_journal_serialization_red.py` retained/live amount comparison |
| Capability | Replacement guard remains `SECURITY DEFINER`, fixes `search_path = pg_catalog, pg_temp`, and revokes PUBLIC execute | Caller GUC alone as authorization | `database/migrations/0032_period_close_journal_population_fence.sql`; `tests/test_trial_balance_snapshot_immutability_contract.py` |

## TDD lineage

- Journal/close stale-population RED: `306f4c14212a0dfbb89a6934bbb493b1e179479e`.
- First freshness candidate through `84f5666aa48ba565fb2e4ff763bb0b3ee27fe857` invalidated stale close by updating the control-row revision for every journal.
- Open-period hotspot RED: `6fe1fdeb1050111b26e557810dbf66b05f75871a`.
- Split-lock causal repair: `7a979845896869ef0e7fabab710c7a4f3a9863de`.
- Static lock-profile ratchet: `37d4f513c2740b644e45bd7d05bbdadb7e4dbad7`.
- ADR alignment: `815f7055b8e6ddbd470904a4abbe6862e5e160be`.
- Real-PostgreSQL compatible-shared-lock regression: `2bdb09a6e7da1444a2356d94ae7fde16d9d40686`.

These commits are development evidence, not protected-head release evidence. Exact-head CI, independent review, central workflow gates, package/SBOM/provenance, migration/recovery verification, and measured performance must be reacquired after every head change.

## Residual risk and release acceptance

`FOR SHARE` avoids the deliberate per-journal exclusive row UPDATE, but row locking itself is not free and PostgreSQL notes that row locking can cause disk writes. This repair therefore establishes a concurrency-control shape and a realistic non-blocking regression; it does not prove the buyer-path p95 ≤ 20 ms target. Release acceptance still requires measured concurrent posting/period-transition load at the exact candidate head, with lock waits and tail latency reported rather than hidden by cache warm-up, reduced samples, or excluded failures.

A `40001` result is not accounting evidence. The calling command must retry its entire transaction and preserve the same immutable source-payload identity/idempotency contract. Recovery must never rewrite posted journals, retained trial-balance evidence, or reconciliation authority rows to make a failed close appear successful.

## References

IFRS Foundation. (n.d.). *IAS 10 Events after the Reporting Period*. https://www.ifrs.org/issued-standards/list-of-standards/ias-10-events-after-the-reporting-period.html/content/

PostgreSQL Global Development Group. (2026a). *PostgreSQL 18 documentation: Explicit locking*. https://www.postgresql.org/docs/18/explicit-locking.html

PostgreSQL Global Development Group. (2026b). *PostgreSQL 18 documentation: Serialization failure handling*. https://www.postgresql.org/docs/18/mvcc-serialization-failure-handling.html

PostgreSQL Global Development Group. (2026c). *PostgreSQL documentation*. https://www.postgresql.org/docs/
