"""PostgreSQL REDs for camt.053 entry additional-information indicator evidence."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
import uuid

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
    lookup_bank_statement_entries,
    parse_bank_statement_payload,
)
from accounting_information_platform import bank_statement
from tests import test_postgres_posting as posting

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_ENTRY_ADDITIONAL_INFORMATION_INDICATOR_PURPOSE = (
    "camt.053.001.14/Stmt/Ntry/AddtlInfInd"
)
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementEntryAdditionalInformationIndicatorEvidenceRedTests(unittest.TestCase):
    """Retain cross-message detail provenance without making it accounting truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare source-real statements that isolate one AddtlInfInd block."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "        <BkTxCd>\n"
            "          <Domn>\n"
            "            <Cd>PMNT</Cd>\n"
            "            <Fmly>\n"
            "              <Cd>RCDT</Cd>\n"
            "              <SubFmlyCd>ESCT</SubFmlyCd>\n"
            "            </Fmly>\n"
            "          </Domn>\n"
            "        </BkTxCd>\n"
            "        <NtryDtls>\n"
        )
        self.assertEqual(fixture.count(marker), 1)

        self.first_payload = self._with_additional_information_indicator(
            fixture,
            marker,
            message_name_identifier="camt.054.001.14",
            message_identifier="DETAIL-MSG-2026-08-24-001",
        )
        self.changed_name_payload = self._with_additional_information_indicator(
            fixture,
            marker,
            message_name_identifier="camt.052.001.14",
            message_identifier="DETAIL-MSG-2026-08-24-001",
        )
        self.changed_identifier_payload = self._with_additional_information_indicator(
            fixture,
            marker,
            message_name_identifier="camt.054.001.14",
            message_identifier="DETAIL-MSG-2026-08-24-002",
        )

        formatting_anchor = (
            "        <AddtlInfInd>\n"
            "          <MsgNmId>camt.054.001.14</MsgNmId>\n"
            "          <MsgId>DETAIL-MSG-2026-08-24-001</MsgId>\n"
            "        </AddtlInfInd>\n"
        ).encode("utf-8")
        self.assertEqual(self.first_payload.count(formatting_anchor), 1)
        self.reformatted_payload = self.first_payload.replace(
            formatting_anchor,
            (
                "        <AddtlInfInd>\n"
                "          <MsgNmId>camt.054.001.14</MsgNmId>\n"
                "          <MsgId>\n"
                "            DETAIL-MSG-2026-08-24-001\n"
                "          </MsgId>\n"
                "        </AddtlInfInd>\n"
            ).encode("utf-8"),
            1,
        )

        self.first_statement = parse_bank_statement_payload(
            self.first_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_name_statement = parse_bank_statement_payload(
            self.changed_name_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_identifier_statement = parse_bank_statement_payload(
            self.changed_identifier_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.reformatted_statement = parse_bank_statement_payload(
            self.reformatted_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.first_statement.account_currency_code,
                "account_identifier_hash": self.first_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_cross_message_reference_semantics_are_material_entry_evidence(self) -> None:
        """Message name and message identity independently affect retained evidence."""
        variants = (
            (
                self.first_statement,
                self._expected_evidence_hash(
                    message_name_identifier="camt.054.001.14",
                    message_identifier="DETAIL-MSG-2026-08-24-001",
                ),
            ),
            (
                self.changed_name_statement,
                self._expected_evidence_hash(
                    message_name_identifier="camt.052.001.14",
                    message_identifier="DETAIL-MSG-2026-08-24-001",
                ),
            ),
            (
                self.changed_identifier_statement,
                self._expected_evidence_hash(
                    message_name_identifier="camt.054.001.14",
                    message_identifier="DETAIL-MSG-2026-08-24-002",
                ),
            ),
        )

        hashes: list[str] = []
        for statement, expected_hash in variants:
            with self.subTest(expected_hash=expected_hash):
                entry = statement.entries[0]
                self.assertRegex(expected_hash, _HASH_PATTERN)
                self.assertEqual(
                    getattr(
                        entry,
                        "entry_additional_information_indicator_evidence_hash",
                        None,
                    ),
                    expected_hash,
                )
                self._assert_entry_hash_binding(entry, expected_hash)
                hashes.append(expected_hash)

        self.assertEqual(len(set(hashes)), len(hashes))
        for changed_statement in (
            self.changed_name_statement,
            self.changed_identifier_statement,
        ):
            self.assertEqual(
                self.first_statement.account_identifier_hash,
                changed_statement.account_identifier_hash,
            )
            self.assertNotEqual(
                self.first_statement.entries[0].source_entry_hash,
                changed_statement.entries[0].source_entry_hash,
            )
            self.assertNotEqual(
                self.first_statement.normalized_payload_hash,
                changed_statement.normalized_payload_hash,
            )
            self.assertEqual(
                self.first_statement.entries[1].source_entry_hash,
                changed_statement.entries[1].source_entry_hash,
            )

    def test_xml_formatting_does_not_change_cross_message_reference_semantics(self) -> None:
        """Insignificant XML formatting changes only raw artifact identity."""
        expected_hash = self._expected_evidence_hash(
            message_name_identifier="camt.054.001.14",
            message_identifier="DETAIL-MSG-2026-08-24-001",
        )
        first_entry = self.first_statement.entries[0]
        reformatted_entry = self.reformatted_statement.entries[0]

        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(
                first_entry,
                "entry_additional_information_indicator_evidence_hash",
                None,
            ),
            expected_hash,
        )
        self.assertEqual(
            getattr(
                reformatted_entry,
                "entry_additional_information_indicator_evidence_hash",
                None,
            ),
            expected_hash,
        )
        self._assert_entry_hash_binding(first_entry, expected_hash)
        self._assert_entry_hash_binding(reformatted_entry, expected_hash)
        self.assertEqual(first_entry.source_entry_hash, reformatted_entry.source_entry_hash)
        self.assertEqual(
            self.first_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_cross_message_reference_requires_explicit_statement_correction(self) -> None:
        """Changed referenced-detail provenance cannot silently replay one statement."""
        accepted = accept_bank_statement_evidence(
            self._command(self.first_payload, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.changed_identifier_payload, "changed-identifier"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_preserves_cross_message_reference_evidence(self) -> None:
        """Supported reads expose the bank-reported reference without granting authority."""
        expected_hash = self._expected_evidence_hash(
            message_name_identifier="camt.054.001.14",
            message_identifier="DETAIL-MSG-2026-08-24-001",
        )
        accepted = accept_bank_statement_evidence(
            self._command(self.first_payload, "lookup"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        entry = document["bank_statement_entries"][0]

        self.assertEqual(
            entry.get("entry_additional_information_indicator_evidence_hash"),
            expected_hash,
        )
        self.assertEqual(
            entry.get("additional_information_indicator_evidence"),
            {
                "message_name_identifier": "camt.054.001.14",
                "message_identifier": "DETAIL-MSG-2026-08-24-001",
            },
        )

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Bind canonical entry identity to cross-message semantics, not raw XML."""
        projection = dict(bank_statement._entry_payload(entry))
        if (
            projection.get("entry_additional_information_indicator_evidence_hash")
            != expected_hash
        ):
            raise AssertionError(
                "canonical entry projection must carry the exact "
                "entry_additional_information_indicator_evidence_hash"
            )
        preimage = json.dumps(
            projection,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if entry.source_entry_hash != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must be the digest of the canonical entry projection"
            )

    @staticmethod
    def _expected_evidence_hash(
        *,
        message_name_identifier: str,
        message_identifier: str,
    ) -> str:
        """Digest the MessageIdentification2 semantics used by this focused RED."""
        preimage = json.dumps(
            {
                "evidence_type": _ENTRY_ADDITIONAL_INFORMATION_INDICATOR_PURPOSE,
                "message_name_identifier": message_name_identifier,
                "message_identifier": message_identifier,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _with_additional_information_indicator(
        fixture: str,
        marker: str,
        *,
        message_name_identifier: str,
        message_identifier: str,
    ) -> bytes:
        """Insert one MessageIdentification2 after the first entry transaction code."""
        return fixture.replace(
            marker,
            "        <BkTxCd>\n"
            "          <Domn>\n"
            "            <Cd>PMNT</Cd>\n"
            "            <Fmly>\n"
            "              <Cd>RCDT</Cd>\n"
            "              <SubFmlyCd>ESCT</SubFmlyCd>\n"
            "            </Fmly>\n"
            "          </Domn>\n"
            "        </BkTxCd>\n"
            "        <AddtlInfInd>\n"
            f"          <MsgNmId>{message_name_identifier}</MsgNmId>\n"
            f"          <MsgId>{message_identifier}</MsgId>\n"
            "        </AddtlInfInd>\n"
            "        <NtryDtls>\n",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"entry-additional-information-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
