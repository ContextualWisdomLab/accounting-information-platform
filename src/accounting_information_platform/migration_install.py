"""Ordered installation boundary for accounting foundation migration extensions."""

from __future__ import annotations

from pathlib import Path

from . import persistence as _persistence
from .core import AccountingValidationError


_LEGACY_APPLY_FOUNDATION_MIGRATION = _persistence.apply_foundation_migration
_LEGACY_MIGRATION_NAMES = (
    "0002_chart_account_class.sql",
    "0003_home_tax_submission.sql",
    "0004_close_idempotency_key.sql",
    "0005_closed_period_guard.sql",
    "0006_concurrency_hot_partition.sql",
    "0007_runtime_tenant_binding.sql",
    "0008_fiscal_period_open_command.sql",
    "0009_accounting_book_period_control.sql",
    "0010_soft_close_command_evidence.sql",
    "0011_bank_statement_evidence.sql",
    "0012_bank_assignment_command_identity.sql",
)


def apply_foundation_migration(database_url: str, migration_path: Path) -> None:
    """Apply the checked-in PostgreSQL foundation through reconciliation evidence."""
    for migration_name in _LEGACY_MIGRATION_NAMES:
        legacy_migration_path = migration_path.parent / migration_name
        if not legacy_migration_path.is_file():
            raise AccountingValidationError(
                f"Accounting foundation migration is missing at {legacy_migration_path}. "
                f"Restore database/migrations/{migration_name}, then retry."
            )

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
