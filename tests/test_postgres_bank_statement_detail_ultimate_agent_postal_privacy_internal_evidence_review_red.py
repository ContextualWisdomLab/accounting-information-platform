"""Focused RED for ultimate-agent postal evidence before buyer privacy projection."""

from __future__ import annotations

import unittest
import uuid
from decimal import Decimal

from tests import (
    test_postgres_bank_statement_detail_ultimate_agent_postal_address_evidence_red as postal,
)


class BankStatementDetailUltimateAgentPostalPrivacyInternalEvidenceReviewRedTests(
    unittest.TestCase
):
    """Prove postal source facts affect internal evidence before asserting non-reversibility."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        postal.BankStatementDetailUltimateAgentPostalAddressEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one isolated postal helper without inheriting its full suite."""
        self.helper = postal.BankStatementDetailUltimateAgentPostalAddressEvidenceRedTests(
            "setUp"
        )
        self.addCleanup(self.helper.doCleanups)
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)

    def test_rich_postal_source_changes_internal_evidence_before_public_projection(self) -> None:
        """Privacy cannot pass merely because both rich and absent postal facts were discarded."""
        placements = (
            ("institution", postal._INSTITUTION_ADDRESS),
            ("branch", postal._BRANCH_ADDRESS),
        )
        for role, identity in self.helper.case.identities.items():
            for placement, source_address in placements:
                with self.subTest(role=role, placement=placement):
                    rich_payload = self.helper._payload(
                        role,
                        identity,
                        institution_address=(
                            source_address if placement == "institution" else None
                        ),
                        branch_address=(
                            source_address if placement == "branch" else None
                        ),
                    )
                    absent_payload = self.helper._payload(
                        role,
                        identity,
                        institution_address=None,
                        branch_address=None,
                    )
                    rich = self.helper._ingest_and_read_first_detail(
                        rich_payload,
                        f"{role}-{placement}-postal-internal-rich-{uuid.uuid4().hex}",
                    )
                    absent = self.helper._ingest_and_read_first_detail(
                        absent_payload,
                        f"{role}-{placement}-postal-internal-absent-{uuid.uuid4().hex}",
                    )
                    evidence_key = self.helper.case._evidence_key(role)

                    for projection in (rich, absent):
                        self.helper._assert_sha256(projection[evidence_key])
                        self.helper._assert_sha256(projection["source_detail_hash"])
                        self.assertEqual(
                            Decimal(str(projection["detail_amount"])),
                            Decimal("25000.00"),
                        )
                        self.assertEqual(projection["detail_currency_code"], "KRW")

                    self.assertNotEqual(rich[evidence_key], absent[evidence_key])
                    self.assertNotEqual(
                        rich["source_detail_hash"],
                        absent["source_detail_hash"],
                    )

                    rich_public = self.helper._public_projection(rich, evidence_key)
                    absent_public = self.helper._public_projection(absent, evidence_key)
                    self.assertEqual(rich_public, absent_public)

                    source_values = set(self.helper._scalar_leaves(source_address))
                    buyer_values = set(self.helper._scalar_leaves(rich_public))
                    self.assertTrue(source_values.isdisjoint(buyer_values))


if __name__ == "__main__":
    unittest.main()
