"""Public installation boundary for the complete accounting foundation migration chain."""

from __future__ import annotations

from pathlib import Path

from .core import AccountingValidationError
from .persistence import (
    _import_psycopg,
    apply_foundation_migration as _apply_foundation_migration,
)


def apply_foundation_migration(database_url: str, migration_path: Path) -> None:
    """Apply the checked-in foundation chain through reconciliation completion.

    The legacy persistence loader currently owns migrations 0001 through 0019.
    This public installation boundary fail-closes on a missing 0020 before any
    database work, delegates that established chain, and then applies the
    forward reconciliation-completion authority migration. Keeping the forward
    migration here ensures the exported production installer and real
    integration fixtures can deploy the new control without rewriting an
    already-reviewed historical migration.
    """
    completion_migration_path = (
        migration_path.parent / "0020_reconciliation_completion_command.sql"
    )
    if not completion_migration_path.is_file():
        raise AccountingValidationError(
            "Reconciliation completion-command migration is missing at "
            f"{completion_migration_path}. Restore "
            "database/migrations/0020_reconciliation_completion_command.sql, then retry."
        )

    _apply_foundation_migration(database_url, migration_path)

    psycopg = _import_psycopg()
    try:
        with psycopg.connect(
            database_url,
            autocommit=True,
            cursor_factory=psycopg.ClientCursor,
        ) as connection:
            connection.execute(
                completion_migration_path.read_text(encoding="utf-8")
            )
    except Exception as error:
        raise AccountingValidationError(
            "Foundation migration failed while applying reconciliation completion authority. "
            "Inspect the PostgreSQL error, restore or repair the migration state, then retry."
        ) from error


__all__ = ["apply_foundation_migration"]
