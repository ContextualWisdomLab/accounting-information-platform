"""PostgreSQL REDs for camt.053 statement reporting-sequence evidence."""

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
    lookup_bank_statement,
    parse_bank_statement_payload,
)
from accounting_information_platform import bank_statement
from tests import test_postgres_posting as posting

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPORTING_SEQUENCE_EVIDENCE_PURPOSE = "camt.053.001.14/Stmt/RptgSeq"


class BankStatementReportingSequenceEvidenceRedTests(unittest.TestCase):
    """Retain statement reporting-sequence provenance without granting accounting authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare lawful reporting-sequence choices over one unchanged statement."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "      <LglSeqNb>7</LglSeqNb>\n"
        self.assertEqual(fixture.count(marker), 1)

        self.from_sequence_payload = self._with_reporting_sequence(
            fixture,
            marker,
            choice="FrSeq",
            value="100",
        )
        self.changed_from_sequence_payload = self._with_reporting_sequence(
            fixture,
            marker,
            choice="FrSeq",
            value="101",
        )
        self.to_sequence_payload = self._with_reporting_sequence(
            fixture,
            marker,
            choice="ToSeq",
            value="100",
        )

        formatting_anchor = (
            "      <RptgSeq>\n"
            "        <FrSeq>100</FrSeq>\n"
            "      </RptgSeq>\n"
        ).encode("utf-8")
        self.assertEqual(self.from_sequence_payload.count(formatting_anchor), 1)
        self.reformatted_from_sequence_payload = self.from_sequence_payload.replace(
            formatting_anchor,
            (
                "      <RptgSeq>\n"
                "        \n"
                "        <FrSeq>100</FrSeq>\n"
                "      </RptgSeq>\n"
            ).encode("utf-8"),
            1,
        )

        self.from_sequence_statement = parse_bank_statement_payload(
            self.from_sequence_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_from_sequence_statement = parse_bank_statement_payload(
            self.changed_from_sequence_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.to_sequence_statement = parse_bank_statement_payload(
            self.to_sequence_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.reformatted_from_sequence_statement = parse_bank_statement_payload(
            self.reformatted_from_sequence_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.from_sequence_statement.account_currency_code,
                "account_identifier_hash": self.from_sequence_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_reporting_sequence_value_is_material_statement_evidence(self) -> None:
        """Changing only RptgSeq/FrSeq changes reporting-sequence and statement evidence."""
        self._assert_reporting_sequence_difference(
            self.from_sequence_statement,
            self.changed_from_sequence_statement,
            left_choice="FrSeq",
            left_value="100",
            right_choice="FrSeq",
            right_value="101",
        )

    def test_reporting_sequence_choice_discriminator_is_material_when_text_matches(self) -> None:
        """FrSeq and ToSeq cannot provenance-alias merely because their text matches."""
        self._assert_reporting_sequence_difference(
            self.from_sequence_statement,
            self.to_sequence_statement,
            left_choice="FrSeq",
            left_value="100",
            right_choice="ToSeq",
            right_value="100",
        )

    def test_source_formatting_cannot_change_semantically_equal_reporting_sequence(self) -> None:
        """Insignificant XML formatting must not leak into normalized sequence identity."""
        expected_hash = self._expected_reporting_sequence_hash("FrSeq", "100")
        self.assertNotEqual(
            self.from_sequence_statement.source_artifact_hash,
            self.reformatted_from_sequence_statement.source_artifact_hash,
        )
        self.assertEqual(
            self.from_sequence_statement.account_identifier_hash,
            self.reformatted_from_sequence_statement.account_identifier_hash,
        )
        self.assertEqual(
            getattr(self.from_sequence_statement, "reporting_sequence_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(
                self.reformatted_from_sequence_statement,
                "reporting_sequence_evidence_hash",
                None,
            ),
            expected_hash,
        )
        self._assert_normalized_hash_binding(
            self.from_sequence_statement,
            expected_hash,
        )
        self._assert_normalized_hash_binding(
            self.reformatted_from_sequence_statement,
            expected_hash,
        )
        self.assertEqual(
            self.from_sequence_statement.normalized_payload_hash,
            self.reformatted_from_sequence_statement.normalized_payload_hash,
        )

    def test_changed_reporting_sequence_requires_statement_correction(self) -> None:
        """The same statement identity cannot silently replay changed sequence provenance."""
        first = accept_bank_statement_evidence(
            self._command(self.from_sequence_payload, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(first["replayed"])

        with self.assertRaisesRegex(
            AccountingValidationError,
            (
                r"^statement identity already exists with different entry evidence\. "
                r"Use an explicit correction contract, then retry ingest\.$"
            ),
        ):
            accept_bank_statement_evidence(
                self._command(self.changed_from_sequence_payload, "second"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_statement_lookup_exposes_same_purpose_bound_reporting_sequence_hash(self) -> None:
        """Buyer reads expose the exact reporting-sequence digest admitted at normalization."""
        expected_hash = self._expected_reporting_sequence_hash("FrSeq", "100")
        self.assertEqual(
            getattr(self.from_sequence_statement, "reporting_sequence_evidence_hash", None),
            expected_hash,
        )

        accepted = accept_bank_statement_evidence(
            self._command(self.from_sequence_payload, "lookup"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        document = lookup_bank_statement(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        self.assertEqual(document.get("reporting_sequence_evidence_hash"), expected_hash)

    def _assert_reporting_sequence_difference(
        self,
        left: object,
        right: object,
        *,
        left_choice: str,
        left_value: str,
        right_choice: str,
        right_value: str,
    ) -> None:
        """Bind reporting-sequence and statement digests to exact parsed semantics."""
        expected_left = self._expected_reporting_sequence_hash(left_choice, left_value)
        expected_right = self._expected_reporting_sequence_hash(right_choice, right_value)
        left_hash = getattr(left, "reporting_sequence_evidence_hash", None)
        right_hash = getattr(right, "reporting_sequence_evidence_hash", None)

        self.assertNotEqual(left.source_artifact_hash, right.source_artifact_hash)
        self.assertEqual(left.account_identifier_hash, right.account_identifier_hash)
        self.assertRegex(expected_left, _HASH_PATTERN)
        self.assertRegex(expected_right, _HASH_PATTERN)
        self.assertEqual(left_hash, expected_left)
        self.assertEqual(right_hash, expected_right)
        self.assertNotEqual(expected_left, expected_right)
        self._assert_normalized_hash_binding(left, expected_left)
        self._assert_normalized_hash_binding(right, expected_right)
        self.assertNotEqual(left.normalized_payload_hash, right.normalized_payload_hash)

    @staticmethod
    def _assert_normalized_hash_binding(statement: object, reporting_sequence_hash: str) -> None:
        """Bind normalized statement identity to semantic reporting-sequence evidence only."""
        projection = dict(bank_statement._normalized_payload(statement))
        if projection.get("reporting_sequence_evidence_hash") != reporting_sequence_hash:
            raise AssertionError(
                "canonical normalized statement projection must carry the exact "
                "reporting_sequence_evidence_hash"
            )
        if "source_artifact_hash" in projection:
            raise AssertionError(
                "canonical normalized statement projection must not carry raw artifact identity"
            )
        preimage = json.dumps(
            projection,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        expected_statement_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if statement.normalized_payload_hash != expected_statement_hash:
            raise AssertionError(
                "normalized_payload_hash must be the digest of the canonical normalized projection"
            )

    @staticmethod
    def _expected_reporting_sequence_hash(choice: str, value: str) -> str:
        """Return the digest of only the admitted RptgSeq choice semantics."""
        preimage = json.dumps(
            {
                "choice": choice,
                "evidence_type": _REPORTING_SEQUENCE_EVIDENCE_PURPOSE,
                "value": value,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _with_reporting_sequence(
        fixture: str,
        marker: str,
        *,
        choice: str,
        value: str,
    ) -> bytes:
        """Insert one lawful SequenceRange1Choice before LglSeqNb."""
        return fixture.replace(
            marker,
            "      <RptgSeq>\n"
            f"        <{choice}>{value}</{choice}>\n"
            "      </RptgSeq>\n"
            "      <LglSeqNb>7</LglSeqNb>\n",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"reporting-sequence-evidence-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
