"""Regression for reconciliation lifecycle visibility after advisory-lock waits."""

from __future__ import annotations

import contextlib
import unittest
import unittest.mock as mock

from accounting_information_platform import reconciliation_lifecycle as lifecycle


class _StopAfterLifecycleLock(RuntimeError):
    """Stop the focused test immediately after the run lifecycle lock is acquired."""


class _RecordingConnection:
    """Record transaction-control SQL without emulating later accounting queries."""

    def __init__(self) -> None:
        """Initialize an empty ordered SQL record."""
        self.statements: list[str] = []

    def execute(self, query: str, parameters: tuple[object, ...] = ()) -> object:
        """Record one SQL statement; later query behavior is intentionally unreachable."""
        del parameters
        self.statements.append(" ".join(query.split()))
        return object()


class _FreshSnapshotLedger:
    """Stop after the lifecycle lock so the isolation contract can be observed exactly."""

    connection = _RecordingConnection()
    locks: list[str] = []

    def __init__(self, database_url: str, tenant_reference: str) -> None:
        """Retain constructor inputs only to match the production adapter boundary."""
        self.database_url = database_url
        self.tenant_reference = tenant_reference

    @contextlib.contextmanager
    def _session(self):
        """Yield the recording connection as one transaction."""
        yield type(self).connection

    def _acquire_command_lock(self, _connection: object, scope: str) -> None:
        """Record the advisory-lock scope selected by the lifecycle command."""
        type(self).locks.append(scope)

    def _require_tenant(self, _connection: object) -> object:
        """Stop before any authority read after the lifecycle lock."""
        raise _StopAfterLifecycleLock


class ReconciliationLifecycleSnapshotFreshnessTests(unittest.TestCase):
    """Keep a waiting finalizer from pinning a pre-wait repeatable-read snapshot."""

    def test_lifecycle_lock_uses_read_committed_before_authority_reads(self) -> None:
        """Subsequent statements must see commits completed while the run lock was awaited."""
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
            ["SET TRANSACTION ISOLATION LEVEL READ COMMITTED"],
        )
        self.assertEqual(
            _FreshSnapshotLedger.locks,
            ["reconciliation_run_lifecycle:11111111-1111-1111-1111-111111111111"],
        )


if __name__ == "__main__":
    unittest.main()
