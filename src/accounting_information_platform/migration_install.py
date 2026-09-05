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
        migration_path.parent / "0019_reconciliation_run_database_snapshot_authority.sql",
        migration_path.parent / "0020_reconciliation_exception_resolution_command.sql",
        migration_path.parent / "0021_reconciliation_exception_resolution_outbox_pair.sql",
        migration_path.parent / "0022_reconciliation_authority_outbox_retention.sql",
        migration_path.parent / "0023_reconciliation_authority_outbox_orphan_guard.sql",
        migration_path.parent / "0024_reconciliation_control_recording_time_authority.sql",
        migration_path.parent / "0025_reconciliation_lifecycle_recording_time_authority.sql",
        migration_path.parent / "0026_reconciliation_lifecycle_source_payload_identity.sql",
        migration_path.parent / "0027_reconciliation_lifecycle_session_lock_authority.sql",
        migration_path.parent / "0028_reconciliation_lifecycle_capability_privileges.sql",
        migration_path.parent / "0029_trial_balance_snapshot_population_unique_index.sql",
        migration_path.parent / "0030_trial_balance_snapshot_immutability.sql",
        migration_path.parent / "0031_trial_balance_line_conservation_validation.sql",
        migration_path.parent / "0032_period_close_journal_population_fence.sql",
        migration_path.parent / "0033_open_period_journal_population_fence.sql",
        migration_path.parent / "0034_book_period_control_seed.sql",
        migration_path.parent / "0035_trial_balance_snapshot_hard_close_pair.sql",
    )
    for forward_migration_path in forward_migration_paths:
        if not forward_migration_path.is_file():
            raise AccountingValidationError(
                "Required reconciliation authority migration is missing at "
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
            "Reconciliation authority migration failed. Inspect the PostgreSQL error, restore "
            "a clean database, then retry the complete foundation migration."
        ) from error


# A large integration-test and operator surface historically imports the loader
# from persistence directly. Keep that compatibility path on the complete-chain
# installer so no supported install can stop before the current database-owned
# reconciliation authority and exception-resolution boundaries.
_persistence.apply_foundation_migration = apply_foundation_migration


__all__ = ["apply_foundation_migration"]
