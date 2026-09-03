"""RED contracts for database-owned financial-report source authority."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0020_financial_report_source_registry.sql"


class FinancialReportSourceRegistryContracts(unittest.TestCase):
    """Require report authority to originate from tenant-scoped AIS persistence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Load the migration that must introduce the authority registry."""
        cls.migration = MIGRATION.read_text(encoding="utf-8")

    def test_report_run_binds_accounting_scope_and_period(self) -> None:
        """A report run cannot substitute caller labels for AIS accounting identity."""
        for fragment in (
            "CREATE TABLE accounting_reporting.financial_report_run",
            "tenant_account_id uuid NOT NULL",
            "legal_entity_id uuid NOT NULL",
            "accounting_book_id uuid NOT NULL",
            "fiscal_period_id uuid NOT NULL",
            "knowledge_cutoff_at timestamptz NOT NULL",
            "reporting_currency_code text NOT NULL",
            "source_period_status_code text NOT NULL",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, self.migration)
        self.assertRegex(
            self.migration,
            re.compile(
                r"FOREIGN KEY\s*\(tenant_account_id, legal_entity_id, accounting_book_id\)\s*"
                r"REFERENCES accounting_core\.accounting_book",
                re.IGNORECASE | re.MULTILINE,
            ),
        )
        self.assertRegex(
            self.migration,
            re.compile(
                r"FOREIGN KEY\s*\(tenant_account_id, fiscal_period_id\)\s*"
                r"REFERENCES accounting_core\.fiscal_period",
                re.IGNORECASE | re.MULTILINE,
            ),
        )

    def test_report_source_requires_an_existing_trial_balance_snapshot(self) -> None:
        """A source row must reference AIS snapshot evidence rather than a supplied digest alone."""
        self.assertIn(
            "CREATE TABLE accounting_reporting.financial_report_source",
            self.migration,
        )
        self.assertIn("trial_balance_snapshot_id uuid NOT NULL", self.migration)
        self.assertRegex(
            self.migration,
            re.compile(
                r"FOREIGN KEY\s*\(tenant_account_id, trial_balance_snapshot_id\)\s*"
                r"REFERENCES accounting_reporting\.trial_balance_snapshot",
                re.IGNORECASE | re.MULTILINE,
            ),
        )
        self.assertIn(
            "UNIQUE (tenant_account_id, financial_report_run_id, period_context_code)",
            self.migration,
        )
        self.assertIn(
            "period_context_code IN ('current', 'comparison')",
            self.migration,
        )

    def test_registry_is_forced_rls_and_tenant_bound(self) -> None:
        """Ordinary runtime access cannot bypass the tenant selected by the database session."""
        for table_name in ("financial_report_run", "financial_report_source"):
            with self.subTest(table_name=table_name):
                self.assertIn(
                    f"ALTER TABLE accounting_reporting.{table_name} ENABLE ROW LEVEL SECURITY",
                    self.migration,
                )
                self.assertIn(
                    f"ALTER TABLE accounting_reporting.{table_name} FORCE ROW LEVEL SECURITY",
                    self.migration,
                )
        self.assertGreaterEqual(
            self.migration.count("accounting_core.current_tenant_account_id()"),
            4,
        )

    def test_source_snapshot_scope_must_match_report_scope(self) -> None:
        """Database constraints must reject a snapshot from another entity, book, or period."""
        for column_name in (
            "legal_entity_id",
            "accounting_book_id",
            "fiscal_period_id",
        ):
            with self.subTest(column_name=column_name):
                self.assertIn(column_name, self.migration)
        self.assertIn("financial_report_source_scope_guard", self.migration)
        self.assertIn("source snapshot scope does not match financial report run", self.migration)

    def test_registry_cannot_claim_validation_approval_or_publication(self) -> None:
        """This source-authority slice must not smuggle later workflow authority into the schema."""
        lowered = self.migration.lower()
        for forbidden in (
            "filing_ready",
            "regulator_accepted",
            "approved_by",
            "published_at",
            "authoritative_report boolean",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)


if __name__ == "__main__":
    unittest.main()
