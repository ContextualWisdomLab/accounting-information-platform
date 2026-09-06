"""Public installation boundary for the accounting foundation migration chain."""

from __future__ import annotations

from pathlib import Path

from . import persistence as _persistence
from .core import AccountingValidationError
from .persistence import (
    _import_psycopg,
    apply_foundation_migration as _apply_foundation_migration,
)


_BASE_FOUNDATION_PREREQUISITES = (
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
    "0013_reconciliation_run_exception_evidence.sql",
    "0014_reconciliation_candidate_allocation.sql",
    "0015_reconciliation_multi_match_conservation.sql",
    "0016_reconciliation_approval_evidence.sql",
    "0017_reconciliation_approval_lock_order.sql",
    "0018_bank_statement_balance_evidence.sql",
    "0019_reconciliation_run_command_evidence.sql",
)


def _base_foundation_chain_is_complete(migration_path: Path) -> bool:
    """Return whether the base loader can reach PostgreSQL after its file preflight."""
    return migration_path.is_file() and all(
        (migration_path.parent / filename).is_file()
        for filename in _BASE_FOUNDATION_PREREQUISITES
    )


def apply_foundation_migration(database_url: str, migration_path: Path) -> None:
    """Apply the complete checked-in foundation chain through the canonical loader."""
    # Preserve the base loader's earliest file-specific diagnostic without
    # applying a partial base chain when a later required overlay is absent.
    if not _base_foundation_chain_is_complete(migration_path):
        _apply_foundation_migration(database_url, migration_path)
        raise AccountingValidationError(
            "Base foundation validation returned without a complete checked-in chain."
        )

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
        migration_path.parent / "0036_hard_close_trial_balance_snapshot_pair.sql",
        migration_path.parent / "0037_soft_close_command_evidence_pair.sql",
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
