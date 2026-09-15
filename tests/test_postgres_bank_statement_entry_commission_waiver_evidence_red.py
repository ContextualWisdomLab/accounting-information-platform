"""PostgreSQL REDs for camt.053 entry commission-waiver provenance."""

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
_ENTRY_COMMISSION_WAIVER_PURPOSE = "camt.053.001.14/Stmt/Ntry/ComssnWvrInd"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementEntryCommissionWaiverEvidenceRedTests(unittest.TestCase):
    """Retain bank-reported commission-waiver evidence without granting posting authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare source-real statements that isolate one ComssnWvrInd value."""
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

        self.waived_payload = self._with_commission_waiver(fixture, marker, "true")
        self.not_waived_payload = self._with_commission_waiver(fixture, marker, "false")
        self.numeric_true_payload = self._with_commission_waiver(fixture, marker, "1")

        self.waived_statement = parse_bank_statement_payload(
            self.waived_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.not_waived_statement = parse_bank_statement_payload(
            self.not_waived_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.numeric_true_statement = parse_bank_statement_payload(
            self.numeric_true_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.waived_statement.account_currency_code,
                "account_identifier_hash": self.waived_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_commission_waiver_semantics_are_material_entry_evidence(self) -> None:
        """Bank-reported true and false values must not collapse to one entry identity."""
        waived_hash = self._expected_evidence_hash(True)
        not_waived_hash = self._expected_evidence_hash(False)
        waived_entry = self.waived_statement.entries[0]
        not_waived_entry = self.not_waived_statement.entries[0]

        self.assertRegex(waived_hash, _HASH_PATTERN)
        self.assertRegex(not_waived_hash, _HASH_PATTERN)
        self.assertNotEqual(waived_hash, not_waived_hash)
        self.assertEqual(
            getattr(waived_entry, "entry_commission_waiver_evidence_hash", None),
            waived_hash,
        )
        self.assertEqual(
            getattr(not_waived_entry, "entry_commission_waiver_evidence_hash", None),
            not_waived_hash,
        )
        self.assertIs(getattr(waived_entry, "commission_waiver_indicator", None), True)
        self.assertIs(getattr(not_waived_entry, "commission_waiver_indicator", None), False)
        self._assert_entry_hash_binding(waived_entry, waived_hash)
        self._assert_entry_hash_binding(not_waived_entry, not_waived_hash)

        self.assertEqual(
            self.waived_statement.account_identifier_hash,
            self.not_waived_statement.account_identifier_hash,
        )
        self.assertNotEqual(waived_entry.source_entry_hash, not_waived_entry.source_entry_hash)
        self.assertNotEqual(
            self.waived_statement.normalized_payload_hash,
            self.not_waived_statement.normalized_payload_hash,
        )
        self.assertEqual(
            self.waived_statement.entries[1].source_entry_hash,
            self.not_waived_statement.entries[1].source_entry_hash,
        )

    def test_boolean_lexical_forms_share_one_commission_waiver_semantic(self) -> None:
        """XML Schema boolean true and 1 must normalize to the same evidence value."""
        expected_hash = self._expected_evidence_hash(True)
        text_entry = self.waived_statement.entries[0]
        numeric_entry = self.numeric_true_statement.entries[0]

        self.assertNotEqual(
            self.waived_statement.source_artifact_hash,
            self.numeric_true_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(text_entry, "entry_commission_waiver_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(numeric_entry, "entry_commission_waiver_evidence_hash", None),
            expected_hash,
        )
        self.assertIs(getattr(text_entry, "commission_waiver_indicator", None), True)
        self.assertIs(getattr(numeric_entry, "commission_waiver_indicator", None), True)
        self._assert_entry_hash_binding(text_entry, expected_hash)
        self._assert_entry_hash_binding(numeric_entry, expected_hash)
        self.assertEqual(text_entry.source_entry_hash, numeric_entry.source_entry_hash)
        self.assertEqual(
            self.waived_statement.normalized_payload_hash,
            self.numeric_true_statement.normalized_payload_hash,
        )

    def test_changed_commission_waiver_requires_explicit_statement_correction(self) -> None:
        """A material waiver change cannot silently replay the same statement identity."""
        accepted = accept_bank_statement_evidence(
            self._command(self.waived_payload, "waived"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.not_waived_payload, "not-waived"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_preserves_commission_waiver_provenance(self) -> None:
        """Supported entry reads expose the bank-reported flag and its purpose digest."""
        expected_hash = self._expected_evidence_hash(True)
        accepted = accept_bank_statement_evidence(
            self._command(self.waived_payload, "lookup"),
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

        self.assertEqual(entry.get("entry_commission_waiver_evidence_hash"), expected_hash)
        self.assertIs(entry.get("commission_waiver_indicator"), True)

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Bind canonical entry identity to the semantic waiver value, not XML spelling."""
        projection = dict(bank_statement._entry_payload(entry))
        if projection.get("entry_commission_waiver_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry the exact "
                "entry_commission_waiver_evidence_hash"
            )
        if projection.get("commission_waiver_indicator") is not getattr(
            entry, "commission_waiver_indicator", None
        ):
            raise AssertionError(
                "canonical entry projection must carry the normalized commission waiver boolean"
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
    def _expected_evidence_hash(commission_waiver_indicator: bool) -> str:
        """Digest the normalized YesNoIndicator semantic used by this focused RED."""
        preimage = json.dumps(
            {
                "evidence_type": _ENTRY_COMMISSION_WAIVER_PURPOSE,
                "commission_waiver_indicator": commission_waiver_indicator,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _with_commission_waiver(fixture: str, marker: str, lexical_value: str) -> bytes:
        """Insert one schema-valid ComssnWvrInd after the first entry transaction code."""
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
            f"        <ComssnWvrInd>{lexical_value}</ComssnWvrInd>\n"
            "        <NtryDtls>\n",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"entry-commission-waiver-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
