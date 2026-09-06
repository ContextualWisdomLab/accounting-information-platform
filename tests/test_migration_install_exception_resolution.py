"""Contracts for installing reconciliation exception-resolution authority."""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from accounting_information_platform import AccountingValidationError
from accounting_information_platform import migration_install


_FORWARD_MIGRATIONS = (
    ("parent", "0020_reconciliation_run_database_snapshot_authority.sql", "SELECT 'parent authority';"),
    ("resolution", "0021_reconciliation_exception_resolution_command.sql", "SELECT 'resolution authority';"),
    ("outbox", "0022_reconciliation_exception_resolution_outbox_pair.sql", "SELECT 'outbox authority';"),
    ("retention", "0023_reconciliation_authority_outbox_retention.sql", "SELECT 'outbox retention';"),
    ("orphan_guard", "0024_reconciliation_authority_outbox_orphan_guard.sql", "SELECT 'outbox orphan guard';"),
    ("control_time", "0025_reconciliation_control_recording_time_authority.sql", "SELECT 'control recording time';"),
    ("lifecycle_time", "0026_reconciliation_lifecycle_recording_time_authority.sql", "SELECT 'lifecycle recording time';"),
    ("source_payload", "0027_reconciliation_lifecycle_source_payload_identity.sql", "SELECT 'source payload identity';"),
    ("session_lock", "0028_reconciliation_lifecycle_session_lock_authority.sql", "SELECT 'session lock authority';"),
    ("capability", "0029_reconciliation_lifecycle_capability_privileges.sql", "SELECT 'capability privileges';"),
)


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

    def _paths(self, root: Path) -> tuple[Path, dict[str, Path]]:
        """Create the complete canonical forward-migration fixture."""
        base = root / "0001_accounting_foundation.sql"
        base.write_text("BEGIN; COMMIT;", encoding="utf-8")
        paths: dict[str, Path] = {}
        for key, filename, statement in _FORWARD_MIGRATIONS:
            path = root / filename
            path.write_text(statement, encoding="utf-8")
            paths[key] = path
        return base, paths

    def test_missing_resolution_migration_fails_before_base_install(self) -> None:
        """The public loader cannot silently stop before exception-resolution authority."""
        with tempfile.TemporaryDirectory() as directory:
            base, paths = self._paths(Path(directory))
            paths["resolution"].unlink()
            base_loader = Mock()
            with patch.object(
                migration_install, "_base_foundation_chain_is_complete", return_value=True
            ), patch.object(migration_install, "_apply_foundation_migration", base_loader):
                with self.assertRaisesRegex(AccountingValidationError, "0021"):
                    migration_install.apply_foundation_migration(
                        "postgresql://unused", base
                    )
            base_loader.assert_not_called()

    def test_forward_migrations_execute_after_existing_chain_in_order(self) -> None:
        """The exported loader applies every reconciliation authority overlay in order."""
        with tempfile.TemporaryDirectory() as directory:
            base, _paths = self._paths(Path(directory))
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
                migration_install, "_base_foundation_chain_is_complete", return_value=True
            ), patch.object(
                migration_install, "_apply_foundation_migration", base_loader
            ), patch.object(
                migration_install,
                "_import_psycopg",
                return_value=_Psycopg(ordered_connection),
            ):
                migration_install.apply_foundation_migration(
                    "postgresql://example", base
                )

            expected = ["base", *[statement for _key, _filename, statement in _FORWARD_MIGRATIONS]]
            self.assertEqual(calls, expected)
            self.assertEqual(ordered_connection.executed, expected[1:])

    def test_forward_database_failure_keeps_original_cause(self) -> None:
        """Operators get one stable error while retaining the PostgreSQL root cause."""
        with tempfile.TemporaryDirectory() as directory:
            base, _paths = self._paths(Path(directory))
            with patch.object(
                migration_install, "_base_foundation_chain_is_complete", return_value=True
            ), patch.object(
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
