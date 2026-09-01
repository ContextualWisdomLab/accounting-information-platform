"""Public installation boundary for the accounting foundation migration chain."""

from __future__ import annotations

from pathlib import Path

from .core import AccountingValidationError
from .persistence import (
    _import_psycopg,
    apply_foundation_migration as _apply_foundation_migration,
)


def apply_foundation_migration(database_url: str, migration_path: Path) -> None:
    """Apply the complete checked-in foundation chain through the canonical loader."""
    resolution_migration_path = (
        migration_path.parent / "0020_reconciliation_exception_resolution_command.sql"
    )
    if not resolution_migration_path.is_file():
        raise AccountingValidationError(
            "Reconciliation exception-resolution command migration is missing at "
            f"{resolution_migration_path}. Restore "
            "database/migrations/0020_reconciliation_exception_resolution_command.sql, "
            "then retry."
        )
    _apply_foundation_migration(database_url, migration_path)
    psycopg = _import_psycopg()
    try:
        with psycopg.connect(
            database_url, autocommit=True, cursor_factory=psycopg.ClientCursor
        ) as connection:
            connection.execute(resolution_migration_path.read_text(encoding="utf-8"))
    except Exception as error:
        raise AccountingValidationError(
            "Reconciliation exception-resolution migration failed. Inspect the PostgreSQL "
            "error, restore a clean database, then retry the complete foundation migration."
        ) from error


__all__ = ["apply_foundation_migration"]
