"""PostgreSQL REDs for camt.053 statement reporting-source evidence."""

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
_REPORTING_SOURCE_EVIDENCE_PURPOSE = "camt.053.001.14/Stmt/RptgSrc"


class BankStatementReportingSourceEvidenceRedTests(unittest.TestCase):
    """Retain statement reporting-source provenance without changing accounting authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare lawful reporting-source choices over one unchanged statement."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "      <Acct>\n"
        self.assertEqual(fixture.count(marker), 1)

        self.accounting_code_payload = self._with_reporting_source(
            fixture,
            marker,
            choice="Cd",
            value="ACCT",
        )
        self.custody_code_payload = self._with_reporting_source(
            fixture,
            marker,
            choice="Cd",
            value="CUST",
        )
        self.proprietary_payload = self._with_reporting_source(
            fixture,
            marker,
            choice="Prtry",
            value="ACCT",
        )

        formatting_anchor = (
            "      <RptgSrc>\n"
            "        <Cd>ACCT</Cd>\n"
            "      </RptgSrc>\n"
        ).encode("utf-8")
        self.assertEqual(self.accounting_code_payload.count(formatting_anchor), 1)
        self.reformatted_accounting_code_payload = self.accounting_code_payload.replace(
            formatting_anchor,
            (
                "      <RptgSrc>\n"
                "        \n"
                "        <Cd>ACCT</Cd>\n"
                "      </RptgSrc>\n"
            ).encode("utf-8"),
            1,
        )

        self.accounting_code_statement = parse_bank_statement_payload(
            self.accounting_code_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.custody_code_statement = parse_bank_statement_payload(
            self.custody_code_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.proprietary_statement = parse_bank_statement_payload(
            self.proprietary_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.reformatted_accounting_code_statement = parse_bank_statement_payload(
            self.reformatted_accounting_code_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.accounting_code_statement.account_currency_code,
                "account_identifier_hash": self.accounting_code_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_reporting_source_code_value_is_material_statement_evidence(self) -> None:
        """Changing only RptgSrc/Cd changes reporting-source and statement evidence."""
        self._assert_reporting_source_difference(
            self.accounting_code_statement,
            self.custody_code_statement,
            left_choice="Cd",
            left_value="ACCT",
            right_choice="Cd",
            right_value="CUST",
        )

    def test_reporting_source_choice_discriminator_is_material_when_text_matches(self) -> None:
        """RptgSrc/Cd and Prtry cannot provenance-alias merely because text matches."""
        self._assert_reporting_source_difference(
            self.accounting_code_statement,
            self.proprietary_statement,
            left_choice="Cd",
            left_value="ACCT",
            right_choice="Prtry",
            right_value="ACCT",
        )

    def test_source_formatting_cannot_change_semantically_equal_reporting_source(self) -> None:
        """Insignificant XML formatting must not leak into normalized source identity."""
        expected_hash = self._expected_reporting_source_hash("Cd", "ACCT")
        self.assertNotEqual(
            self.accounting_code_statement.source_artifact_hash,
            self.reformatted_accounting_code_statement.source_artifact_hash,
        )
        self.assertEqual(
            self.accounting_code_statement.account_identifier_hash,
            self.reformatted_accounting_code_statement.account_identifier_hash,
        )
        self.assertEqual(
            getattr(self.accounting_code_statement, "reporting_source_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(
                self.reformatted_accounting_code_statement,
                "reporting_source_evidence_hash",
                None,
            ),
            expected_hash,
        )
        self._assert_normalized_hash_binding(
            self.accounting_code_statement,
            expected_hash,
        )
        self._assert_normalized_hash_binding(
            self.reformatted_accounting_code_statement,
            expected_hash,
        )
        self.assertEqual(
            self.accounting_code_statement.normalized_payload_hash,
            self.reformatted_accounting_code_statement.normalized_payload_hash,
        )

    def test_changed_reporting_source_requires_statement_correction(self) -> None:
        """The same statement identity cannot silently replay changed source provenance."""
        first = accept_bank_statement_evidence(
            self._command(self.accounting_code_payload, "first"),
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
                self._command(self.custody_code_payload, "second"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_statement_lookup_exposes_same_purpose_bound_reporting_source_hash(self) -> None:
        """Buyer reads expose the exact reporting-source digest admitted at normalization."""
        expected_hash = self._expected_reporting_source_hash("Cd", "ACCT")
        self.assertEqual(
            getattr(self.accounting_code_statement, "reporting_source_evidence_hash", None),
            expected_hash,
        )

        accepted = accept_bank_statement_evidence(
            self._command(self.accounting_code_payload, "lookup"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        document = lookup_bank_statement(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        self.assertEqual(document.get("reporting_source_evidence_hash"), expected_hash)

    def _assert_reporting_source_difference(
        self,
        left: object,
        right: object,
        *,
        left_choice: str,
        left_value: str,
        right_choice: str,
        right_value: str,
    ) -> None:
        """Bind reporting-source and statement digests to exact parsed semantics."""
        expected_left = self._expected_reporting_source_hash(left_choice, left_value)
        expected_right = self._expected_reporting_source_hash(right_choice, right_value)
        left_hash = getattr(left, "reporting_source_evidence_hash", None)
        right_hash = getattr(right, "reporting_source_evidence_hash", None)

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
    def _assert_normalized_hash_binding(statement: object, reporting_source_hash: str) -> None:
        """Bind normalized statement identity to semantic reporting-source evidence only."""
        projection = dict(bank_statement._normalized_payload(statement))
        if projection.get("reporting_source_evidence_hash") != reporting_source_hash:
            raise AssertionError(
                "canonical normalized statement projection must carry the exact "
                "reporting_source_evidence_hash"
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
    def _expected_reporting_source_hash(choice: str, value: str) -> str:
        """Return the digest of only the admitted RptgSrc choice semantics."""
        preimage = json.dumps(
            {
                "choice": choice,
                "evidence_type": _REPORTING_SOURCE_EVIDENCE_PURPOSE,
                "value": value,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _with_reporting_source(
        fixture: str,
        marker: str,
        *,
        choice: str,
        value: str,
    ) -> bytes:
        """Insert one lawful ReportingSource1Choice immediately before Acct."""
        return fixture.replace(
            marker,
            "      <RptgSrc>\n"
            f"        <{choice}>{value}</{choice}>\n"
            "      </RptgSrc>\n"
            "      <Acct>\n",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"reporting-source-evidence-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
