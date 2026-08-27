"""Ordered installation boundary for accounting foundation migration extensions."""

from __future__ import annotations

from pathlib import Path

from . import persistence as _persistence
from .core import AccountingValidationError


_LEGACY_APPLY_FOUNDATION_MIGRATION = _persistence.apply_foundation_migration


def apply_foundation_migration(database_url: str, migration_path: Path) -> None:
    """Apply the checked-in PostgreSQL foundation through reconciliation evidence."""
    reconciliation_control_migration_path = (
        migration_path.parent / "0013_reconciliation_run_exception_evidence.sql"
    )
    if not reconciliation_control_migration_path.is_file():
        raise AccountingValidationError(
            "Reconciliation run/exception evidence migration is missing at "
            f"{reconciliation_control_migration_path}. Restore "
            "database/migrations/0013_reconciliation_run_exception_evidence.sql, then retry."
        )

    _LEGACY_APPLY_FOUNDATION_MIGRATION(database_url, migration_path)
    psycopg = _persistence._import_psycopg()
    try:
        with psycopg.connect(
            database_url, autocommit=True, cursor_factory=psycopg.ClientCursor
        ) as connection:
            connection.execute(
                reconciliation_control_migration_path.read_text(encoding="utf-8")
            )
    except Exception as error:
        raise AccountingValidationError(
            "Reconciliation-control migration failed. Inspect the PostgreSQL error, "
            "restore a clean database, then retry the migration."
        ) from error


# Keep direct imports from the persistence implementation aligned with the public
# package installer while migration loading is being decomposed out of that module.
_persistence.apply_foundation_migration = apply_foundation_migration
