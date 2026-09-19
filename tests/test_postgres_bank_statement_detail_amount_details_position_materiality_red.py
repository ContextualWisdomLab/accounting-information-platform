"""Focused REDs for position-complete camt.053 amount-detail materiality."""

from __future__ import annotations

import copy
import unittest
from decimal import Decimal
from typing import cast

from accounting_information_platform import load_canonical_statement_fixture
from tests.test_postgres_bank_statement_detail_amount_details_evidence_red import (
    BankStatementDetailAmountDetailsEvidenceRedTests,
)


class BankStatementDetailAmountDetailsPositionMaterialityRedTests(unittest.TestCase):
    """Reject position-specific loss of admitted amount-detail evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        BankStatementDetailAmountDetailsEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Reuse the complete V14 amount-detail fixture without changing its controls."""
        self.subject = BankStatementDetailAmountDetailsEvidenceRedTests(
            "test_complete_amount_details_are_material_to_hash_chain"
        )
        self.subject.setUp()
        self.addCleanup(self.subject.doCleanups)
        self.addCleanup(self.subject.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")

    def test_every_repeated_position_and_sibling_currency_is_material(self) -> None:
        """Vary only the previously unproven field while accounting value stays fixed."""
        base_statement = self.subject.base_statement
        base_entry = base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        base_hash = self.subject._expected_hash(self.subject.base_semantics)

        def countervalue_currency(data: dict[str, object]) -> None:
            branch = cast(dict[str, object], data["countervalue_amount"])
            branch["currency_code"] = "EUR"

        def announced_posting_currency(data: dict[str, object]) -> None:
            branch = cast(dict[str, object], data["announced_posting_amount"])
            branch["currency_code"] = "USD"

        def proprietary_first_amount(data: dict[str, object]) -> None:
            amounts = cast(list[dict[str, object]], data["proprietary_amounts"])
            amounts[0]["amount"] = "18.81"

        def proprietary_first_currency(data: dict[str, object]) -> None:
            amounts = cast(list[dict[str, object]], data["proprietary_amounts"])
            amounts[0]["currency_code"] = "EUR"

        def proprietary_second_type(data: dict[str, object]) -> None:
            amounts = cast(list[dict[str, object]], data["proprietary_amounts"])
            amounts[1]["type"] = "BANK_SETTLEMENT_NET"

        mutators = {
            "countervalue_amount_currency": countervalue_currency,
            "announced_posting_amount_currency": announced_posting_currency,
            "proprietary_first_amount_value": proprietary_first_amount,
            "proprietary_first_amount_currency": proprietary_first_currency,
            "proprietary_second_type": proprietary_second_type,
        }

        for name, mutate in mutators.items():
            with self.subTest(name=name):
                semantics = copy.deepcopy(self.subject.base_semantics)
                mutate(semantics)
                payload = self.subject._with_amount_details(self.fixture, semantics)
                statement = self.subject._parse(payload)
                entry = statement.entries[0]
                detail = entry.entry_details[0]
                changed_hash = self.subject._expected_hash(semantics)

                self.assertNotEqual(base_hash, changed_hash)
                self.assertEqual(entry.entry_amount, Decimal("25000.00"))
                self.assertEqual(entry.entry_currency_code, "KRW")
                self.assertEqual(detail.detail_amount, Decimal("25000.00"))
                self.assertEqual(detail.detail_currency_code, "KRW")
                self.assertEqual(getattr(detail, "amount_details", None), semantics)
                self.assertEqual(
                    getattr(detail, "amount_details_evidence_hash", None), changed_hash
                )
                self.subject._assert_entry_hash_binding(entry, changed_hash)
                self.assertNotEqual(base_detail.source_detail_hash, detail.source_detail_hash)
                self.assertNotEqual(base_entry.source_entry_hash, entry.source_entry_hash)
                self.assertNotEqual(
                    base_statement.normalized_payload_hash,
                    statement.normalized_payload_hash,
                )
                self.assertEqual(
                    base_statement.entries[1].source_entry_hash,
                    statement.entries[1].source_entry_hash,
                )


if __name__ == "__main__":
    unittest.main()
