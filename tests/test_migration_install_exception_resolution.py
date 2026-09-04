"""Contracts for installing reconciliation exception-resolution authority."""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from accounting_information_platform import AccountingValidationError
from accounting_information_platform import migration_install


class _Connection:
    """Capture forward migration execution."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.executed: list[str] = []

    def execute(self, statement: str) -> None:
        """Execute or raise the configured database failure."""
        if self.fail:
            raise RuntimeError("forward migration failed")
        self.executed.append(statement)


class _Psycopg:
    """Minimal psycopg module double for the public install wrapper."""

    ClientCursor = object

    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def connect(self, *_args: object, **_kwargs: object):
        """Yield the configured autocommit connection."""
        return contextlib.nullcontext(self.connection)


class ReconciliationExceptionResolutionInstallTests(unittest.TestCase):
    """Keep the complete reconciliation authority overlay fail-closed and ordered."""

    def _paths(
        self, root: Path
    ) -> tuple[Path, Path, Path, Path, Path, Path, Path, Path]:
        """Create base plus every required reconciliation authority migration path."""
        base = root / "0001_accounting_foundation.sql"
        parent_authority = root / "0019_reconciliation_run_database_snapshot_authority.sql"
        resolution = root / "0020_reconciliation_exception_resolution_command.sql"
        outbox = root / "0021_reconciliation_exception_resolution_outbox_pair.sql"
        retention = root / "0022_reconciliation_authority_outbox_retention.sql"
        orphan_guard = root / "0023_reconciliation_authority_outbox_orphan_guard.sql"
        control_time = root / "0024_reconciliation_control_recording_time_authority.sql"
        lifecycle_time = root / "0025_reconciliation_lifecycle_recording_time_authority.sql"
        base.write_text("BEGIN; COMMIT;", encoding="utf-8")
        parent_authority.write_text("SELECT 'parent authority';", encoding="utf-8")
        return (
            base,
            parent_authority,
            resolution,
            outbox,
            retention,
            orphan_guard,
            control_time,
            lifecycle_time,
        )

    def test_missing_resolution_migration_fails_before_base_install(self) -> None:
        """The public loader cannot silently stop before exception-resolution authority."""
        with tempfile.TemporaryDirectory() as directory:
            (
                base,
                _parent,
                _resolution,
                _outbox,
                _retention,
                _orphan_guard,
                _control_time,
                _lifecycle_time,
            ) = self._paths(Path(directory))
            base_loader = Mock()
            with patch.object(migration_install, "_apply_foundation_migration", base_loader):
                with self.assertRaisesRegex(AccountingValidationError, "0020"):
                    migration_install.apply_foundation_migration(
                        "postgresql://unused", base
                    )
            base_loader.assert_not_called()

    def test_forward_migrations_execute_after_existing_chain_in_order(self) -> None:
        """The exported loader applies every reconciliation authority overlay in order."""
        with tempfile.TemporaryDirectory() as directory:
            (
                base,
                _parent,
                resolution,
                outbox,
                retention,
                orphan_guard,
                control_time,
                lifecycle_time,
            ) = self._paths(Path(directory))
            resolution.write_text("SELECT 'resolution authority';", encoding="utf-8")
            outbox.write_text("SELECT 'outbox authority';", encoding="utf-8")
            retention.write_text("SELECT 'outbox retention';", encoding="utf-8")
            orphan_guard.write_text("SELECT 'outbox orphan guard';", encoding="utf-8")
            control_time.write_text("SELECT 'control recording time';", encoding="utf-8")
            lifecycle_time.write_text("SELECT 'lifecycle recording time';", encoding="utf-8")
            calls: list[str] = []

            def base_loader(database_url: str, migration_path: Path) -> None:
                self.assertEqual(database_url, "postgresql://example")
                self.assertEqual(migration_path, base)
                calls.append("base")

            class _OrderedConnection(_Connection):
                def execute(self, statement: str) -> None:
                    self.executed.append(statement)
                    calls.append(statement)

            ordered_connection = _OrderedConnection()
            with patch.object(
                migration_install, "_apply_foundation_migration", base_loader
            ), patch.object(
                migration_install,
                "_import_psycopg",
                return_value=_Psycopg(ordered_connection),
            ):
                migration_install.apply_foundation_migration(
                    "postgresql://example", base
                )

            expected = [
                "base",
                "SELECT 'parent authority';",
                "SELECT 'resolution authority';",
                "SELECT 'outbox authority';",
                "SELECT 'outbox retention';",
                "SELECT 'outbox orphan guard';",
                "SELECT 'control recording time';",
                "SELECT 'lifecycle recording time';",
            ]
            self.assertEqual(calls, expected)
            self.assertEqual(ordered_connection.executed, expected[1:])

    def test_forward_database_failure_keeps_original_cause(self) -> None:
        """Operators get one stable error while retaining the PostgreSQL root cause."""
        with tempfile.TemporaryDirectory() as directory:
            (
                base,
                _parent,
                resolution,
                outbox,
                retention,
                orphan_guard,
                control_time,
                lifecycle_time,
            ) = self._paths(Path(directory))
            resolution.write_text("SELECT 'resolution authority';", encoding="utf-8")
            outbox.write_text("SELECT 'outbox authority';", encoding="utf-8")
            retention.write_text("SELECT 'outbox retention';", encoding="utf-8")
            orphan_guard.write_text("SELECT 'outbox orphan guard';", encoding="utf-8")
            control_time.write_text("SELECT 'control recording time';", encoding="utf-8")
            lifecycle_time.write_text("SELECT 'lifecycle recording time';", encoding="utf-8")
            with patch.object(
                migration_install, "_apply_foundation_migration", return_value=None
            ), patch.object(
                migration_install,
                "_import_psycopg",
                return_value=_Psycopg(_Connection(fail=True)),
            ):
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    "Reconciliation authority migration failed",
                ) as raised:
                    migration_install.apply_foundation_migration(
                        "postgresql://example", base
                    )
            self.assertIsInstance(raised.exception.__cause__, RuntimeError)


if __name__ == "__main__":
    unittest.main()
