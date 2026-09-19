"""Focused RED for complete ultimate-agent postal buyer non-reversibility."""

from __future__ import annotations

import unittest
import uuid

from tests import (
    test_postgres_bank_statement_detail_ultimate_agent_postal_address_evidence_red as postal,
)


class BankStatementDetailUltimateAgentPostalPrivacyReviewRedTests(unittest.TestCase):
    """Reject every admitted PostalAddress27 scalar from buyer-visible output."""

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

    def test_every_admitted_postal_scalar_is_non_reversible(self) -> None:
        """Short/common source scalars remain protected, not silently allowlisted."""
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
                        f"{role}-{placement}-postal-complete-privacy-{uuid.uuid4().hex}",
                    )
                    absent = self.helper._ingest_and_read_first_detail(
                        absent_payload,
                        f"{role}-{placement}-postal-complete-privacy-absent-{uuid.uuid4().hex}",
                    )
                    evidence_key = self.helper.case._evidence_key(role)
                    rich_public = self.helper._public_projection(rich, evidence_key)
                    absent_public = self.helper._public_projection(absent, evidence_key)

                    self.assertEqual(rich_public, absent_public)
                    source_values = set(self.helper._scalar_leaves(source_address))
                    self.assertIn("BIZZ", source_values)
                    self.assertIn("DE", source_values)
                    self.assertIn("17", source_values)
                    self.assertIn("12", source_values)
                    buyer_values = set(self.helper._scalar_leaves(rich_public))
                    self.assertTrue(source_values.isdisjoint(buyer_values))


if __name__ == "__main__":
    unittest.main()
