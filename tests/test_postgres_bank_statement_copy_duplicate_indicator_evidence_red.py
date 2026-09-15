"""PostgreSQL REDs for camt.053 statement copy/duplicate provenance."""

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
_COPY_DUPLICATE_EVIDENCE_PURPOSE = "camt.053.001.14/Stmt/CpyDplctInd"


class BankStatementCopyDuplicateIndicatorEvidenceRedTests(unittest.TestCase):
    """Retain bank-reported copy/duplicate status without granting accounting authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare lawful COPY/DUPL variants over one unchanged statement."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "      </FrToDt>\n      <Acct>\n"
        self.assertEqual(fixture.count(marker), 1)

        self.copy_payload = self._with_copy_duplicate_indicator(fixture, marker, "COPY")
        self.duplicate_payload = self._with_copy_duplicate_indicator(fixture, marker, "DUPL")

        formatting_anchor = (
            "      </FrToDt>\n"
            "      <CpyDplctInd>COPY</CpyDplctInd>\n"
            "      <Acct>\n"
        ).encode("utf-8")
        self.assertEqual(self.copy_payload.count(formatting_anchor), 1)
        self.reformatted_copy_payload = self.copy_payload.replace(
            formatting_anchor,
            (
                "      </FrToDt>\n"
                "      \n"
                "      <CpyDplctInd>COPY</CpyDplctInd>\n"
                "      <Acct>\n"
            ).encode("utf-8"),
            1,
        )

        self.copy_statement = parse_bank_statement_payload(
            self.copy_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.duplicate_statement = parse_bank_statement_payload(
            self.duplicate_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.reformatted_copy_statement = parse_bank_statement_payload(
            self.reformatted_copy_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.copy_statement.account_currency_code,
                "account_identifier_hash": self.copy_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_copy_duplicate_indicator_value_is_material_statement_evidence(self) -> None:
        """Changing only CpyDplctInd changes provenance and normalized statement evidence."""
        expected_copy = self._expected_copy_duplicate_hash("COPY")
        expected_duplicate = self._expected_copy_duplicate_hash("DUPL")
        copy_hash = getattr(self.copy_statement, "copy_duplicate_indicator_evidence_hash", None)
        duplicate_hash = getattr(
            self.duplicate_statement,
            "copy_duplicate_indicator_evidence_hash",
            None,
        )

        self.assertNotEqual(
            self.copy_statement.source_artifact_hash,
            self.duplicate_statement.source_artifact_hash,
        )
        self.assertEqual(
            self.copy_statement.account_identifier_hash,
            self.duplicate_statement.account_identifier_hash,
        )
        self.assertRegex(expected_copy, _HASH_PATTERN)
        self.assertRegex(expected_duplicate, _HASH_PATTERN)
        self.assertEqual(copy_hash, expected_copy)
        self.assertEqual(duplicate_hash, expected_duplicate)
        self.assertNotEqual(expected_copy, expected_duplicate)
        self._assert_normalized_hash_binding(self.copy_statement, expected_copy)
        self._assert_normalized_hash_binding(self.duplicate_statement, expected_duplicate)
        self.assertNotEqual(
            self.copy_statement.normalized_payload_hash,
            self.duplicate_statement.normalized_payload_hash,
        )

    def test_source_formatting_cannot_change_semantically_equal_copy_indicator(self) -> None:
        """Insignificant XML formatting must not leak into normalized COPY provenance."""
        expected_hash = self._expected_copy_duplicate_hash("COPY")
        self.assertNotEqual(
            self.copy_statement.source_artifact_hash,
            self.reformatted_copy_statement.source_artifact_hash,
        )
        self.assertEqual(
            self.copy_statement.account_identifier_hash,
            self.reformatted_copy_statement.account_identifier_hash,
        )
        self.assertEqual(
            getattr(self.copy_statement, "copy_duplicate_indicator_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(
                self.reformatted_copy_statement,
                "copy_duplicate_indicator_evidence_hash",
                None,
            ),
            expected_hash,
        )
        self._assert_normalized_hash_binding(self.copy_statement, expected_hash)
        self._assert_normalized_hash_binding(self.reformatted_copy_statement, expected_hash)
        self.assertEqual(
            self.copy_statement.normalized_payload_hash,
            self.reformatted_copy_statement.normalized_payload_hash,
        )

    def test_changed_copy_duplicate_indicator_requires_statement_correction(self) -> None:
        """The same statement identity cannot silently replay changed copy/duplicate status."""
        first = accept_bank_statement_evidence(
            self._command(self.copy_payload, "first"),
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
                self._command(self.duplicate_payload, "second"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_statement_lookup_exposes_same_purpose_bound_copy_duplicate_hash(self) -> None:
        """Buyer reads expose the exact copy/duplicate digest admitted at normalization."""
        expected_hash = self._expected_copy_duplicate_hash("COPY")
        self.assertEqual(
            getattr(self.copy_statement, "copy_duplicate_indicator_evidence_hash", None),
            expected_hash,
        )

        accepted = accept_bank_statement_evidence(
            self._command(self.copy_payload, "lookup"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        document = lookup_bank_statement(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        self.assertEqual(
            document.get("copy_duplicate_indicator_evidence_hash"),
            expected_hash,
        )

    @staticmethod
    def _assert_normalized_hash_binding(statement: object, copy_duplicate_hash: str) -> None:
        """Bind normalized identity to semantic copy/duplicate provenance, not raw bytes."""
        projection = dict(bank_statement._normalized_payload(statement))
        if projection.get("copy_duplicate_indicator_evidence_hash") != copy_duplicate_hash:
            raise AssertionError(
                "canonical normalized statement projection must carry the exact "
                "copy_duplicate_indicator_evidence_hash"
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
    def _expected_copy_duplicate_hash(value: str) -> str:
        """Return the digest of only the admitted CpyDplctInd semantics."""
        preimage = json.dumps(
            {
                "evidence_type": _COPY_DUPLICATE_EVIDENCE_PURPOSE,
                "value": value,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _with_copy_duplicate_indicator(fixture: str, marker: str, value: str) -> bytes:
        """Insert one lawful CopyDuplicate1Code immediately before Acct."""
        return fixture.replace(
            marker,
            "      </FrToDt>\n"
            f"      <CpyDplctInd>{value}</CpyDplctInd>\n"
            "      <Acct>\n",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"copy-duplicate-evidence-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
