"""PostgreSQL REDs for structured camt.053 remittance evidence preservation."""

from __future__ import annotations

import re
import unittest
import uuid
from decimal import Decimal

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
from tests import test_postgres_posting as posting

_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementStructuredRemittanceEvidenceRedTests(unittest.TestCase):
    """Retain source-ordered structured remittance as reconciliation evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare structured references and repeated referred-document evidence."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        self.marker = "<Ustrd>Invoice 1001</Ustrd>"
        self.assertEqual(fixture.count(self.marker), 1)

        self.first_payload = self._with_structured_reference(
            fixture,
            self.marker,
            "RF18539007547034",
        )
        self.second_payload = self._with_structured_reference(
            fixture,
            self.marker,
            "RF88539007547035",
        )
        self.first_statement = parse_bank_statement_payload(
            self.first_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.second_statement = parse_bank_statement_payload(
            self.second_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        self.first_document_number = "INV-2026-1001"
        self.base_second_document_number = "INV-2026-1002"
        self.changed_second_document_number = "INV-2026-1003"
        for value in (
            self.first_document_number,
            self.base_second_document_number,
            self.changed_second_document_number,
        ):
            if not 1 <= len(value) <= 35:
                raise AssertionError("referred document number must satisfy Max35Text")

        self.base_document_payload = self._with_referred_documents(
            fixture,
            self.marker,
            self.first_document_number,
            self.base_second_document_number,
        )
        self.changed_document_payload = self._with_referred_documents(
            fixture,
            self.marker,
            self.first_document_number,
            self.changed_second_document_number,
        )
        self.base_document_statement = parse_bank_statement_payload(
            self.base_document_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_document_statement = parse_bank_statement_payload(
            self.changed_document_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        self.base_structured_population_payload = self._with_repeated_structured_blocks(
            fixture,
            self.marker,
            self.first_document_number,
            self.base_second_document_number,
        )
        self.changed_structured_population_payload = self._with_repeated_structured_blocks(
            fixture,
            self.marker,
            self.first_document_number,
            self.changed_second_document_number,
        )
        self.base_structured_population_statement = parse_bank_statement_payload(
            self.base_structured_population_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_structured_population_statement = parse_bank_statement_payload(
            self.changed_structured_population_payload,
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

    def test_structured_creditor_reference_changes_canonical_hashes(self) -> None:
        """Changing only Strd/CdtrRefInf/Ref changes detail, entry, and statement identity."""
        first_detail = self.first_statement.entries[0].entry_details[0]
        second_detail = self.second_statement.entries[0].entry_details[0]

        self.assertNotEqual(first_detail.source_detail_hash, second_detail.source_detail_hash)
        self.assertNotEqual(
            self.first_statement.entries[0].source_entry_hash,
            self.second_statement.entries[0].source_entry_hash,
        )
        self.assertNotEqual(
            self.first_statement.normalized_payload_hash,
            self.second_statement.normalized_payload_hash,
        )

    def test_same_statement_identity_cannot_replay_changed_structured_creditor_reference(
        self,
    ) -> None:
        """Changed structured remittance evidence requires correction, not silent replay."""
        accept_bank_statement_evidence(
            self._command(self.first_payload, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )

        with self.assertRaises(AccountingValidationError) as captured:
            accept_bank_statement_evidence(
                self._command(self.second_payload, "second"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )
        self.assertEqual(
            str(captured.exception),
            "statement identity already exists with different entry evidence. "
            "Use an explicit correction contract, then retry ingest.",
        )

    def test_later_referred_document_number_is_material_to_evidence_identity(self) -> None:
        """A later RfrdDocInf/Nb cannot alias when earlier remittance and accounting facts match."""
        base_entry = self.base_document_statement.entries[0]
        changed_entry = self.changed_document_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]

        for value in (
            base_detail.source_detail_hash,
            changed_detail.source_detail_hash,
            base_entry.source_entry_hash,
            changed_entry.source_entry_hash,
            self.base_document_statement.normalized_payload_hash,
            self.changed_document_statement.normalized_payload_hash,
            self.base_document_statement.account_identifier_hash,
            self.changed_document_statement.account_identifier_hash,
            self.base_document_statement.entries[1].source_entry_hash,
            self.changed_document_statement.entries[1].source_entry_hash,
        ):
            self._assert_sha256(value)

        self.assertNotEqual(base_detail.source_detail_hash, changed_detail.source_detail_hash)
        self.assertNotEqual(base_entry.source_entry_hash, changed_entry.source_entry_hash)
        self.assertNotEqual(
            self.base_document_statement.normalized_payload_hash,
            self.changed_document_statement.normalized_payload_hash,
        )
        self.assertEqual(
            self.base_document_statement.account_identifier_hash,
            self.changed_document_statement.account_identifier_hash,
        )
        self.assertEqual(
            self.base_document_statement.entries[1].source_entry_hash,
            self.changed_document_statement.entries[1].source_entry_hash,
        )
        self._assert_exact_amount(base_entry, base_detail)
        self._assert_exact_amount(changed_entry, changed_detail)

    def test_changed_later_referred_document_requires_explicit_statement_correction(self) -> None:
        """Changing a later referred invoice cannot silently replay one statement identity."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_document_payload, "document-base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.changed_document_payload, "document-changed"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_keeps_referred_document_numbers_in_source_order(self) -> None:
        """Reconciliation reads keep invoice references without changing exact amount truth."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_document_payload, "document-lookup"),
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
        text = detail.get("remittance_evidence_text")
        if not isinstance(text, str):
            raise AssertionError("buyer read must retain structured remittance evidence")
        self.assertIn(self.first_document_number, text)
        self.assertIn(self.base_second_document_number, text)
        self.assertLess(
            text.index(self.first_document_number),
            text.index(self.base_second_document_number),
        )
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def test_later_structured_block_is_material_to_evidence_identity(self) -> None:
        """A later Strd block cannot alias when the earlier structured remittance is unchanged."""
        base_entry = self.base_structured_population_statement.entries[0]
        changed_entry = self.changed_structured_population_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]

        for value in (
            base_detail.source_detail_hash,
            changed_detail.source_detail_hash,
            base_entry.source_entry_hash,
            changed_entry.source_entry_hash,
            self.base_structured_population_statement.normalized_payload_hash,
            self.changed_structured_population_statement.normalized_payload_hash,
            self.base_structured_population_statement.account_identifier_hash,
            self.changed_structured_population_statement.account_identifier_hash,
            self.base_structured_population_statement.entries[1].source_entry_hash,
            self.changed_structured_population_statement.entries[1].source_entry_hash,
        ):
            self._assert_sha256(value)

        self.assertNotEqual(base_detail.source_detail_hash, changed_detail.source_detail_hash)
        self.assertNotEqual(base_entry.source_entry_hash, changed_entry.source_entry_hash)
        self.assertNotEqual(
            self.base_structured_population_statement.normalized_payload_hash,
            self.changed_structured_population_statement.normalized_payload_hash,
        )
        self.assertEqual(
            self.base_structured_population_statement.account_identifier_hash,
            self.changed_structured_population_statement.account_identifier_hash,
        )
        self.assertEqual(
            self.base_structured_population_statement.entries[1].source_entry_hash,
            self.changed_structured_population_statement.entries[1].source_entry_hash,
        )
        self._assert_exact_amount(base_entry, base_detail)
        self._assert_exact_amount(changed_entry, changed_detail)

    def test_changed_later_structured_block_requires_explicit_statement_correction(self) -> None:
        """Changing a later Strd block cannot silently replay one statement identity."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_structured_population_payload, "strd-population-base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(
                    self.changed_structured_population_payload,
                    "strd-population-changed",
                ),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_keeps_repeated_structured_blocks_in_source_order(self) -> None:
        """Reconciliation reads keep repeated Strd blocks in source order."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_structured_population_payload, "strd-population-lookup"),
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
        text = detail.get("remittance_evidence_text")
        if not isinstance(text, str):
            raise AssertionError("buyer read must retain repeated structured remittance evidence")
        self.assertIn(self.first_document_number, text)
        self.assertIn(self.base_second_document_number, text)
        self.assertLess(
            text.index(self.first_document_number),
            text.index(self.base_second_document_number),
        )
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    @staticmethod
    def _with_structured_reference(fixture: str, marker: str, reference: str) -> bytes:
        """Insert one standards-shaped SCOR creditor reference beside existing Ustrd evidence."""
        structured = (
            f"{marker}\n"
            "              <Strd>\n"
            "                <CdtrRefInf>\n"
            "                  <Tp>\n"
            "                    <CdOrPrtry>\n"
            "                      <Cd>SCOR</Cd>\n"
            "                    </CdOrPrtry>\n"
            "                    <Issr>ISO</Issr>\n"
            "                  </Tp>\n"
            f"                  <Ref>{reference}</Ref>\n"
            "                </CdtrRefInf>\n"
            "              </Strd>"
        )
        return fixture.replace(marker, structured, 1).encode("utf-8")

    @staticmethod
    def _with_referred_documents(
        fixture: str,
        marker: str,
        first_document_number: str,
        second_document_number: str,
    ) -> bytes:
        """Insert two source-ordered commercial-invoice references into one Strd block."""
        structured = (
            f"{marker}\n"
            "              <Strd>\n"
            "                <RfrdDocInf>\n"
            "                  <Tp>\n"
            "                    <CdOrPrtry>\n"
            "                      <Cd>CINV</Cd>\n"
            "                    </CdOrPrtry>\n"
            "                  </Tp>\n"
            f"                  <Nb>{first_document_number}</Nb>\n"
            "                </RfrdDocInf>\n"
            "                <RfrdDocInf>\n"
            "                  <Tp>\n"
            "                    <CdOrPrtry>\n"
            "                      <Cd>CINV</Cd>\n"
            "                    </CdOrPrtry>\n"
            "                  </Tp>\n"
            f"                  <Nb>{second_document_number}</Nb>\n"
            "                </RfrdDocInf>\n"
            "              </Strd>"
        )
        return fixture.replace(marker, structured, 1).encode("utf-8")

    @staticmethod
    def _with_repeated_structured_blocks(
        fixture: str,
        marker: str,
        first_document_number: str,
        second_document_number: str,
    ) -> bytes:
        """Insert two source-ordered Strd blocks with one invoice reference in each."""
        structured = (
            f"{marker}\n"
            "              <Strd>\n"
            "                <RfrdDocInf>\n"
            "                  <Tp>\n"
            "                    <CdOrPrtry>\n"
            "                      <Cd>CINV</Cd>\n"
            "                    </CdOrPrtry>\n"
            "                  </Tp>\n"
            f"                  <Nb>{first_document_number}</Nb>\n"
            "                </RfrdDocInf>\n"
            "              </Strd>\n"
            "              <Strd>\n"
            "                <RfrdDocInf>\n"
            "                  <Tp>\n"
            "                    <CdOrPrtry>\n"
            "                      <Cd>CINV</Cd>\n"
            "                    </CdOrPrtry>\n"
            "                  </Tp>\n"
            f"                  <Nb>{second_document_number}</Nb>\n"
            "                </RfrdDocInf>\n"
            "              </Strd>"
        )
        return fixture.replace(marker, structured, 1).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"structured-remittance-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

    def _assert_sha256(self, value: object) -> None:
        """Require canonical digest syntax before equality comparisons."""
        if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
            raise AssertionError("evidence hash must be canonical SHA-256")

    @staticmethod
    def _assert_exact_amount(entry: object, detail: object) -> None:
        """Keep remittance evidence separate from exact accounting amount truth."""
        if getattr(entry, "entry_amount", None) != Decimal("25000.00"):
            raise AssertionError("entry amount must remain exactly 25000.00")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("entry currency must remain KRW")
        if getattr(detail, "detail_amount", None) != Decimal("25000.00"):
            raise AssertionError("detail amount must remain exactly 25000.00")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("detail currency must remain KRW")


if __name__ == "__main__":
    unittest.main()
