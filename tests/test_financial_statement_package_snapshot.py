"""Consistency regression for the statutory financial-statement package read."""

from __future__ import annotations

import unittest
from decimal import Decimal
from unittest import mock

import accounting_information_platform.accept as accept_module
from accounting_information_platform.persistence import PostgresPostingLedger
from tests import test_postgres_posting as posting


class FinancialStatementPackageSnapshotTests(unittest.TestCase):
    """Require all statement components to come from one PostgreSQL read snapshot."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_package_cannot_mix_pre_and_post_commit_statement_components(self) -> None:
        """A concurrent posting cannot tear one package across independent transactions."""
        original_lookup = PostgresPostingLedger.load_financial_statement
        lookup_count = 0

        def interleaved_lookup(
            ledger: PostgresPostingLedger, *args: object, **kwargs: object
        ) -> dict[str, object]:
            nonlocal lookup_count
            document = original_lookup(ledger, *args, **kwargs)
            lookup_count += 1
            if lookup_count == 1:
                self.case.ledger.post(self.case._two_line_proposal(), self.case.policy)
            return document

        with mock.patch.object(
            PostgresPostingLedger,
            "load_financial_statement",
            autospec=True,
            side_effect=interleaved_lookup,
        ):
            package = accept_module.lookup_financial_statement_package(
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                self.case.policy.legal_entity_reference,
                self.case.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
            )

        income = package["income_statement"]
        balance_sheet = package["balance_sheet"]
        self.assertIsInstance(income, dict)
        self.assertIsInstance(balance_sheet, dict)
        self.assertEqual(
            Decimal(str(income["net_income_amount"])),
            Decimal(str(balance_sheet["net_income_amount"])),
        )


if __name__ == "__main__":
    unittest.main()
