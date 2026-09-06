"""Contracts for installing reconciliation outbox authority migrations."""

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

    def _paths(self, root: Path) -> tuple[Path, dict[str, Path]]:
        """Create the complete canonical reconciliation forward chain."""
        base = root / "0001_accounting_foundation.sql"
        base.write_text("BEGIN; COMMIT;", encoding="utf-8")
        paths: dict[str, Path] = {}
        for key, filename, statement in _FORWARD_MIGRATIONS:
            path = root / filename
            path.write_text(statement, encoding="utf-8")
            paths[key] = path
        return base, paths

    def _assert_missing_fails_before_base(self, missing: str, marker: str) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base, paths = self._paths(Path(directory))
            paths[missing].unlink()
            base_loader = Mock()
            with patch.object(
                migration_install, "_base_foundation_chain_is_complete", return_value=True
            ), patch.object(migration_install, "_apply_foundation_migration", base_loader):
                with self.assertRaisesRegex(AccountingValidationError, marker):
                    migration_install.apply_foundation_migration(
                        "postgresql://unused", base
                    )
            base_loader.assert_not_called()

    def test_missing_outbox_migration_fails_before_base_install(self) -> None:
        """The public loader cannot stop before the command/status/outbox invariant."""
        self._assert_missing_fails_before_base("outbox", "0022")

    def test_missing_retention_migration_fails_before_base_install(self) -> None:
        """The loader cannot admit atomicity without preserving the committed evidence pair."""
        self._assert_missing_fails_before_base("retention", "0023")

    def test_missing_orphan_guard_migration_fails_before_base_install(self) -> None:
        """The loader cannot publish reserved authority event types without command admission."""
        self._assert_missing_fails_before_base("orphan_guard", "0024")

    def test_outbox_migrations_execute_after_resolution_command_migration(self) -> None:
        """The exported loader preserves the complete canonical forward order."""
        with tempfile.TemporaryDirectory() as directory:
            base, _paths = self._paths(Path(directory))
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
                [statement for _key, _filename, statement in _FORWARD_MIGRATIONS],
            )


if __name__ == "__main__":
    unittest.main()
