"""Fail-closed validation for durable HomeTax command provenance."""

from __future__ import annotations

import unittest

from accounting_information_platform.core import AccountingValidationError
from accounting_information_platform.persistence import PostgresPostingLedger


class HomeTaxPersistenceProvenanceValidationTests(unittest.TestCase):
    """Validate immutable HomeTax source evidence before opening a database session."""

    def setUp(self) -> None:
        self.ledger = PostgresPostingLedger(
            "postgresql://unused",
            "urn:cwl:tenant_account:test",
        )
        self.base_arguments = {
            "legal_entity_reference": "urn:cwl:legal_entity:test",
            "accounting_book_reference": "urn:cwl:accounting_book:test",
            "period_code": "2026-08",
            "submission_idempotency_key": "urn:cwl:home_tax_submission:test:v1",
            "register_document": {"as_of_date": "2026-08-31", "closing_amount": "0"},
            "rejection_reason_code": "hometax_transport_unavailable",
        }

    def test_invalid_source_hash_fails_before_database_work(self) -> None:
        """A malformed canonical source digest cannot reach the persistence session."""
        with self.assertRaisesRegex(AccountingValidationError, "source_payload_hash"):
            self.ledger.persist_home_tax_submission(
                **self.base_arguments,
                source_payload_hash="sha256:not-a-digest",
                source_payload_reference="urn:cwl:evidence:home_tax:test:v1",
            )

    def test_blank_source_reference_fails_before_database_work(self) -> None:
        """Whitespace cannot become the immutable locator for a statutory command."""
        with self.assertRaisesRegex(AccountingValidationError, "source_payload_reference"):
            self.ledger.persist_home_tax_submission(
                **self.base_arguments,
                source_payload_hash="sha256:" + "a" * 64,
                source_payload_reference="   ",
            )


if __name__ == "__main__":
    unittest.main()
