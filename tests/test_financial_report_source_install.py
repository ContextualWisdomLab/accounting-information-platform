"""Contracts for installing the financial-report source registry."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from accounting_information_platform import AccountingValidationError, apply_foundation_migration


class FinancialReportSourceInstallTests(unittest.TestCase):
    """Keep migration 0020 on the public installation path."""

    def test_install_fails_before_database_work_when_source_registry_is_missing(self) -> None:
        """A deployment cannot silently omit database-owned report-source authority."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            foundation = root / "0001_accounting_foundation.sql"
            foundation.write_text("SELECT 1;", encoding="utf-8")
            with self.assertRaisesRegex(
                AccountingValidationError,
                "0020_financial_report_source_registry.sql",
            ):
                apply_foundation_migration("postgresql://unused", foundation)

    def test_install_applies_source_registry_after_foundation_chain(self) -> None:
        """The public loader applies migration 0020 after the existing foundation loader succeeds."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            foundation = root / "0001_accounting_foundation.sql"
            source_registry = root / "0020_financial_report_source_registry.sql"
            foundation.write_text("SELECT 1;", encoding="utf-8")
            source_registry.write_text("SELECT 'financial_report_source';", encoding="utf-8")

            connection = MagicMock()
            context_manager = MagicMock()
            context_manager.__enter__.return_value = connection
            context_manager.__exit__.return_value = False
            psycopg = MagicMock()
            psycopg.ClientCursor = object
            psycopg.connect.return_value = context_manager

            with (
                patch(
                    "accounting_information_platform.migration_install._persistence.apply_foundation_migration"
                ) as base_install,
                patch(
                    "accounting_information_platform.migration_install._persistence._import_psycopg",
                    return_value=psycopg,
                ),
            ):
                apply_foundation_migration("postgresql://unused", foundation)

            base_install.assert_called_once_with("postgresql://unused", foundation)
            connection.execute.assert_called_once_with("SELECT 'financial_report_source';")

    def test_install_preserves_source_registry_database_failure_as_cause(self) -> None:
        """Operators receive one domain error while retaining the PostgreSQL root cause."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            foundation = root / "0001_accounting_foundation.sql"
            source_registry = root / "0020_financial_report_source_registry.sql"
            foundation.write_text("SELECT 1;", encoding="utf-8")
            source_registry.write_text("SELECT 1;", encoding="utf-8")

            psycopg = MagicMock()
            psycopg.ClientCursor = object
            psycopg.connect.side_effect = RuntimeError("report registry connection refused")

            with (
                patch(
                    "accounting_information_platform.migration_install._persistence.apply_foundation_migration"
                ),
                patch(
                    "accounting_information_platform.migration_install._persistence._import_psycopg",
                    return_value=psycopg,
                ),
            ):
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    "Financial report source registry migration failed",
                ) as raised:
                    apply_foundation_migration("postgresql://unused", foundation)

            self.assertIsInstance(raised.exception.__cause__, RuntimeError)
            self.assertEqual(
                str(raised.exception.__cause__),
                "report registry connection refused",
            )


if __name__ == "__main__":
    unittest.main()
