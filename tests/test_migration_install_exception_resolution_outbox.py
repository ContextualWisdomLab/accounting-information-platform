"""Contracts for installing reconciliation outbox authority migrations."""

from __future__ import annotations

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from accounting_information_platform import AccountingValidationError
from accounting_information_platform import migration_install


class _Connection:
    """Capture forward migration statements in execution order."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, statement: str) -> None:
        """Record one migration statement batch."""
        self.executed.append(statement)


class _Psycopg:
    """Minimal psycopg module double for the public migration loader."""

    ClientCursor = object

    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    def connect(self, *_args: object, **_kwargs: object):
        """Yield the configured autocommit connection."""
        return contextlib.nullcontext(self.connection)


class ReconciliationExceptionResolutionOutboxInstallTests(unittest.TestCase):
    """Keep outbox atomicity, retention, and orphan admission mandatory and ordered."""

    def _paths(self, root: Path) -> tuple[Path, Path, Path, Path, Path, Path]:
        """Create base plus all reconciliation authority forward-migration paths."""
        base = root / "0001_accounting_foundation.sql"
        parent_authority = root / "0019_reconciliation_run_database_snapshot_authority.sql"
        resolution = root / "0020_reconciliation_exception_resolution_command.sql"
        outbox = root / "0021_reconciliation_exception_resolution_outbox_pair.sql"
        retention = root / "0022_reconciliation_authority_outbox_retention.sql"
        orphan_guard = root / "0023_reconciliation_authority_outbox_orphan_guard.sql"
        base.write_text("BEGIN; COMMIT;", encoding="utf-8")
        parent_authority.write_text("SELECT 'parent authority';", encoding="utf-8")
        resolution.write_text("SELECT 'resolution authority';", encoding="utf-8")
        return base, parent_authority, resolution, outbox, retention, orphan_guard

    def test_missing_outbox_migration_fails_before_base_install(self) -> None:
        """The public loader cannot stop before the command/status/outbox invariant."""
        with tempfile.TemporaryDirectory() as directory:
            base, _parent, _resolution, _outbox, _retention, _orphan = self._paths(
                Path(directory)
            )
            base_loader = Mock()
            with patch.object(
                migration_install, "_base_foundation_chain_is_complete", return_value=True
            ), patch.object(migration_install, "_apply_foundation_migration", base_loader):
                with self.assertRaisesRegex(AccountingValidationError, "0021"):
                    migration_install.apply_foundation_migration(
                        "postgresql://unused", base
                    )
            base_loader.assert_not_called()

    def test_missing_retention_migration_fails_before_base_install(self) -> None:
        """The loader cannot admit atomicity without preserving the committed evidence pair."""
        with tempfile.TemporaryDirectory() as directory:
            base, _parent, _resolution, outbox, _retention, _orphan = self._paths(
                Path(directory)
            )
            outbox.write_text("SELECT 'outbox authority';", encoding="utf-8")
            base_loader = Mock()
            with patch.object(
                migration_install, "_base_foundation_chain_is_complete", return_value=True
            ), patch.object(migration_install, "_apply_foundation_migration", base_loader):
                with self.assertRaisesRegex(AccountingValidationError, "0022"):
                    migration_install.apply_foundation_migration(
                        "postgresql://unused", base
                    )
            base_loader.assert_not_called()

    def test_missing_orphan_guard_migration_fails_before_base_install(self) -> None:
        """The loader cannot publish reserved authority event types without command admission."""
        with tempfile.TemporaryDirectory() as directory:
            base, _parent, _resolution, outbox, retention, _orphan = self._paths(
                Path(directory)
            )
            outbox.write_text("SELECT 'outbox authority';", encoding="utf-8")
            retention.write_text("SELECT 'outbox retention';", encoding="utf-8")
            base_loader = Mock()
            with patch.object(
                migration_install, "_base_foundation_chain_is_complete", return_value=True
            ), patch.object(migration_install, "_apply_foundation_migration", base_loader):
                with self.assertRaisesRegex(AccountingValidationError, "0023"):
                    migration_install.apply_foundation_migration(
                        "postgresql://unused", base
                    )
            base_loader.assert_not_called()

    def test_outbox_migrations_execute_after_resolution_command_migration(self) -> None:
        """The exported loader applies atomicity, retention, then orphan admission."""
        with tempfile.TemporaryDirectory() as directory:
            base, _parent, _resolution, outbox, retention, orphan_guard = self._paths(
                Path(directory)
            )
            outbox.write_text("SELECT 'outbox authority';", encoding="utf-8")
            retention.write_text("SELECT 'outbox retention';", encoding="utf-8")
            orphan_guard.write_text("SELECT 'outbox orphan guard';", encoding="utf-8")
            connection = _Connection()
            with patch.object(
                migration_install, "_base_foundation_chain_is_complete", return_value=True
            ), patch.object(
                migration_install, "_apply_foundation_migration", return_value=None
            ), patch.object(
                migration_install,
                "_import_psycopg",
                return_value=_Psycopg(connection),
            ):
                migration_install.apply_foundation_migration(
                    "postgresql://example", base
                )

            self.assertEqual(
                connection.executed,
                [
                    "SELECT 'parent authority';",
                    "SELECT 'resolution authority';",
                    "SELECT 'outbox authority';",
                    "SELECT 'outbox retention';",
                    "SELECT 'outbox orphan guard';",
                ],
            )


if __name__ == "__main__":
    unittest.main()
