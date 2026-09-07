"""Concurrency contract for reconciliation lifecycle authority snapshots."""

from __future__ import annotations

import contextlib
import unittest
from uuid import UUID

from accounting_information_platform import reconciliation_lifecycle


class _Connection:
    """Record transaction and SQL boundaries without emulating accounting rows."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def execute(self, statement: str, _parameters: object = None) -> "_Connection":
        normalized = " ".join(statement.split())
        self.events.append(("execute", normalized))
        return self

    def fetchone(self) -> tuple[bool]:
        return (True,)

    def commit(self) -> None:
        self.events.append(("commit", ""))

    def rollback(self) -> None:
        self.events.append(("rollback", ""))


class _Ledger:
    """Expose the same command-lock shape used by the PostgreSQL adapter."""

    def __init__(self) -> None:
        self.connection = _Connection()
        self._tenant_reference = "tenant-1"

    @contextlib.contextmanager
    def _session(self):
        yield self.connection

    def _acquire_command_lock(self, connection: _Connection, command_scope: str) -> None:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
            (self._tenant_reference, command_scope),
        )


class ReconciliationLifecycleSnapshotLockOrderTests(unittest.TestCase):
    """Keep lock admission outside the repeatable-read authority snapshot."""

    def test_session_lock_precedes_fresh_repeatable_read_snapshot(self) -> None:
        """A waiting advisory lock must not freeze the authority snapshot before admission."""
        self.assertTrue(
            hasattr(reconciliation_lifecycle, "_lifecycle_authority_session"),
            "lifecycle authority needs a pre-snapshot session-lock boundary",
        )
        ledger = _Ledger()
        run_id = UUID("00000000-0000-0000-0000-000000000043")

        with reconciliation_lifecycle._lifecycle_authority_session(ledger, run_id):
            ledger.connection.events.append(("authority-read", ""))

        events = ledger.connection.events
        session_lock = next(
            index
            for index, event in enumerate(events)
            if event[0] == "execute" and "pg_advisory_lock(" in event[1]
        )
        acquisition_commit = next(
            index for index, event in enumerate(events) if index > session_lock and event[0] == "commit"
        )
        repeatable_read = next(
            index
            for index, event in enumerate(events)
            if event[0] == "execute" and "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ" in event[1]
        )
        transaction_lock = next(
            index
            for index, event in enumerate(events)
            if event[0] == "execute" and "pg_advisory_xact_lock(" in event[1]
        )
        authority_read = events.index(("authority-read", ""))
        unlock = next(
            index
            for index, event in enumerate(events)
            if event[0] == "execute" and "pg_advisory_unlock(" in event[1]
        )

        self.assertLess(session_lock, acquisition_commit)
        self.assertLess(acquisition_commit, repeatable_read)
        self.assertLess(repeatable_read, transaction_lock)
        self.assertLess(transaction_lock, authority_read)
        self.assertLess(authority_read, unlock)


if __name__ == "__main__":  # pragma: no cover - direct test execution convenience
    unittest.main()
