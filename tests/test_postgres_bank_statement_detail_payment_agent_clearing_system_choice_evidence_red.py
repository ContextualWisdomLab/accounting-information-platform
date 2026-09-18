"""PostgreSQL REDs for clearing-system choice evidence on standard payment agents."""

from __future__ import annotations

import re
import unittest
import uuid

from accounting_information_platform import AccountingValidationError, MemoryArtifactStore
from tests import (
    test_postgres_bank_statement_detail_payment_agent_other_role_postal_address_evidence_red as postal,
)

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)
_DIGEST_RE = r"sha256:[0-9a-f]{64}"


class BankStatementDetailPaymentAgentClearingSystemChoiceEvidenceRedTests(
    unittest.TestCase
):
    """Retain ClrSysMmbId choice provenance on non-instructing standard roles."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the source-real PostgreSQL and camt.053 integration fixture."""
        postal.BankStatementDetailPaymentAgentOtherRolePostalAddressEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare the focused role helper with exception-safe nested cleanup."""
        self.case = (
            postal.BankStatementDetailPaymentAgentOtherRolePostalAddressEvidenceRedTests(
                "setUp"
            )
        )
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def test_clearing_system_choice_is_material_for_every_remaining_role(self) -> None:
        """ClrSysId choice, value, member id, and absence affect canonical evidence."""
        for element_name, (digest_key, bicfi) in self.case.ROLES.items():
            with self.subTest(element_name=element_name):
                base_payload = self._payload(
                    element_name,
                    bicfi,
                    choice_kind="proprietary",
                    choice_value="USABA",
                    member_id=f"{element_name}-MEMBER-001",
                )
                base = self.case._statement(base_payload)
                base_entry = base.entries[1]
                base_detail = base_entry.entry_details[0]
                base_digest = getattr(base_detail, digest_key, None)
                self._assert_digest(base_digest)
                self.case._assert_exact_accounting_amount(base_entry, base_detail)

                variants = {
                    "member-id": self._payload(
                        element_name,
                        bicfi,
                        choice_kind="proprietary",
                        choice_value="USABA",
                        member_id=f"{element_name}-MEMBER-002",
                    ),
                    "choice-value": self._payload(
                        element_name,
                        bicfi,
                        choice_kind="proprietary",
                        choice_value="ALTCLR",
                        member_id=f"{element_name}-MEMBER-001",
                    ),
                    "same-scalar-choice-discriminator": self._payload(
                        element_name,
                        bicfi,
                        choice_kind="code",
                        choice_value="USABA",
                        member_id=f"{element_name}-MEMBER-001",
                    ),
                    "choice-absent": self._payload(
                        element_name,
                        bicfi,
                        choice_kind=None,
                        choice_value=None,
                        member_id=f"{element_name}-MEMBER-001",
                    ),
                }

                for variant_name, payload in variants.items():
                    with self.subTest(variant_name=variant_name):
                        statement = self.case._statement(payload)
                        entry = statement.entries[1]
                        detail = entry.entry_details[0]
                        digest = getattr(detail, digest_key, None)
                        self._assert_digest(digest)
                        self.assertEqual(base_digest, digest)
                        self.assertEqual(
                            base.account_identifier_hash,
                            statement.account_identifier_hash,
                        )
                        self.assertNotEqual(
                            base_detail.source_detail_hash,
                            detail.source_detail_hash,
                        )
                        self.assertNotEqual(
                            base_entry.source_entry_hash,
                            entry.source_entry_hash,
                        )
                        self.assertNotEqual(
                            base.normalized_payload_hash,
                            statement.normalized_payload_hash,
                        )
                        self.assertEqual(
                            base.entries[0].source_entry_hash,
                            statement.entries[0].source_entry_hash,
                        )
                        self.case._assert_exact_accounting_amount(entry, detail)

    def test_clearing_system_layout_is_representation_only_for_every_role(self) -> None:
        """Whitespace between ClrSysId children changes source bytes, not semantics."""
        needle = b"                    <ClrSysId>\n"
        whitespace = b"                      \n"

        for element_name, (digest_key, bicfi) in self.case.ROLES.items():
            with self.subTest(element_name=element_name):
                payload = self._payload(
                    element_name,
                    bicfi,
                    choice_kind="proprietary",
                    choice_value="USABA",
                    member_id=f"{element_name}-MEMBER-001",
                )
                self.assertEqual(payload.count(needle), 1)
                reformatted = payload.replace(needle, needle + whitespace, 1)
                self.assertNotEqual(payload, reformatted)

                base = self.case._statement(payload)
                changed = self.case._statement(reformatted)
                base_entry = base.entries[1]
                changed_entry = changed.entries[1]
                base_detail = base_entry.entry_details[0]
                changed_detail = changed_entry.entry_details[0]
                base_digest = getattr(base_detail, digest_key, None)
                changed_digest = getattr(changed_detail, digest_key, None)
                self._assert_digest(base_digest)
                self._assert_digest(changed_digest)

                self.assertNotEqual(base.source_artifact_hash, changed.source_artifact_hash)
                self.assertEqual(base_digest, changed_digest)
                self.assertEqual(
                    base_detail.source_detail_hash,
                    changed_detail.source_detail_hash,
                )
                self.assertEqual(base_entry.source_entry_hash, changed_entry.source_entry_hash)
                self.assertEqual(
                    base.normalized_payload_hash,
                    changed.normalized_payload_hash,
                )
                self.case._assert_exact_accounting_amount(changed_entry, changed_detail)

    def test_clearing_system_change_requires_explicit_correction_for_every_role(self) -> None:
        """Accepted clearing-system evidence cannot be silently replaced on replay."""
        for element_name, (_, bicfi) in self.case.ROLES.items():
            with self.subTest(element_name=element_name):
                base_payload = self._payload(
                    element_name,
                    bicfi,
                    choice_kind="proprietary",
                    choice_value="USABA",
                    member_id=f"{element_name}-MEMBER-001",
                )
                changed_payload = self._payload(
                    element_name,
                    bicfi,
                    choice_kind="code",
                    choice_value="USABA",
                    member_id=f"{element_name}-MEMBER-001",
                )
                statement = self.case._statement(base_payload)
                account_reference = self.case._register_bank_account(statement)
                store = MemoryArtifactStore()
                accepted = postal.accept_bank_statement_evidence(
                    self.case._command(
                        base_payload,
                        account_reference,
                        f"clearing-base-{element_name}",
                    ),
                    postal.posting.DATABASE_URL,
                    self.case.case.policy.tenant_reference,
                    artifact_store=store,
                )
                self.assertFalse(accepted["replayed"])
                with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                    postal.accept_bank_statement_evidence(
                        self.case._command(
                            changed_payload,
                            account_reference,
                            f"clearing-changed-{element_name}",
                        ),
                        postal.posting.DATABASE_URL,
                        self.case.case.policy.tenant_reference,
                        artifact_store=store,
                    )

    def test_clearing_system_choice_stays_private_on_buyer_projection(self) -> None:
        """Buyer reads retain the role digest without reversible clearing identifiers."""
        for element_name, (digest_key, bicfi) in self.case.ROLES.items():
            with self.subTest(element_name=element_name):
                proprietary_payload = self._payload(
                    element_name,
                    bicfi,
                    choice_kind="proprietary",
                    choice_value="USABA",
                    member_id=f"{element_name}-MEMBER-001",
                )
                coded_payload = self._payload(
                    element_name,
                    bicfi,
                    choice_kind="code",
                    choice_value="USABA",
                    member_id=f"{element_name}-MEMBER-001",
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
                    f"clearing-private-proprietary-{element_name}-{uuid.uuid4().hex}",
                )
                coded_detail = self.case._ingest_and_read(
                    coded_payload,
                    coded_reference,
                    f"clearing-private-coded-{element_name}-{uuid.uuid4().hex}",
                )

                for projection in (proprietary_detail, coded_detail):
                    self._assert_digest(projection.get(digest_key))
                    self._assert_digest(projection.get("source_detail_hash"))
                    self.assertEqual(projection["detail_amount"], "6000")
                    self.assertEqual(projection["detail_currency_code"], "KRW")

                self.assertEqual(
                    proprietary_detail[digest_key],
                    coded_detail[digest_key],
                )
                self.assertNotEqual(
                    proprietary_detail["source_detail_hash"],
                    coded_detail["source_detail_hash"],
                )
                proprietary_visible = dict(proprietary_detail)
                coded_visible = dict(coded_detail)
                for projection in (proprietary_visible, coded_visible):
                    projection.pop(digest_key)
                    projection.pop("source_detail_hash")
                self.assertEqual(proprietary_visible, coded_visible)

    def _payload(
        self,
        element_name: str,
        bicfi: str,
        *,
        choice_kind: str | None,
        choice_value: str | None,
        member_id: str,
    ) -> bytes:
        """Insert ClearingSystemMemberIdentification2 after the role BICFI."""
        plain = self.case._with_agent(
            element_name,
            self.case._agent_value(element_name, bicfi),
        )
        needle = f"                  <BICFI>{bicfi}</BICFI>\n".encode("utf-8")
        if plain.count(needle) != 1:
            raise AssertionError("focused standard role must contain exactly one BICFI")
        clearing = self._clearing_system_xml(
            choice_kind=choice_kind,
            choice_value=choice_value,
            member_id=member_id,
        )
        return plain.replace(needle, needle + clearing, 1)

    @staticmethod
    def _clearing_system_xml(
        *,
        choice_kind: str | None,
        choice_value: str | None,
        member_id: str,
    ) -> bytes:
        """Serialize ClearingSystemMemberIdentification2 in V14 sequence order."""
        if not member_id:
            raise AssertionError("MmbId is mandatory")
        lines = ["                  <ClrSysMmbId>\n"]
        if choice_kind is not None or choice_value is not None:
            if choice_kind not in {"code", "proprietary"} or not choice_value:
                raise AssertionError("ClrSysId requires code|proprietary plus a value")
            lines.append("                    <ClrSysId>\n")
            tag = "Cd" if choice_kind == "code" else "Prtry"
            lines.append(f"                      <{tag}>{choice_value}</{tag}>\n")
            lines.append("                    </ClrSysId>\n")
        lines.append(f"                    <MmbId>{member_id}</MmbId>\n")
        lines.append("                  </ClrSysMmbId>\n")
        return "".join(lines).encode("utf-8")

    @staticmethod
    def _assert_digest(value: object) -> None:
        """Reject missing digests and require canonical sha256 syntax."""
        if not isinstance(value, str) or re.fullmatch(_DIGEST_RE, value) is None:
            raise AssertionError("canonical digest must match sha256:<64 lowercase hex>")


if __name__ == "__main__":
    unittest.main()
