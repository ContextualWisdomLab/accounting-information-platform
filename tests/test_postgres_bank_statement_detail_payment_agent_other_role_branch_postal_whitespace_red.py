"""Regression for branch PostalAddress27 whitespace normalization across standard roles."""

from __future__ import annotations

import unittest

from tests import (
    test_postgres_bank_statement_detail_payment_agent_other_role_postal_address_evidence_red as postal,
)


class BankStatementDetailPaymentAgentOtherRoleBranchPostalWhitespaceRedTests(
    unittest.TestCase
):
    """Keep branch-only XML layout changes out of admitted accounting evidence identity."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the source-real PostgreSQL and camt.053 integration fixture."""
        postal.BankStatementDetailPaymentAgentOtherRolePostalAddressEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare the existing focused helper without leaking nested cleanup state."""
        self.case = (
            postal.BankStatementDetailPaymentAgentOtherRolePostalAddressEvidenceRedTests(
                "setUp"
            )
        )
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def test_branch_postal_xml_layout_is_representation_only_for_every_role(self) -> None:
        """Whitespace inserted only in BrnchId/PstlAdr must not change semantic hashes."""
        needle = b"                  <PstlAdr>\n"
        whitespace = b"                    \n"

        for element_name, (digest_key, bicfi) in self.case.ROLES.items():
            with self.subTest(element_name=element_name):
                value = self.case._agent_value(element_name, bicfi)
                payload = self.case._with_agent(element_name, value)
                self.assertEqual(payload.count(needle), 2)

                institution_offset = payload.find(needle)
                branch_offset = payload.find(
                    needle,
                    institution_offset + len(needle),
                )
                self.assertGreater(branch_offset, institution_offset)

                insertion_offset = branch_offset + len(needle)
                reformatted = (
                    payload[:insertion_offset]
                    + whitespace
                    + payload[insertion_offset:]
                )
                self.assertNotEqual(payload, reformatted)
                self.assertEqual(reformatted.count(needle), 2)

                base = self.case._statement(payload)
                changed = self.case._statement(reformatted)
                base_entry = base.entries[1]
                changed_entry = changed.entries[1]
                base_detail = base_entry.entry_details[0]
                changed_detail = changed_entry.entry_details[0]
                base_digest = getattr(base_detail, digest_key, None)
                changed_digest = getattr(changed_detail, digest_key, None)

                self.assertNotEqual(
                    base.source_artifact_hash,
                    changed.source_artifact_hash,
                )
                for digest in (base_digest, changed_digest):
                    self.assertIsInstance(digest, str)
                    self.assertRegex(str(digest), r"\Asha256:[0-9a-f]{64}\Z")
                self.assertEqual(base_digest, changed_digest)
                self.assertEqual(
                    base_detail.source_detail_hash,
                    changed_detail.source_detail_hash,
                )
                self.assertEqual(
                    base_entry.source_entry_hash,
                    changed_entry.source_entry_hash,
                )
                self.assertEqual(
                    base.normalized_payload_hash,
                    changed.normalized_payload_hash,
                )
                self.case._assert_exact_accounting_amount(
                    changed_entry,
                    changed_detail,
                )
