"""Public installation boundary for the accounting foundation migration chain."""

from __future__ import annotations

from pathlib import Path

from . import persistence as _persistence
from .core import AccountingValidationError


def apply_foundation_migration(database_url: str, migration_path: Path) -> None:
    """Apply the complete checked-in foundation chain through reconciliation conservation."""
    conservation_migration_path = (
        migration_path.parent / "0015_reconciliation_multi_match_conservation.sql"
    )
    if not conservation_migration_path.is_file():
        raise AccountingValidationError(
            "Reconciliation multi-match conservation migration is missing at "
            f"{conservation_migration_path}. Restore "
            "database/migrations/0015_reconciliation_multi_match_conservation.sql, then retry."
        )

    _persistence.apply_foundation_migration(database_url, migration_path)
    psycopg = _persistence._import_psycopg()
    try:
        with psycopg.connect(
            database_url,
            autocommit=True,
            cursor_factory=psycopg.ClientCursor,
        ) as connection:
            connection.execute(conservation_migration_path.read_text(encoding="utf-8"))
    except Exception as error:
        raise AccountingValidationError(
            "Foundation migration failed. Inspect the PostgreSQL error, restore a clean "
            "database, then retry the migration."
        ) from error


__all__ = ["apply_foundation_migration"]
