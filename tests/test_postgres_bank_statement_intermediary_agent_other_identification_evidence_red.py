"""PostgreSQL REDs for intermediary-agent GenericFinancialIdentification1 evidence."""

from __future__ import annotations

import unittest
import uuid

from accounting_information_platform import AccountingValidationError, MemoryArtifactStore
from tests import (
    test_postgres_bank_statement_intermediary_agent_deep_identity_evidence_red as deep,
)

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementIntermediaryAgentOtherIdentificationEvidenceRedTests(
    unittest.TestCase
):
    """Retain FinInstnId/Othr semantics for IntrmyAgt1, IntrmyAgt2 and IntrmyAgt3."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the source-real PostgreSQL and camt.053 integration fixture."""
        deep.BankStatementIntermediaryAgentDeepIdentityEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare the focused intermediary helper with exception-safe cleanup."""
        self.case = deep.BankStatementIntermediaryAgentDeepIdentityEvidenceRedTests(
            "setUp"
        )
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def test_other_identification_is_material_at_each_chain_position(self) -> None:
        """Id, scheme choice/optionality, issuer, and Othr presence affect evidence."""
        for slot in (1, 2, 3):
            with self.subTest(slot=slot):
                base_payload = self._payload(
                    slot,
                    identifier=f"INTRMY-{slot}-ALT-001",
                    scheme_kind="proprietary",
                    scheme_value="BANK",
                    issuer="CWL Bank Registry",
                )
                base = self.case._statement(base_payload)
                base_entry = base.entries[1]
                base_detail = base_entry.entry_details[0]
                digest_key = f"intermediary_agent_{slot}_evidence_hash"
                base_digest = getattr(base_detail, digest_key, None)
                self.case._assert_digest(base_digest)
                self.case._assert_digest(base_detail.source_detail_hash)
                self.case._assert_exact_amount(base_entry, base_detail)

                variants = {
                    "other-id": self._payload(
                        slot,
                        identifier=f"INTRMY-{slot}-ALT-002",
                        scheme_kind="proprietary",
                        scheme_value="BANK",
                        issuer="CWL Bank Registry",
                    ),
                    "scheme-proprietary": self._payload(
                        slot,
                        identifier=f"INTRMY-{slot}-ALT-001",
                        scheme_kind="proprietary",
                        scheme_value="NATIONAL_BANK_ID",
                        issuer="CWL Bank Registry",
                    ),
                    "same-scalar-scheme-discriminator": self._payload(
                        slot,
                        identifier=f"INTRMY-{slot}-ALT-001",
                        scheme_kind="code",
                        scheme_value="BANK",
                        issuer="CWL Bank Registry",
                    ),
                    "scheme-absent": self._payload(
                        slot,
                        identifier=f"INTRMY-{slot}-ALT-001",
                        scheme_kind=None,
                        scheme_value=None,
                        issuer="CWL Bank Registry",
                    ),
                    "issuer": self._payload(
                        slot,
                        identifier=f"INTRMY-{slot}-ALT-001",
                        scheme_kind="proprietary",
                        scheme_value="BANK",
                        issuer="Alternate Bank Registry",
                    ),
                    "issuer-absent": self._payload(
                        slot,
                        identifier=f"INTRMY-{slot}-ALT-001",
                        scheme_kind="proprietary",
                        scheme_value="BANK",
                        issuer=None,
                    ),
                    "other-absent": self._payload(
                        slot,
                        identifier=None,
                        scheme_kind=None,
                        scheme_value=None,
                        issuer=None,
                    ),
                }

                for variant_name, payload in variants.items():
                    with self.subTest(variant=variant_name):
                        statement = self.case._statement(payload)
                        entry = statement.entries[1]
                        detail = entry.entry_details[0]
                        digest = getattr(detail, digest_key, None)
                        self.case._assert_digest(digest)
                        self.case._assert_digest(detail.source_detail_hash)
                        self.assertNotEqual(base_digest, digest)
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
                        self.case._assert_exact_amount(entry, detail)

    def test_other_identification_layout_is_representation_only(self) -> None:
        """Whitespace inside Othr changes raw bytes without changing semantics."""
        needle = b"                  <Othr>\n"
        whitespace = b"                    \n"

        for slot in (1, 2, 3):
            with self.subTest(slot=slot):
                payload = self._payload(
                    slot,
                    identifier=f"INTRMY-{slot}-ALT-001",
                    scheme_kind="proprietary",
                    scheme_value="BANK",
                    issuer="CWL Bank Registry",
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
                digest_key = f"intermediary_agent_{slot}_evidence_hash"
                base_digest = getattr(base_detail, digest_key, None)
                changed_digest = getattr(changed_detail, digest_key, None)
                self.case._assert_digest(base_digest)
                self.case._assert_digest(changed_digest)
                self.case._assert_digest(base_detail.source_detail_hash)
                self.case._assert_digest(changed_detail.source_detail_hash)

                self.assertNotEqual(base.source_artifact_hash, changed.source_artifact_hash)
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
                self.case._assert_exact_amount(changed_entry, changed_detail)

    def test_other_identification_change_requires_explicit_correction(self) -> None:
        """Accepted intermediary alternate identity cannot be replaced on replay."""
        slot = 3
        base_payload = self._payload(
            slot,
            identifier="INTRMY-3-ALT-001",
            scheme_kind="proprietary",
            scheme_value="BANK",
            issuer="CWL Bank Registry",
        )
        changed_payload = self._payload(
            slot,
            identifier="INTRMY-3-ALT-001",
            scheme_kind="code",
            scheme_value="BANK",
            issuer="CWL Bank Registry",
        )
        statement = self.case._statement(base_payload)
        account_reference = self.case._register_account(statement, "other-correction")
        store = MemoryArtifactStore()
        accepted = deep.accept_bank_statement_evidence(
            self.case._command(base_payload, account_reference, "other-base"),
            deep.posting.DATABASE_URL,
            self.case.case.policy.tenant_reference,
            artifact_store=store,
        )
        self.assertFalse(accepted["replayed"])
        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            deep.accept_bank_statement_evidence(
                self.case._command(
                    changed_payload,
                    account_reference,
                    "other-changed",
                ),
                deep.posting.DATABASE_URL,
                self.case.case.policy.tenant_reference,
                artifact_store=store,
            )

    def test_other_identification_stays_digest_only_on_buyer_projection(self) -> None:
        """Buyer reads expose no reversible intermediary alternate-ID fields."""
        for slot in (1, 2, 3):
            with self.subTest(slot=slot):
                proprietary_payload = self._payload(
                    slot,
                    identifier=f"INTRMY-{slot}-ALT-001",
                    scheme_kind="proprietary",
                    scheme_value="BANK",
                    issuer="CWL Bank Registry",
                )
                coded_payload = self._payload(
                    slot,
                    identifier=f"INTRMY-{slot}-ALT-001",
                    scheme_kind="code",
                    scheme_value="BANK",
                    issuer="CWL Bank Registry",
                )
                proprietary_statement = self.case._statement(proprietary_payload)
                coded_statement = self.case._statement(coded_payload)
                proprietary_reference = self.case._register_account(
                    proprietary_statement,
                    f"other-proprietary-{slot}",
                )
                coded_reference = self.case._register_account(
                    coded_statement,
                    f"other-coded-{slot}",
                )
                proprietary_detail = self.case._ingest_and_read(
                    proprietary_payload,
                    proprietary_reference,
                    f"other-proprietary-{slot}-{uuid.uuid4().hex}",
                )
                coded_detail = self.case._ingest_and_read(
                    coded_payload,
                    coded_reference,
                    f"other-coded-{slot}-{uuid.uuid4().hex}",
                )
                digest_key = f"intermediary_agent_{slot}_evidence_hash"

                for projection in (proprietary_detail, coded_detail):
                    self.case._assert_digest(projection.get(digest_key))
                    self.case._assert_digest(projection.get("source_detail_hash"))
                    self.assertEqual(projection["detail_amount"], "6000")
                    self.assertEqual(projection["detail_currency_code"], "KRW")

                self.assertNotEqual(
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
        slot: int,
        *,
        identifier: str | None,
        scheme_kind: str | None,
        scheme_value: str | None,
        issuer: str | None,
    ) -> bytes:
        """Insert GenericFinancialIdentification1 after target institution Nm."""
        identity = self.case.AGENTS[slot]
        payload = self.case._with_chain(slot, identity)
        if identifier is None:
            if scheme_kind is not None or scheme_value is not None or issuer is not None:
                raise AssertionError("Othr absence cannot carry scheme or issuer")
            return payload

        needle = f"                  <Nm>{identity['name']}</Nm>\n".encode("utf-8")
        if payload.count(needle) != 1:
            raise AssertionError("target intermediary institution name must be unique")
        other = self._other_xml(
            identifier=identifier,
            scheme_kind=scheme_kind,
            scheme_value=scheme_value,
            issuer=issuer,
        )
        return payload.replace(needle, needle + other, 1)

    @staticmethod
    def _other_xml(
        *,
        identifier: str,
        scheme_kind: str | None,
        scheme_value: str | None,
        issuer: str | None,
    ) -> bytes:
        """Serialize GenericFinancialIdentification1 in V14 sequence order."""
        if not identifier:
            raise AssertionError("Othr requires Id")
        lines = [
            "                  <Othr>\n",
            f"                    <Id>{identifier}</Id>\n",
        ]
        if scheme_kind is not None or scheme_value is not None:
            if scheme_kind not in {"code", "proprietary"} or not scheme_value:
                raise AssertionError("SchmeNm requires code|proprietary plus a value")
            tag = "Cd" if scheme_kind == "code" else "Prtry"
            lines.extend(
                [
                    "                    <SchmeNm>\n",
                    f"                      <{tag}>{scheme_value}</{tag}>\n",
                    "                    </SchmeNm>\n",
                ]
            )
        if issuer is not None:
            lines.append(f"                    <Issr>{issuer}</Issr>\n")
        lines.append("                  </Othr>\n")
        return "".join(lines).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
