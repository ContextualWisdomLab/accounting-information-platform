"""Public installation boundary for the accounting foundation migration chain."""

from __future__ import annotations

from pathlib import Path

from . import persistence as _persistence
from .core import AccountingValidationError


_apply_base_foundation_migration = _persistence.apply_foundation_migration


def apply_foundation_migration(database_url: str, migration_path: Path) -> None:
    """Apply the complete checked-in foundation chain through the canonical loader."""
    _apply_base_foundation_migration(database_url, migration_path)

    authority_migration_path = (
        migration_path.parent / "0019_reconciliation_run_database_snapshot_authority.sql"
    )
    if not authority_migration_path.is_file():
        raise AccountingValidationError(
            "Required reconciliation transition database-authority migration is missing at "
            f"{authority_migration_path}. Restore the checked-in migration chain, then retry."
        )

    psycopg = _persistence._import_psycopg()
    try:
        with psycopg.connect(
            database_url, autocommit=True, cursor_factory=psycopg.ClientCursor
        ) as connection:
            connection.execute(authority_migration_path.read_text(encoding="utf-8"))
    except Exception as error:
        raise AccountingValidationError(
            "Reconciliation transition database-authority migration failed. Inspect the "
            "PostgreSQL error, restore a clean database, then retry the complete foundation "
            "migration."
        ) from error


# A large integration-test and operator surface historically imports the loader
# from persistence directly. Keep that compatibility path on the complete-chain
# installer so no supported install can stop after the caller-trusting 0019 base
# definition and accidentally omit the database-owned transition authority.
_persistence.apply_foundation_migration = apply_foundation_migration


__all__ = ["apply_foundation_migration"]
