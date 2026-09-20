"""REDs for structured referred-document remitted-amount evidence preservation."""

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


class BankStatementStructuredRemittedAmountEvidenceRedTests(unittest.TestCase):
    """Retain RfrdDocAmt remitted amount as Bank Reconciliation evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare statements that differ only in the remitted amount."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "<Ustrd>Invoice 1001</Ustrd>"
        if fixture.count(marker) != 1:
            raise AssertionError("canonical Ustrd marker must occur exactly once")

        self.invoice_number = "INV-2026-1001"
        self.due_payable_amount = "26000"
        self.base_remitted_amount = "22922"
        self.changed_remitted_amount = "22923"
        self.base_payload = self._with_referred_document_amounts(
            fixture,
            marker,
            self.base_remitted_amount,
        )
        self.changed_payload = self._with_referred_document_amounts(
            fixture,
            marker,
            self.changed_remitted_amount,
        )
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        self.bank_account_reference = (
            f"urn:cwl:bank_account:structured-remitted:{uuid.uuid4().hex}"
        )
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

    def test_remitted_amount_is_material_to_evidence_identity(self) -> None:
        """A changed remitted amount must change only its owning evidence chain."""
        base_entry = self.base_statement.entries[0]
        changed_entry = self.changed_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]

        for value in (
            base_detail.source_detail_hash,
            changed_detail.source_detail_hash,
            base_entry.source_entry_hash,
            changed_entry.source_entry_hash,
            self.base_statement.normalized_payload_hash,
            self.changed_statement.normalized_payload_hash,
            self.base_statement.account_identifier_hash,
            self.changed_statement.account_identifier_hash,
            self.base_statement.entries[1].source_entry_hash,
            self.changed_statement.entries[1].source_entry_hash,
        ):
            self._assert_sha256(value)

        self.assertNotEqual(
            base_detail.source_detail_hash,
            changed_detail.source_detail_hash,
        )
        self.assertNotEqual(
            base_entry.source_entry_hash,
            changed_entry.source_entry_hash,
        )
        self.assertNotEqual(
            self.base_statement.normalized_payload_hash,
            self.changed_statement.normalized_payload_hash,
        )
        self.assertEqual(
            self.base_statement.account_identifier_hash,
            self.changed_statement.account_identifier_hash,
        )
        self.assertEqual(
            self.base_statement.entries[1].source_entry_hash,
            self.changed_statement.entries[1].source_entry_hash,
        )
        self._assert_exact_transaction_amount(base_entry, base_detail)
        self._assert_exact_transaction_amount(changed_entry, changed_detail)

    def test_changed_remitted_amount_requires_explicit_statement_correction(self) -> None:
        """Changing bank-reported remitted evidence cannot silently replay a statement."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "remitted-base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.changed_payload, "remitted-changed"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_keeps_referred_document_amounts_in_source_order(self) -> None:
        """Buyer reads retain invoice, due, and remitted amounts in source order."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "remitted-lookup"),
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
            raise AssertionError(
                "buyer read must retain referred-document remitted evidence"
            )

        source_order = re.compile(
            rf"{re.escape(self.invoice_number)}.*?"
            rf"{re.escape(self.due_payable_amount)}.*?"
            rf"{re.escape(self.base_remitted_amount)}",
            re.DOTALL,
        )
        self.assertRegex(text, source_order)
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _with_referred_document_amounts(
        self,
        fixture: str,
        marker: str,
        remitted_amount: str,
    ) -> bytes:
        """Insert one source-ordered due/remitted amount pair into RfrdDocAmt."""
        structured = (
            f"{marker}\n"
            "              <Strd>\n"
            "                <RfrdDocInf>\n"
            "                  <Tp>\n"
            "                    <CdOrPrtry>\n"
            "                      <Cd>CINV</Cd>\n"
            "                    </CdOrPrtry>\n"
            "                  </Tp>\n"
            f"                  <Nb>{self.invoice_number}</Nb>\n"
            "                </RfrdDocInf>\n"
            "                <RfrdDocAmt>\n"
            f'                  <DuePyblAmt Ccy="KRW">{self.due_payable_amount}</DuePyblAmt>\n'
            f'                  <RmtdAmt Ccy="KRW">{remitted_amount}</RmtdAmt>\n'
            "                </RfrdDocAmt>\n"
            "              </Strd>"
        )
        return fixture.replace(marker, structured, 1).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"structured-remitted-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

    @staticmethod
    def _assert_sha256(value: object) -> None:
        """Require canonical digest syntax before equality comparisons."""
        if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
            raise AssertionError("evidence hash must be canonical SHA-256")

    @staticmethod
    def _assert_exact_transaction_amount(entry: object, detail: object) -> None:
        """Keep bank-reported remitted amount separate from transaction amount truth."""
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
