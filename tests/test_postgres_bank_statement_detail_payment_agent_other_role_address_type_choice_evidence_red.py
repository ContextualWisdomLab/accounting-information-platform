"""PostgreSQL REDs for AddressType3Choice across remaining standard payment-agent roles."""

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


class BankStatementDetailPaymentAgentOtherRoleAddressTypeChoiceEvidenceRedTests(
    unittest.TestCase
):
    """Retain institution/branch address-type choice provenance on every remaining role."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the source-real PostgreSQL and camt.053 integration fixture."""
        postal.BankStatementDetailPaymentAgentOtherRolePostalAddressEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare the existing focused helper with exception-safe nested cleanup."""
        self.case = (
            postal.BankStatementDetailPaymentAgentOtherRolePostalAddressEvidenceRedTests(
                "setUp"
            )
        )
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def test_address_type_choice_is_material_for_every_role_and_owner(self) -> None:
        """Cd/Prtry and admitted proprietary fields affect canonical evidence identity."""
        for element_name, (digest_key, bicfi) in self.case.ROLES.items():
            for owner in ("institution", "branch"):
                with self.subTest(element_name=element_name, owner=owner):
                    plain = self.case._with_agent(
                        element_name,
                        self.case._agent_value(element_name, bicfi),
                    )
                    base_payload = self._with_choice(
                        plain,
                        owner,
                        self._proprietary_choice(),
                    )
                    base = self.case._statement(base_payload)
                    base_entry = base.entries[1]
                    base_detail = base_entry.entry_details[0]
                    base_digest = getattr(base_detail, digest_key, None)
                    self._assert_digest(base_digest)
                    self.case._assert_exact_accounting_amount(base_entry, base_detail)

                    for variant_name, choice in self._choice_variants().items():
                        with self.subTest(variant_name=variant_name):
                            payload = self._with_choice(plain, owner, choice)
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

    def test_proprietary_choice_layout_is_representation_only_for_every_role_and_owner(
        self,
    ) -> None:
        """Whitespace inside Prtry changes source bytes without changing admitted semantics."""
        needle = b"                      <Prtry>\n"
        whitespace = b"                        \n"

        for element_name, (digest_key, bicfi) in self.case.ROLES.items():
            for owner in ("institution", "branch"):
                with self.subTest(element_name=element_name, owner=owner):
                    plain = self.case._with_agent(
                        element_name,
                        self.case._agent_value(element_name, bicfi),
                    )
                    payload = self._with_choice(
                        plain,
                        owner,
                        self._proprietary_choice(),
                    )
                    self.assertEqual(payload.count(needle), 1)
                    reformatted = payload.replace(
                        needle,
                        needle + whitespace,
                        1,
                    )
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

                    self.assertNotEqual(
                        base.source_artifact_hash,
                        changed.source_artifact_hash,
                    )
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

    def test_changed_choice_requires_explicit_correction_for_every_role_and_owner(self) -> None:
        """Accepted address-type evidence cannot be silently replaced on replay."""
        for element_name, (_, bicfi) in self.case.ROLES.items():
            for owner in ("institution", "branch"):
                with self.subTest(element_name=element_name, owner=owner):
                    plain = self.case._with_agent(
                        element_name,
                        self.case._agent_value(element_name, bicfi),
                    )
                    base_payload = self._with_choice(
                        plain,
                        owner,
                        self._proprietary_choice(),
                    )
                    changed_payload = self._with_choice(
                        plain,
                        owner,
                        self._coded_choice(),
                    )
                    statement = self.case._statement(base_payload)
                    account_reference = self.case._register_bank_account(statement)
                    store = MemoryArtifactStore()
                    accepted = postal.accept_bank_statement_evidence(
                        self.case._command(
                            base_payload,
                            account_reference,
                            f"address-type-base-{element_name}-{owner}",
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
                                f"address-type-changed-{element_name}-{owner}",
                            ),
                            postal.posting.DATABASE_URL,
                            self.case.case.policy.tenant_reference,
                            artifact_store=store,
                        )

    def test_choice_evidence_stays_private_on_buyer_projection_for_every_role_and_owner(
        self,
    ) -> None:
        """Buyer reads retain only the canonical role digest, not reversible choice facts."""
        for element_name, (digest_key, bicfi) in self.case.ROLES.items():
            for owner in ("institution", "branch"):
                with self.subTest(element_name=element_name, owner=owner):
                    plain = self.case._with_agent(
                        element_name,
                        self.case._agent_value(element_name, bicfi),
                    )
                    proprietary_payload = self._with_choice(
                        plain,
                        owner,
                        self._proprietary_choice(),
                    )
                    coded_payload = self._with_choice(
                        plain,
                        owner,
                        self._coded_choice(),
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
                        f"privacy-proprietary-{element_name}-{owner}-{uuid.uuid4().hex}",
                    )
                    coded_detail = self.case._ingest_and_read(
                        coded_payload,
                        coded_reference,
                        f"privacy-coded-{element_name}-{owner}-{uuid.uuid4().hex}",
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

    @staticmethod
    def _assert_digest(value: object) -> None:
        """Require a present canonical digest instead of allowing None-equality false positives."""
        if not isinstance(value, str) or re.fullmatch(_DIGEST_RE, value) is None:
            raise AssertionError("canonical digest must match sha256:<64 lowercase hex>")

    @classmethod
    def _with_choice(cls, payload: bytes, owner: str, choice: bytes) -> bytes:
        """Insert AdrTp as the first child of institution or branch PostalAddress27."""
        needle = b"                  <PstlAdr>\n"
        offsets: list[int] = []
        start = 0
        while True:
            offset = payload.find(needle, start)
            if offset < 0:
                break
            offsets.append(offset)
            start = offset + len(needle)
        if len(offsets) != 2:
            raise AssertionError("focused standard role must contain two postal addresses")
        if owner == "institution":
            offset = offsets[0]
        elif owner == "branch":
            offset = offsets[1]
        else:
            raise AssertionError(f"unsupported address owner: {owner}")
        insertion = offset + len(needle)
        return payload[:insertion] + choice + payload[insertion:]

    @staticmethod
    def _proprietary_choice(
        *,
        identifier: str = "CSTM",
        issuer: str = "CWL Address Registry",
        scheme_name: str | None = "ADDR_TYPE",
    ) -> bytes:
        """Serialize the proprietary AddressType3Choice branch in V14 sequence order."""
        scheme = (
            f"                        <SchmeNm>{scheme_name}</SchmeNm>\n"
            if scheme_name is not None
            else ""
        )
        return (
            "                    <AdrTp>\n"
            "                      <Prtry>\n"
            f"                        <Id>{identifier}</Id>\n"
            f"                        <Issr>{issuer}</Issr>\n"
            + scheme
            + "                      </Prtry>\n"
            "                    </AdrTp>\n"
        ).encode("utf-8")

    @staticmethod
    def _coded_choice() -> bytes:
        """Serialize the coded AddressType3Choice branch."""
        return (
            "                    <AdrTp>\n"
            "                      <Cd>BIZZ</Cd>\n"
            "                    </AdrTp>\n"
        ).encode("utf-8")

    @classmethod
    def _choice_variants(cls) -> dict[str, bytes]:
        """Vary every admitted proprietary field plus the choice discriminator."""
        return {
            "proprietary-id": cls._proprietary_choice(identifier="OTHR"),
            "proprietary-issuer": cls._proprietary_choice(
                issuer="Alternate Address Registry"
            ),
            "proprietary-scheme": cls._proprietary_choice(scheme_name="ALT_ADDR_TYPE"),
            "proprietary-scheme-absent": cls._proprietary_choice(scheme_name=None),
            "choice-code": cls._coded_choice(),
        }


if __name__ == "__main__":
    unittest.main()
