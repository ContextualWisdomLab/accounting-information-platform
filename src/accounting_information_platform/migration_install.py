"""Public installation boundary for the accounting foundation migration chain."""

from __future__ import annotations

from pathlib import Path

from . import persistence as _persistence
from .core import AccountingValidationError
from .persistence import (
    _import_psycopg,
    apply_foundation_migration as _apply_foundation_migration,
)


def apply_foundation_migration(database_url: str, migration_path: Path) -> None:
    """Apply the complete checked-in foundation chain through the canonical loader."""
    forward_migration_paths = (
        migration_path.parent / "0020_reconciliation_exception_resolution_command.sql",
        migration_path.parent / "0021_reconciliation_exception_resolution_outbox_pair.sql",
    )
    for forward_migration_path in forward_migration_paths:
        if not forward_migration_path.is_file():
            raise AccountingValidationError(
                "Required reconciliation exception-resolution migration is missing at "
                f"{forward_migration_path}. Restore the checked-in migration chain, then retry."
            )

    _apply_foundation_migration(database_url, migration_path)
    psycopg = _import_psycopg()
    try:
        with psycopg.connect(
            database_url, autocommit=True, cursor_factory=psycopg.ClientCursor
        ) as connection:
            for forward_migration_path in forward_migration_paths:
                connection.execute(forward_migration_path.read_text(encoding="utf-8"))
    except Exception as error:
        raise AccountingValidationError(
            "Reconciliation exception-resolution migration failed. Inspect the PostgreSQL "
            "error, restore a clean database, then retry the complete foundation migration."
        ) from error


# A large integration-test surface historically imports the loader from the
# persistence module directly. During this stacked migration, keep that legacy
# import path pointed at the exported complete-chain loader so isolated suites
# cannot stop before the current exception-resolution authority boundary and
# accidentally depend on test discovery order.
_persistence.apply_foundation_migration = apply_foundation_migration


__all__ = ["apply_foundation_migration"]
