"""Concurrency contract for reconciliation lifecycle authority snapshots."""

from __future__ import annotations

import contextlib
import unittest
from uuid import UUID

from accounting_information_platform import reconciliation_lifecycle
from accounting_information_platform.core import AccountingValidationError


class _Connection:
    """Record transaction and SQL boundaries without emulating accounting rows."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []
        self.fail_session_lock = False
        self.unlock_result: tuple[bool] | None = (True,)

    def execute(self, statement: str, _parameters: object = None) -> "_Connection":
        normalized = " ".join(statement.split())
        self.events.append(("execute", normalized))
        if self.fail_session_lock and "pg_advisory_lock(" in normalized:
            raise RuntimeError("session lock acquisition failed")
        return self

    def fetchone(self) -> tuple[bool] | None:
        return self.unlock_result

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
        authority_commit = next(
            index for index, event in enumerate(events) if index > authority_read and event[0] == "commit"
        )
        unlock = next(
            index
            for index, event in enumerate(events)
            if event[0] == "execute" and "pg_advisory_unlock(" in event[1]
        )

        self.assertLess(session_lock, acquisition_commit)
        self.assertLess(acquisition_commit, repeatable_read)
        self.assertLess(repeatable_read, transaction_lock)
        self.assertLess(transaction_lock, authority_read)
        self.assertLess(authority_read, authority_commit)
        self.assertLess(authority_commit, unlock)

    def test_session_lock_acquisition_failure_rolls_back_without_unlock_attempt(self) -> None:
        """A failed admission writes nothing and does not pretend to release an unowned lock."""
        ledger = _Ledger()
        ledger.connection.fail_session_lock = True

        with self.assertRaisesRegex(RuntimeError, "session lock acquisition failed"):
            with reconciliation_lifecycle._lifecycle_authority_session(
                ledger, UUID("00000000-0000-0000-0000-000000000043")
            ):
                self.fail("authority body must not run without lock admission")

        self.assertIn(("rollback", ""), ledger.connection.events)
        self.assertFalse(
            any("pg_advisory_unlock(" in event[1] for event in ledger.connection.events)
        )

    def test_authority_error_rolls_back_before_releasing_session_lock(self) -> None:
        """Failed authority work releases the session lease only after transaction rollback."""
        ledger = _Ledger()

        with self.assertRaisesRegex(ValueError, "authority failed"):
            with reconciliation_lifecycle._lifecycle_authority_session(
                ledger, UUID("00000000-0000-0000-0000-000000000043")
            ):
                raise ValueError("authority failed")

        rollback = ledger.connection.events.index(("rollback", ""))
        unlock = next(
            index
            for index, event in enumerate(ledger.connection.events)
            if event[0] == "execute" and "pg_advisory_unlock(" in event[1]
        )
        self.assertLess(rollback, unlock)

    def test_missing_owned_session_lock_fails_closed_after_successful_authority_work(self) -> None:
        """An unexpected false unlock result cannot be reported as a successful command return."""
        ledger = _Ledger()
        ledger.connection.unlock_result = (False,)

        with self.assertRaisesRegex(AccountingValidationError, "could not be released"):
            with reconciliation_lifecycle._lifecycle_authority_session(
                ledger, UUID("00000000-0000-0000-0000-000000000043")
            ):
                pass

        self.assertGreaterEqual(ledger.connection.events.count(("rollback", "")), 1)

    def test_cleanup_failure_does_not_replace_original_authority_error(self) -> None:
        """Cleanup diagnostics must preserve the accounting failure that caused rollback."""
        ledger = _Ledger()
        ledger.connection.unlock_result = None

        with self.assertRaisesRegex(ValueError, "authority failed"):
            with reconciliation_lifecycle._lifecycle_authority_session(
                ledger, UUID("00000000-0000-0000-0000-000000000043")
            ):
                raise ValueError("authority failed")

        self.assertGreaterEqual(ledger.connection.events.count(("rollback", "")), 2)


if __name__ == "__main__":  # pragma: no cover - direct test execution convenience
    unittest.main()
