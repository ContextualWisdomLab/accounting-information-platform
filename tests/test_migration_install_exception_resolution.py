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
    """Capture one forward migration execution."""

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
    """Keep migration 0020 fail-closed at the exported install boundary."""

    def _paths(self, root: Path) -> tuple[Path, Path]:
        """Create placeholder base and forward migration paths."""
        base = root / "0001_accounting_foundation.sql"
        forward = root / "0020_reconciliation_exception_resolution_command.sql"
        base.write_text("BEGIN; COMMIT;", encoding="utf-8")
        return base, forward

    def test_missing_forward_migration_fails_before_base_install(self) -> None:
        """The public loader cannot silently stop at migration 0019."""
        with tempfile.TemporaryDirectory() as directory:
            base, _forward = self._paths(Path(directory))
            base_loader = Mock()
            with patch.object(migration_install, "_apply_foundation_migration", base_loader):
                with self.assertRaisesRegex(AccountingValidationError, "0020"):
                    migration_install.apply_foundation_migration(
                        "postgresql://unused", base
                    )
            base_loader.assert_not_called()

    def test_forward_migration_executes_after_existing_chain(self) -> None:
        """The exported loader applies the existing chain before migration 0020."""
        with tempfile.TemporaryDirectory() as directory:
            base, forward = self._paths(Path(directory))
            forward.write_text("SELECT 'resolution authority';", encoding="utf-8")
            connection = _Connection()
            calls: list[str] = []

            def base_loader(database_url: str, migration_path: Path) -> None:
                self.assertEqual(database_url, "postgresql://example")
                self.assertEqual(migration_path, base)
                calls.append("base")

            class _OrderedConnection(_Connection):
                def execute(self, statement: str) -> None:
                    self.executed.append(statement)
                    calls.append("forward")

            ordered_connection = _OrderedConnection()
            with patch.object(
                migration_install, "_apply_foundation_migration", base_loader
            ), patch.object(
                migration_install, "_import_psycopg", return_value=_Psycopg(ordered_connection)
            ):
                migration_install.apply_foundation_migration(
                    "postgresql://example", base
                )

            self.assertEqual(calls, ["base", "forward"])
            self.assertEqual(
                ordered_connection.executed, ["SELECT 'resolution authority';"]
            )
            self.assertEqual(connection.executed, [])

    def test_forward_database_failure_keeps_original_cause(self) -> None:
        """Operators get one stable error while retaining the PostgreSQL root cause."""
        with tempfile.TemporaryDirectory() as directory:
            base, forward = self._paths(Path(directory))
            forward.write_text("SELECT 'resolution authority';", encoding="utf-8")
            with patch.object(
                migration_install, "_apply_foundation_migration", return_value=None
            ), patch.object(
                migration_install,
                "_import_psycopg",
                return_value=_Psycopg(_Connection(fail=True)),
            ):
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    "exception-resolution migration failed",
                ) as raised:
                    migration_install.apply_foundation_migration(
                        "postgresql://example", base
                    )
            self.assertIsInstance(raised.exception.__cause__, RuntimeError)


if __name__ == "__main__":
    unittest.main()
