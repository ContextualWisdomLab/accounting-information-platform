"""Public installation boundary for the accounting foundation migration chain."""

from __future__ import annotations

from pathlib import Path

from . import persistence as _persistence
from .core import AccountingValidationError


_FINANCIAL_REPORT_SOURCE_MIGRATION = "0020_financial_report_source_registry.sql"


def apply_foundation_migration(database_url: str, migration_path: Path) -> None:
    """Apply the accounting foundation and database-owned reporting-source registry."""
    source_registry_path = migration_path.parent / _FINANCIAL_REPORT_SOURCE_MIGRATION
    if not source_registry_path.is_file():
        raise AccountingValidationError(
            "Financial report source registry migration is missing at "
            f"{source_registry_path}. Restore database/migrations/"
            f"{_FINANCIAL_REPORT_SOURCE_MIGRATION}, then retry."
        )

    _persistence.apply_foundation_migration(database_url, migration_path)
    psycopg = _persistence._import_psycopg()
    try:
        with psycopg.connect(
            database_url,
            autocommit=True,
            cursor_factory=psycopg.ClientCursor,
        ) as connection:
            connection.execute(source_registry_path.read_text(encoding="utf-8"))
    except Exception as error:
        raise AccountingValidationError(
            "Financial report source registry migration failed. Inspect the PostgreSQL "
            "error, restore a clean database, then retry the migration."
        ) from error


__all__ = ["apply_foundation_migration"]
