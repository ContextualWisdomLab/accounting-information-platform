"""Public installation boundary for the accounting foundation migration chain."""

from __future__ import annotations

from pathlib import Path

from .core import AccountingValidationError
from .persistence import (
    _import_psycopg,
    apply_foundation_migration as _apply_foundation_migration,
)

_COMPLETION_MIGRATION = "0020_reconciliation_run_completion_evidence.sql"


def apply_foundation_migration(database_url: str, migration_path: Path) -> None:
    """Apply the complete checked-in foundation chain including run completion evidence."""
    completion_path = migration_path.parent / _COMPLETION_MIGRATION
    if not completion_path.is_file():
        raise AccountingValidationError(
            f"Reconciliation completion-evidence migration is missing at {completion_path}. "
            f"Restore database/migrations/{_COMPLETION_MIGRATION}, then retry."
        )
    _apply_foundation_migration(database_url, migration_path)
    psycopg = _import_psycopg()
    try:
        with psycopg.connect(
            database_url,
            autocommit=True,
            cursor_factory=psycopg.ClientCursor,
        ) as connection:
            connection.execute(completion_path.read_text(encoding="utf-8"))
    except Exception as error:
        raise AccountingValidationError(
            "Reconciliation completion migration failed. Inspect the PostgreSQL error, "
            "restore a clean database, then retry the migration."
        ) from error


__all__ = ["apply_foundation_migration"]
