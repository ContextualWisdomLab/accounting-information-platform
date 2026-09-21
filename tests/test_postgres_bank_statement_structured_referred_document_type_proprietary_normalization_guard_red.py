"""RED guard for collateral normalization during proprietary type parsing."""

from __future__ import annotations

import unittest

from accounting_information_platform import (
    CAMT053_MESSAGE_DEFINITION,
    parse_bank_statement_payload,
)
from tests import (
    test_postgres_bank_statement_structured_referred_document_type_proprietary_evidence_red
    as proprietary_type_contract,
)

_PARENT_TEST = (
    proprietary_type_contract.
    BankStatementStructuredReferredDocumentTypeProprietaryEvidenceRedTests
)


class BankStatementStructuredReferredDocumentTypeProprietaryNormalizationGuardRedTests(
    unittest.TestCase
):
    """Keep non-structured normalization stable across Cd -> Prtry conversion."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Build coded, base-proprietary, and changed-proprietary statements."""
        self.parent = _PARENT_TEST(
            "test_document_type_proprietary_is_material_to_each_canonical_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.coded_statement = parse_bank_statement_payload(
            self.parent.coded_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

    def test_code_to_proprietary_conversion_preserves_unrelated_normalization(
        self,
    ) -> None:
        """A Prtry branch must not clobber unrelated statement/entry/detail fields."""
        statements = (
            self.coded_statement,
            self.parent.base_statement,
            self.parent.changed_statement,
        )
        statement_fields = (
            "message_definition_identifier",
            "statement_identity_reference",
            "electronic_sequence_number",
            "legal_sequence_number",
            "period_start_at",
            "period_end_at",
            "opening_balance_hash",
            "closing_balance_hash",
            "account_currency_code",
            "account_identifier_hash",
        )
        self._assert_equal_fields(statements, statement_fields)
        self.assertEqual(
            {len(statement.entries) for statement in statements},
            {len(self.coded_statement.entries)},
        )

        entry_fields = (
            "source_entry_identity",
            "entry_sequence_number",
            "source_locator_path",
            "booking_occurred_at",
            "value_occurred_at",
            "entry_amount",
            "entry_currency_code",
            "credit_debit_code",
            "reversal_indicator",
            "bank_transaction_domain_code",
            "bank_transaction_family_code",
            "bank_transaction_subfamily_code",
            "end_to_end_reference",
            "account_servicer_reference",
            "mandate_reference",
            "cheque_reference",
            "remittance_evidence_text",
            "counterparty_evidence_hash",
        )
        detail_fields = (
            "detail_sequence_number",
            "source_locator_path",
            "detail_amount",
            "detail_currency_code",
            "credit_debit_code",
            "end_to_end_reference",
            "account_servicer_reference",
            "remittance_evidence_text",
        )

        for entry_index in range(len(self.coded_statement.entries)):
            entries = tuple(
                statement.entries[entry_index]
                for statement in statements
            )
            self._assert_equal_fields(entries, entry_fields)
            self.assertEqual(
                {len(entry.entry_details) for entry in entries},
                {len(entries[0].entry_details)},
            )
            for detail_index in range(len(entries[0].entry_details)):
                details = tuple(
                    entry.entry_details[detail_index]
                    for entry in entries
                )
                self._assert_equal_fields(details, detail_fields)

        for statement in statements:
            entry = statement.entries[0]
            detail = entry.entry_details[0]
            self.parent.line_contract._assert_exact_transaction_amount(
                entry,
                detail,
            )

    def _assert_equal_fields(
        self,
        objects: tuple[object, ...],
        fields: tuple[str, ...],
    ) -> None:
        """Assert every listed field matches the coded baseline exactly."""
        baseline = tuple(
            getattr(objects[0], field)
            for field in fields
        )
        for candidate in objects[1:]:
            self.assertEqual(
                tuple(getattr(candidate, field) for field in fields),
                baseline,
            )


if __name__ == "__main__":
    unittest.main()
