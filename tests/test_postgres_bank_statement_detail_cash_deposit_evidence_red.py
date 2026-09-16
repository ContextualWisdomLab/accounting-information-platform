"""PostgreSQL REDs for camt.053 transaction-detail cash-deposit evidence."""

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
from tests import test_postgres_posting as posting

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_CASH_DEPOSIT_PURPOSE = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/CshDpst"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailCashDepositEvidenceRedTests(unittest.TestCase):
    """Retain repeatable CashDeposit1 evidence without promoting it to posting truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare cash-deposit variants while holding the transaction amount constant."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "            <RmtInf>\n"
            "              <Ustrd>Invoice 1001</Ustrd>\n"
            "            </RmtInf>"
        )
        self.assertEqual(fixture.count(marker), 1)

        self.base_records = (
            ("5000.00", "KRW", "3", "15000.00", "KRW"),
            ("10000.00", "KRW", "1", "10000.00", "KRW"),
        )
        self.changed_breakdown_records = (
            ("1000.00", "KRW", "15", "15000.00", "KRW"),
            self.base_records[1],
        )
        self.changed_reported_amount_records = (
            self.base_records[0],
            ("10000.00", "KRW", "2", "20000.00", "KRW"),
        )
        self.reordered_records = tuple(reversed(self.base_records))

        self.base_payload = self._with_cash_deposits(fixture, marker, self.base_records)
        self.changed_breakdown_payload = self._with_cash_deposits(
            fixture, marker, self.changed_breakdown_records
        )
        self.changed_reported_amount_payload = self._with_cash_deposits(
            fixture, marker, self.changed_reported_amount_records
        )
        self.reordered_payload = self._with_cash_deposits(
            fixture, marker, self.reordered_records
        )

        cash_deposit_xml = self._cash_deposit_xml(self.base_records)
        self.assertEqual(self.base_payload.count(cash_deposit_xml.encode("utf-8")), 1)
        reformatted = cash_deposit_xml.replace(
            "            <CshDpst>\n",
            "            <CshDpst>\n              \n",
            1,
        )
        self.reformatted_payload = self.base_payload.replace(
            cash_deposit_xml.encode("utf-8"),
            reformatted.encode("utf-8"),
            1,
        )

        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_breakdown_statement = parse_bank_statement_payload(
            self.changed_breakdown_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_reported_amount_statement = parse_bank_statement_payload(
            self.changed_reported_amount_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.reordered_statement = parse_bank_statement_payload(
            self.reordered_payload, CAMT053_MESSAGE_DEFINITION
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

    def test_cash_deposit_breakdown_is_material_detail_evidence(self) -> None:
        """Denomination/count composition and reported amounts must affect evidence identity."""
        expected = self._expected_hash(self.base_records)
        changed_breakdown = self._expected_hash(self.changed_breakdown_records)
        changed_reported_amount = self._expected_hash(self.changed_reported_amount_records)
        reordered = self._expected_hash(self.reordered_records)
        self.assertEqual(len({expected, changed_breakdown, changed_reported_amount, reordered}), 4)

        variants = (
            (self.base_statement, expected),
            (self.changed_breakdown_statement, changed_breakdown),
            (self.changed_reported_amount_statement, changed_reported_amount),
            (self.reordered_statement, reordered),
        )
        for statement, expected_hash in variants:
            with self.subTest(expected_hash=expected_hash):
                detail = statement.entries[0].entry_details[0]
                self.assertRegex(expected_hash, _HASH_PATTERN)
                self.assertEqual(
                    getattr(detail, "cash_deposit_evidence_hash", None),
                    expected_hash,
                )

        for changed in (
            self.changed_breakdown_statement,
            self.changed_reported_amount_statement,
            self.reordered_statement,
        ):
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

    def test_xml_layout_does_not_change_cash_deposit_semantics(self) -> None:
        """Whitespace is raw-artifact provenance, not CashDeposit1 semantic identity."""
        expected = self._expected_hash(self.base_records)
        base_detail = self.base_statement.entries[0].entry_details[0]
        reformatted_detail = self.reformatted_statement.entries[0].entry_details[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(getattr(base_detail, "cash_deposit_evidence_hash", None), expected)
        self.assertEqual(
            getattr(reformatted_detail, "cash_deposit_evidence_hash", None), expected
        )
        self.assertEqual(base_detail.source_detail_hash, reformatted_detail.source_detail_hash)
        self.assertEqual(
            self.base_statement.entries[0].source_entry_hash,
            self.reformatted_statement.entries[0].source_entry_hash,
        )
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_cash_deposit_requires_explicit_statement_correction(self) -> None:
        """Changed bank-reported note evidence must not silently replace accepted evidence."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        for suffix, changed_payload in (
            ("breakdown", self.changed_breakdown_payload),
            ("reported-amount", self.changed_reported_amount_payload),
            ("order", self.reordered_payload),
        ):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                    accept_bank_statement_evidence(
                        self._command(changed_payload, suffix),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_buyer_read_preserves_cash_deposit_records_without_recomputing_amount_truth(
        self,
    ) -> None:
        """Buyer reads expose source-ordered deposit evidence while transaction amounts stay distinct."""
        expected = self._expected_hash(self.base_records)
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

        self.assertEqual(detail.get("cash_deposit_evidence_hash"), expected)
        self.assertEqual(
            detail.get("cash_deposits"),
            [
                {
                    "note_denomination": {"amount": "5000.00", "currency_code": "KRW"},
                    "number_of_notes": "3",
                    "reported_amount": {"amount": "15000.00", "currency_code": "KRW"},
                },
                {
                    "note_denomination": {"amount": "10000.00", "currency_code": "KRW"},
                    "number_of_notes": "1",
                    "reported_amount": {"amount": "10000.00", "currency_code": "KRW"},
                },
            ],
        )
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    @staticmethod
    def _expected_hash(records: tuple[tuple[str, str, str, str, str], ...]) -> str:
        """Digest exact repeatable CashDeposit1 semantics in source order."""
        preimage = json.dumps(
            {
                "evidence_type": _CASH_DEPOSIT_PURPOSE,
                "cash_deposits": [
                    {
                        "note_denomination": {
                            "amount": denomination,
                            "currency_code": denomination_currency,
                        },
                        "number_of_notes": number_of_notes,
                        "reported_amount": {
                            "amount": reported_amount,
                            "currency_code": reported_currency,
                        },
                    }
                    for (
                        denomination,
                        denomination_currency,
                        number_of_notes,
                        reported_amount,
                        reported_currency,
                    ) in records
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _cash_deposit_xml(
        records: tuple[tuple[str, str, str, str, str], ...]
    ) -> str:
        """Return schema-ordered repeatable CashDeposit1 elements."""
        return "".join(
            (
                "            <CshDpst>\n"
                f"              <NoteDnmtn Ccy=\"{denomination_currency}\">{denomination}</NoteDnmtn>\n"
                f"              <NbOfNotes>{number_of_notes}</NbOfNotes>\n"
                f"              <Amt Ccy=\"{reported_currency}\">{reported_amount}</Amt>\n"
                "            </CshDpst>\n"
            )
            for (
                denomination,
                denomination_currency,
                number_of_notes,
                reported_amount,
                reported_currency,
            ) in records
        )

    @classmethod
    def _with_cash_deposits(
        cls,
        fixture: str,
        marker: str,
        records: tuple[tuple[str, str, str, str, str], ...],
    ) -> bytes:
        """Insert repeatable CashDeposit1 after remittance and before later optional TxDtls fields."""
        return fixture.replace(
            marker,
            marker + "\n" + cls._cash_deposit_xml(records).rstrip("\n"),
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"detail-cash-deposit-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
