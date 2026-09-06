"""Public installation boundary for the accounting foundation migration chain."""

from __future__ import annotations

from pathlib import Path

from . import persistence as _persistence
from .core import AccountingValidationError


_apply_base_foundation_migration = _persistence.apply_foundation_migration

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
    # The complete-chain wrapper must preserve two fail-closed boundaries at once:
    # base-chain gaps keep the base loader's precise recovery message, while a
    # missing forward overlay is rejected before any base migration is applied.
    if not _base_foundation_chain_is_complete(migration_path):
        _apply_base_foundation_migration(database_url, migration_path)
        raise AccountingValidationError(
            "Base foundation validation returned without a complete checked-in chain."
        )

    authority_migration_path = (
        migration_path.parent / "0019_reconciliation_run_database_snapshot_authority.sql"
    )
    if not authority_migration_path.is_file():
        raise AccountingValidationError(
            "Required reconciliation transition database-authority migration is missing at "
            f"{authority_migration_path}. Restore the checked-in migration chain, then retry."
        )

    _apply_base_foundation_migration(database_url, migration_path)
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
