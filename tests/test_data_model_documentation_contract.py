"""Keep buyer-facing data-model documentation aligned with durable accounting facts."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_MODEL = ROOT / "docs/DATA_MODEL.md"
ERD = ROOT / "docs/ERD.md"
HOME_TAX_MIGRATION = ROOT / "database/migrations/0003_home_tax_submission.sql"
BANK_STATEMENT_MIGRATION = ROOT / "database/migrations/0011_bank_statement_evidence.sql"


class DataModelDocumentationContractTests(unittest.TestCase):
    """Reject stale data-model claims that contradict the checked-in schema."""

    def test_home_tax_provenance_columns_are_documented(self) -> None:
        """HomeTax command identity and source provenance stay visible in the data model."""
        migration = HOME_TAX_MIGRATION.read_text(encoding="utf-8")
        data_model = DATA_MODEL.read_text(encoding="utf-8")
        for column_name in (
            "submission_idempotency_key",
            "source_payload_hash",
            "source_payload_reference",
            "register_payload_hash",
        ):
            self.assertIn(column_name, migration)
            self.assertIn(column_name, data_model)

    def test_bank_statement_evidence_columns_are_documented(self) -> None:
        """Statement artifact hashes and idempotency stay visible in the data model."""
        migration = BANK_STATEMENT_MIGRATION.read_text(encoding="utf-8")
        data_model = DATA_MODEL.read_text(encoding="utf-8")
        for column_name in (
            "account_identifier_hash",
            "source_artifact_hash",
            "normalized_payload_hash",
            "ingestion_idempotency_key",
            "source_entry_hash",
        ):
            self.assertIn(column_name, migration)
            self.assertIn(column_name, data_model)
        self.assertIn(
            "FOREIGN KEY (tenant_account_id, legal_entity_id, accounting_book_id)",
            migration,
        )

    def test_current_financial_statements_are_not_described_as_future_work(self) -> None:
        """The future-extension section must not demote already implemented statement reads."""
        data_model = DATA_MODEL.read_text(encoding="utf-8")
        self.assertIn("## Future extensions", data_model)
        future_extensions = data_model.split("## Future extensions", 1)[1]
        self.assertNotIn("financial statements", future_extensions.lower())

    def test_foundation_erd_is_checked_in_and_covers_accounting_authority(self) -> None:
        """Acquisition diligence gets a durable ERD for the authoritative foundation."""
        self.assertTrue(ERD.is_file(), "docs/ERD.md must be checked in")
        erd = ERD.read_text(encoding="utf-8")
        self.assertIn("erDiagram", erd)
        for object_name in (
            "legal_entity_record",
            "accounting_book",
            "chart_account",
            "fiscal_period",
            "journal_proposal_record",
            "general_journal",
            "journal_entry_line",
            "posting_receipt",
            "outbox_event",
            "home_tax_submission",
            "bank_account_record",
            "bank_account_assignment",
            "bank_statement_record",
            "bank_statement_entry",
        ):
            self.assertIn(object_name, erd)


if __name__ == "__main__":
    unittest.main()
