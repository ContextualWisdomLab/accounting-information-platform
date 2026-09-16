"""PostgreSQL REDs for camt.053 transaction-detail local-instrument evidence."""

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
_LOCAL_INSTRUMENT_PURPOSE = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/LclInstrm"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailLocalInstrumentEvidenceRedTests(unittest.TestCase):
    """Retain the direct EntryTransaction16 LocalInstrument2Choice as source evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one-field and choice-discriminator variants with accounting facts fixed."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "            </RltdPties>\n            <RmtInf>"
        self.assertEqual(fixture.count(marker), 1)

        self.base_choice = "code"
        self.base_value = "CORE"
        self.changed_value = "B2B"
        self.proprietary_choice = "proprietary"
        self.proprietary_value = self.base_value

        self.base_payload = self._with_local_instrument(
            fixture, marker, self.base_choice, self.base_value
        )
        self.changed_value_payload = self._with_local_instrument(
            fixture, marker, self.base_choice, self.changed_value
        )
        self.proprietary_payload = self._with_local_instrument(
            fixture, marker, self.proprietary_choice, self.proprietary_value
        )

        local_xml = self._local_instrument_xml(self.base_choice, self.base_value)
        self.assertEqual(self.base_payload.count(local_xml.encode("utf-8")), 1)
        reformatted_xml = local_xml.replace(
            "            <LclInstrm>\n",
            "            <LclInstrm>\n              \n",
            1,
        )
        self.reformatted_payload = self.base_payload.replace(
            local_xml.encode("utf-8"), reformatted_xml.encode("utf-8"), 1
        )

        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_value_statement = parse_bank_statement_payload(
            self.changed_value_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.proprietary_statement = parse_bank_statement_payload(
            self.proprietary_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.reformatted_statement = parse_bank_statement_payload(
            self.reformatted_payload, CAMT053_MESSAGE_DEFINITION
        )

        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.base_statement.account_currency_code,
                "account_identifier_hash": self.base_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_local_instrument_value_and_choice_are_material_evidence(self) -> None:
        """Both the LocalInstrument2Choice discriminator and value affect evidence identity."""
        variants = (
            (self.base_statement, self.base_choice, self.base_value),
            (self.changed_value_statement, self.base_choice, self.changed_value),
            (self.proprietary_statement, self.proprietary_choice, self.proprietary_value),
        )
        expected_hashes = [
            self._expected_hash(choice, value) for _statement, choice, value in variants
        ]
        self.assertEqual(len(set(expected_hashes)), len(expected_hashes))

        for (statement, _choice, _value), expected_hash in zip(
            variants, expected_hashes, strict=True
        ):
            with self.subTest(expected_hash=expected_hash):
                detail = statement.entries[0].entry_details[0]
                self.assertRegex(expected_hash, _HASH_PATTERN)
                self.assertEqual(
                    getattr(detail, "local_instrument_evidence_hash", None),
                    expected_hash,
                )
                self._assert_entry_hash_binding(statement.entries[0], expected_hash)

        for changed, _choice, _value in variants[1:]:
            self.assertEqual(
                self.base_statement.account_identifier_hash,
                changed.account_identifier_hash,
            )
            self.assertEqual(
                self.base_statement.entries[0].entry_amount,
                changed.entries[0].entry_amount,
            )
            self.assertEqual(
                self.base_statement.entries[0].entry_details[0].detail_amount,
                changed.entries[0].entry_details[0].detail_amount,
            )
            self.assertNotEqual(
                self.base_statement.entries[0].entry_details[0].source_detail_hash,
                changed.entries[0].entry_details[0].source_detail_hash,
            )
            self.assertNotEqual(
                self.base_statement.entries[0].source_entry_hash,
                changed.entries[0].source_entry_hash,
            )
            self.assertNotEqual(
                self.base_statement.normalized_payload_hash,
                changed.normalized_payload_hash,
            )
            self.assertEqual(
                self.base_statement.entries[1].source_entry_hash,
                changed.entries[1].source_entry_hash,
            )

    def test_xml_layout_does_not_change_local_instrument_semantics(self) -> None:
        """Element-layout whitespace changes raw provenance, not LocalInstrument2Choice identity."""
        expected = self._expected_hash(self.base_choice, self.base_value)
        base_detail = self.base_statement.entries[0].entry_details[0]
        reformatted_detail = self.reformatted_statement.entries[0].entry_details[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(base_detail, "local_instrument_evidence_hash", None), expected
        )
        self.assertEqual(
            getattr(reformatted_detail, "local_instrument_evidence_hash", None), expected
        )
        self._assert_entry_hash_binding(self.base_statement.entries[0], expected)
        self._assert_entry_hash_binding(self.reformatted_statement.entries[0], expected)
        self.assertEqual(base_detail.source_detail_hash, reformatted_detail.source_detail_hash)
        self.assertEqual(
            self.base_statement.entries[0].source_entry_hash,
            self.reformatted_statement.entries[0].source_entry_hash,
        )
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_local_instrument_requires_explicit_statement_correction(self) -> None:
        """Accepted local-clearing evidence cannot be replaced by silent replay."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        for suffix, payload in (
            ("code-value", self.changed_value_payload),
            ("choice", self.proprietary_payload),
        ):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                    accept_bank_statement_evidence(
                        self._command(payload, suffix),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_buyer_read_preserves_local_instrument_without_changing_amount_truth(self) -> None:
        """Tenant-scoped reads expose clearing provenance without promoting it to amount truth."""
        expected_hash = self._expected_hash(self.base_choice, self.base_value)
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "lookup"),
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
        detail = entry["entry_details"][0]

        self.assertEqual(detail.get("local_instrument_evidence_hash"), expected_hash)
        self.assertEqual(
            detail.get("local_instrument"),
            {"choice": self.base_choice, "value": self.base_value},
        )
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Require source_entry_hash to digest the purpose-bound local-instrument hash."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        first_detail = details[0]
        if not isinstance(first_detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if first_detail.get("local_instrument_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry the exact local_instrument_evidence_hash"
            )
        preimage = json.dumps(
            projection, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if getattr(entry, "source_entry_hash", None) != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must digest the canonical entry projection containing "
                "local_instrument_evidence_hash"
            )

    @staticmethod
    def _expected_hash(choice: str, value: str) -> str:
        """Digest the exact LocalInstrument2Choice discriminator and source value."""
        preimage = json.dumps(
            {
                "evidence_type": _LOCAL_INSTRUMENT_PURPOSE,
                "local_instrument": {"choice": choice, "value": value},
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _local_instrument_xml(choice: str, value: str) -> str:
        """Return schema-ordered direct EntryTransaction16 LclInstrm XML."""
        tag = {"code": "Cd", "proprietary": "Prtry"}[choice]
        return (
            "            <LclInstrm>\n"
            f"              <{tag}>{value}</{tag}>\n"
            "            </LclInstrm>\n"
        )

    @classmethod
    def _with_local_instrument(
        cls, fixture: str, marker: str, choice: str, value: str
    ) -> bytes:
        """Insert direct LclInstrm after related parties and before later TxDtls elements."""
        return fixture.replace(
            marker,
            "            </RltdPties>\n"
            + cls._local_instrument_xml(choice, value)
            + "            <RmtInf>",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"detail-local-instrument-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
