"""PostgreSQL RED for buyer-visible purpose-digest presence on standard agents."""

from __future__ import annotations

import unittest

from tests import (
    test_postgres_bank_statement_detail_payment_agent_other_role_identification_evidence_red
    as other_role,
)
from tests import test_postgres_posting as posting


class BankStatementDetailPaymentAgentOtherRoleDigestPresenceRedTests(unittest.TestCase):
    """Reject privacy-oracle false positives when a role digest disappears entirely."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Reuse the exact role fixture with exception-safe nested cleanup ordering."""
        self.case = (
            other_role.BankStatementDetailPaymentAgentOtherRoleIdentificationEvidenceRedTests(
                "test_other_identification_stays_private_on_buyer_projection_for_each_role"
            )
        )
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def test_each_private_projection_retains_its_canonical_role_digest(self) -> None:
        """Both scheme-choice projections must expose the existing non-reversible role digest."""
        for element_name, (digest_key, bicfi) in self.case.ROLES.items():
            with self.subTest(element_name=element_name):
                proprietary_payload = self.case._with_agent(
                    element_name,
                    bicfi,
                    other_id=f"{element_name}-ALT-001",
                    scheme_kind="proprietary",
                    scheme_value="BANK",
                    issuer="CWL Registry",
                )
                coded_payload = self.case._with_agent(
                    element_name,
                    bicfi,
                    other_id=f"{element_name}-ALT-001",
                    scheme_kind="code",
                    scheme_value="BANK",
                    issuer="CWL Registry",
                )
                proprietary_statement = self.case._statement(proprietary_payload)
                coded_statement = self.case._statement(coded_payload)
                proprietary_reference = self.case._register_bank_account(
                    proprietary_statement
                )
                coded_reference = self.case._register_bank_account(coded_statement)
                proprietary_detail = self.case._ingest_and_read(
                    proprietary_payload,
                    proprietary_reference,
                    f"digest-proprietary-{element_name}",
                )
                coded_detail = self.case._ingest_and_read(
                    coded_payload,
                    coded_reference,
                    f"digest-coded-{element_name}",
                )

                for projection_name, projection in (
                    ("proprietary", proprietary_detail),
                    ("coded", coded_detail),
                ):
                    with self.subTest(
                        element_name=element_name,
                        projection_name=projection_name,
                    ):
                        digest = projection.get(digest_key)
                        self.assertIsInstance(digest, str)
                        self.assertRegex(str(digest), r"\Asha256:[0-9a-f]{64}\Z")

                self.assertEqual(
                    proprietary_detail[digest_key],
                    coded_detail[digest_key],
                    "BICFI is fixed; the purpose digest must exist and stay stable",
                )


if __name__ == "__main__":
    unittest.main()
