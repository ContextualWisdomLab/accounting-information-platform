"""Focused RED closing the direct private-party country-of-birth privacy oracle gap."""

from __future__ import annotations

import copy
import json
import unittest
from decimal import Decimal

from tests import test_postgres_bank_statement_debtor_creditor_party_private_identification_evidence_red as private_red
from tests import test_postgres_posting as posting


class BankStatementDebtorCreditorPrivateCountryPrivacyReviewRedTests(unittest.TestCase):
    """Prove CtryOfBirth changes evidence identity without becoming buyer-visible PII."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Reuse the reviewed direct-private-party fixture without subclass test duplication."""
        self.helper = (
            private_red.BankStatementDebtorCreditorPartyPrivateIdentificationEvidenceRedTests(
                "runTest"
            )
        )
        self.addCleanup(self.helper.doCleanups)
        self.helper.setUp()

    def test_country_of_birth_is_material_but_not_reversible_in_buyer_projection(self) -> None:
        """Changing only CtryOfBirth must alter internal evidence and leave buyer data unchanged."""
        baseline_private = copy.deepcopy(self.helper.base_private)
        changed_private = copy.deepcopy(self.helper.base_private)
        self.helper._birth(changed_private)["country_of_birth"] = "DE"

        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                baseline = self.helper._ingest_and_read_target_entry(
                    role,
                    self.helper._with_role_private(role, baseline_private),
                    f"{role}-private-country-baseline",
                )
                changed = self.helper._ingest_and_read_target_entry(
                    role,
                    self.helper._with_role_private(role, changed_private),
                    f"{role}-private-country-de",
                )

                expected_entry_amount = (
                    Decimal("25000.00") if role == "debtor" else Decimal("10000.00")
                )
                expected_detail_amount = (
                    Decimal("25000.00") if role == "debtor" else Decimal("6000.00")
                )
                for projection in (baseline, changed):
                    self.helper._assert_sha256(projection["counterparty_evidence_hash"])
                    self.helper._assert_sha256(projection["source_entry_hash"])
                    first_detail = projection["entry_details"][0]
                    self.helper._assert_sha256(first_detail["source_detail_hash"])
                    self.assertEqual(
                        Decimal(str(projection["entry_amount"])),
                        expected_entry_amount,
                    )
                    self.assertEqual(str(projection["entry_currency_code"]), "KRW")
                    self.assertEqual(
                        Decimal(str(first_detail["detail_amount"])),
                        expected_detail_amount,
                    )
                    self.assertEqual(str(first_detail["detail_currency_code"]), "KRW")

                self.assertNotEqual(
                    baseline["counterparty_evidence_hash"],
                    changed["counterparty_evidence_hash"],
                )
                self.assertNotEqual(
                    baseline["entry_details"][0]["source_detail_hash"],
                    changed["entry_details"][0]["source_detail_hash"],
                )
                self.assertNotEqual(
                    baseline["source_entry_hash"],
                    changed["source_entry_hash"],
                )
                self.assertEqual(
                    self.helper._public_projection(baseline),
                    self.helper._public_projection(changed),
                )

                serialized_changed = json.dumps(changed, sort_keys=True, default=str)
                self.assertNotIn('"DE"', serialized_changed)
