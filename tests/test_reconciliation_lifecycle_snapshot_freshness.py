"""Regression for reconciliation lifecycle visibility after advisory-lock waits."""

from __future__ import annotations

import contextlib
import unittest
import unittest.mock as mock

from accounting_information_platform import reconciliation_lifecycle as lifecycle


class _StopAfterLifecycleLock(RuntimeError):
    """Stop the focused test immediately before the first authority read."""


class _RecordingConnection:
    """Record transaction and session-lock SQL without emulating authority queries."""

    def __init__(self) -> None:
        """Initialize ordered SQL and transaction-boundary records."""
        self.statements: list[str] = []
        self.commit_count = 0
        self.rollback_count = 0

    def execute(self, query: str, parameters: tuple[object, ...] = ()) -> object:
        """Record one SQL statement; later authority behavior is unreachable."""
        del parameters
        self.statements.append(" ".join(query.split()))
        return object()

    def commit(self) -> None:
        """Record an explicit commit while preserving the session advisory lock."""
        self.commit_count += 1

    def rollback(self) -> None:
        """Record rollback of the authority transaction after the sentinel failure."""
        self.rollback_count += 1


class _FreshSnapshotLedger:
    """Stop after the transaction lock so snapshot ordering can be observed exactly."""

    connection = _RecordingConnection()
    locks: list[str] = []

    def __init__(self, database_url: str, tenant_reference: str) -> None:
        """Retain constructor inputs only to match the production adapter boundary."""
        self.database_url = database_url
        self.tenant_reference = tenant_reference

    @contextlib.contextmanager
    def _session(self):
        """Yield the recording connection as one PostgreSQL session."""
        yield type(self).connection

    def _acquire_command_lock(self, _connection: object, scope: str) -> None:
        """Record the transaction advisory-lock scope selected after session lease."""
        type(self).locks.append(scope)

    def _require_tenant(self, _connection: object) -> object:
        """Stop before any authority read after both lifecycle locks are held."""
        raise _StopAfterLifecycleLock


class ReconciliationLifecycleSnapshotFreshnessTests(unittest.TestCase):
    """Keep a waiting finalizer from pinning a pre-lock repeatable-read snapshot."""

    def test_session_lease_precedes_fresh_repeatable_read_authority_transaction(self) -> None:
        """The coherent snapshot must begin only after database-owned lock acquisition commits."""
        _FreshSnapshotLedger.connection = _RecordingConnection()
        _FreshSnapshotLedger.locks = []
        command = {
            "tenant_reference": "urn:cwl:tenant:test",
            "reconciliation_action_code": "reconcile",
            "reconciliation_run_id": "11111111-1111-1111-1111-111111111111",
            "reconciliation_idempotency_key": "reconcile-after-wait",
            "actor_reference": "urn:cwl:principal:controller",
            "purpose_code": "month_end_reconciliation",
            "effective_at": "2026-09-02T00:00:00Z",
        }

        with mock.patch.object(
            lifecycle, "PostgresPostingLedger", _FreshSnapshotLedger
        ):
            with self.assertRaises(_StopAfterLifecycleLock):
                lifecycle.reconcile_reconciliation_run(
                    command,
                    "postgresql://example",
                    "urn:cwl:tenant:test",
                )

        self.assertEqual(
            _FreshSnapshotLedger.connection.statements,
            [
                "SELECT accounting_core.acquire_reconciliation_lifecycle_session(%s, %s)",
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ",
                "SELECT accounting_core.release_reconciliation_lifecycle_session(%s, %s)",
            ],
        )
        self.assertEqual(_FreshSnapshotLedger.connection.commit_count, 2)
        self.assertEqual(_FreshSnapshotLedger.connection.rollback_count, 1)
        self.assertEqual(
            _FreshSnapshotLedger.locks,
            ["reconciliation_run_lifecycle:11111111-1111-1111-1111-111111111111"],
        )


if __name__ == "__main__":
    unittest.main()
