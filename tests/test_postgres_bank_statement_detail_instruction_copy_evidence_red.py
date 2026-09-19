"""PostgreSQL REDs for camt.053 transaction-detail instruction-copy evidence."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
import uuid
from xml.sax.saxutils import escape

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
_INSTRUCTION_COPY_PATH = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/InstrCpy"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailInstructionCopyEvidenceRedTests(unittest.TestCase):
    """Retain bank-reported instruction copies as evidence, never posting authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare semantically equal and materially different InstructionCopy payloads."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        self.marker = (
            "            <RmtInf>\n"
            "              <Ustrd>Invoice 1001</Ustrd>\n"
            "            </RmtInf>\n"
            "          </TxDtls>"
        )
        self.assertEqual(fixture.count(self.marker), 1)

        self.base_instruction_copy = (
            "<Document><GrpHdr><MsgId>ORIG-001</MsgId></GrpHdr></Document>"
        )
        self.changed_instruction_copy = (
            "<Document><GrpHdr><MsgId>ORIG-002</MsgId></GrpHdr></Document>"
        )
        self.base_payload = self._with_instruction_copy(
            fixture, self.base_instruction_copy, cdata=False
        )
        self.changed_payload = self._with_instruction_copy(
            fixture, self.changed_instruction_copy, cdata=False
        )
        self.cdata_payload = self._with_instruction_copy(
            fixture, self.base_instruction_copy, cdata=True
        )

        self.base_statement = self._parse(self.base_payload)
        self.changed_statement = self._parse(self.changed_payload)
        self.cdata_statement = self._parse(self.cdata_payload)

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

    def test_instruction_copy_is_material_to_detail_entry_and_statement_identity(self) -> None:
        """Changing only InstrCpy changes its digest and enclosing semantic identities."""
        base_hash = self._expected_hash(self.base_instruction_copy)
        changed_hash = self._expected_hash(self.changed_instruction_copy)
        base_entry = self.base_statement.entries[0]
        changed_entry = self.changed_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]

        self.assertRegex(base_hash, _HASH_PATTERN)
        self.assertRegex(changed_hash, _HASH_PATTERN)
        self.assertNotEqual(base_hash, changed_hash)
        self.assertEqual(
            getattr(base_detail, "instruction_copy_evidence_hash", None), base_hash
        )
        self.assertEqual(
            getattr(changed_detail, "instruction_copy_evidence_hash", None), changed_hash
        )
        self._assert_entry_hash_binding(base_entry, base_hash)
        self._assert_entry_hash_binding(changed_entry, changed_hash)

        self.assertEqual(
            self.base_statement.account_identifier_hash,
            self.changed_statement.account_identifier_hash,
        )
        self.assertEqual(base_entry.entry_amount, changed_entry.entry_amount)
        self.assertEqual(base_detail.detail_amount, changed_detail.detail_amount)
        self.assertEqual(base_entry.entry_currency_code, changed_entry.entry_currency_code)
        self.assertEqual(base_detail.detail_currency_code, changed_detail.detail_currency_code)
        self.assertNotEqual(base_detail.source_detail_hash, changed_detail.source_detail_hash)
        self.assertNotEqual(base_entry.source_entry_hash, changed_entry.source_entry_hash)
        self.assertNotEqual(
            self.base_statement.normalized_payload_hash,
            self.changed_statement.normalized_payload_hash,
        )
        self.assertEqual(
            self.base_statement.entries[1].source_entry_hash,
            self.changed_statement.entries[1].source_entry_hash,
        )

    def test_text_and_cdata_representations_have_one_instruction_copy_identity(self) -> None:
        """Equivalent XML text and CDATA representations retain one semantic identity."""
        expected_hash = self._expected_hash(self.base_instruction_copy)
        base_entry = self.base_statement.entries[0]
        cdata_entry = self.cdata_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        cdata_detail = cdata_entry.entry_details[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.cdata_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(base_detail, "instruction_copy_evidence_hash", None), expected_hash
        )
        self.assertEqual(
            getattr(cdata_detail, "instruction_copy_evidence_hash", None), expected_hash
        )
        self._assert_entry_hash_binding(base_entry, expected_hash)
        self._assert_entry_hash_binding(cdata_entry, expected_hash)
        self.assertEqual(base_detail.source_detail_hash, cdata_detail.source_detail_hash)
        self.assertEqual(base_entry.source_entry_hash, cdata_entry.source_entry_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.cdata_statement.normalized_payload_hash,
        )

    def test_changed_instruction_copy_requires_explicit_statement_correction(self) -> None:
        """An accepted original-instruction copy cannot be silently replaced on replay."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.changed_payload, "changed"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_preserves_instruction_copy_without_changing_accounting_amount(self) -> None:
        """Tenant reads expose exact instruction evidence while accounting amount stays reported."""
        expected_hash = self._expected_hash(self.base_instruction_copy)
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

        self.assertEqual(detail.get("instruction_copy"), self.base_instruction_copy)
        self.assertEqual(detail.get("instruction_copy_evidence_hash"), expected_hash)
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    @staticmethod
    def _parse(payload: bytes) -> object:
        """Parse one V14 statement through the supported adapter boundary."""
        return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    @staticmethod
    def _expected_hash(instruction_copy: str) -> str:
        """Digest the admitted InstructionCopy text under its exact source purpose."""
        preimage = json.dumps(
            {
                "evidence_type": _INSTRUCTION_COPY_PATH,
                "instruction_copy": instruction_copy,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Require source_entry_hash to digest the instruction-copy-bound projection."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        first_detail = details[0]
        if not isinstance(first_detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if first_detail.get("instruction_copy_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry the exact instruction_copy_evidence_hash"
            )
        preimage = json.dumps(
            projection, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if getattr(entry, "source_entry_hash", None) != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must digest the canonical entry projection containing "
                "instruction_copy_evidence_hash"
            )

    def _with_instruction_copy(
        self, fixture: str, value: str, *, cdata: bool
    ) -> bytes:
        """Insert one schema-positioned InstructionCopy after omitted optional siblings."""
        if cdata:
            if "]]>" in value:
                raise AssertionError("focused InstructionCopy cannot contain a CDATA terminator")
            serialized = f"<![CDATA[{value}]]>"
        else:
            serialized = escape(value)
        replacement = (
            "            <RmtInf>\n"
            "              <Ustrd>Invoice 1001</Ustrd>\n"
            "            </RmtInf>\n"
            f"            <InstrCpy>{serialized}</InstrCpy>\n"
            "          </TxDtls>"
        )
        self.assertEqual(fixture.count(self.marker), 1)
        return fixture.replace(self.marker, replacement, 1).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh tenant-scoped replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"instruction-copy-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
