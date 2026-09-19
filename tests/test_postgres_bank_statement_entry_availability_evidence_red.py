"""PostgreSQL REDs for camt.053 entry-availability evidence."""

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
_ENTRY_AVAILABILITY_EVIDENCE_PURPOSE = "camt.053.001.14/Stmt/Ntry/Avlbty"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementEntryAvailabilityEvidenceRedTests(unittest.TestCase):
    """Retain bank-reported funds availability without making it ledger truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare source-real statements that isolate one CashAvailability1 record."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "      <AcctSvcrRef>ASV-1</AcctSvcrRef>\n"
            "      <BkTxCd>\n"
        )
        self.assertEqual(fixture.count(marker), 1)

        self.first_payload = self._with_availability(
            fixture,
            marker,
            actual_date="2026-08-25",
            amount="25000.00",
            credit_debit_code="CRDT",
        )
        self.changed_date_payload = self._with_availability(
            fixture,
            marker,
            actual_date="2026-08-26",
            amount="25000.00",
            credit_debit_code="CRDT",
        )
        self.changed_amount_payload = self._with_availability(
            fixture,
            marker,
            actual_date="2026-08-25",
            amount="25000.01",
            credit_debit_code="CRDT",
        )
        self.changed_direction_payload = self._with_availability(
            fixture,
            marker,
            actual_date="2026-08-25",
            amount="25000.00",
            credit_debit_code="DBIT",
        )

        formatting_anchor = (
            "      <Avlbty>\n"
            "        <Dt><ActlDt>2026-08-25</ActlDt></Dt>\n"
            "        <Amt Ccy=\"KRW\">25000.00</Amt>\n"
            "        <CdtDbtInd>CRDT</CdtDbtInd>\n"
            "      </Avlbty>\n"
        ).encode("utf-8")
        self.assertEqual(self.first_payload.count(formatting_anchor), 1)
        self.reformatted_payload = self.first_payload.replace(
            formatting_anchor,
            (
                "      <Avlbty>\n"
                "        <Dt>\n"
                "          <ActlDt>2026-08-25</ActlDt>\n"
                "        </Dt>\n"
                "        <Amt Ccy=\"KRW\">25000.00</Amt>\n"
                "        <CdtDbtInd>CRDT</CdtDbtInd>\n"
                "      </Avlbty>\n"
            ).encode("utf-8"),
            1,
        )

        self.first_statement = parse_bank_statement_payload(
            self.first_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_date_statement = parse_bank_statement_payload(
            self.changed_date_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_amount_statement = parse_bank_statement_payload(
            self.changed_amount_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_direction_statement = parse_bank_statement_payload(
            self.changed_direction_payload,
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

    def test_availability_semantics_are_material_entry_evidence(self) -> None:
        """Date, exact amount, and direction each change retained entry evidence."""
        variants = (
            (
                self.first_statement,
                self._expected_availability_hash(
                    actual_date="2026-08-25",
                    amount="25000.00",
                    credit_debit_code="CRDT",
                ),
            ),
            (
                self.changed_date_statement,
                self._expected_availability_hash(
                    actual_date="2026-08-26",
                    amount="25000.00",
                    credit_debit_code="CRDT",
                ),
            ),
            (
                self.changed_amount_statement,
                self._expected_availability_hash(
                    actual_date="2026-08-25",
                    amount="25000.01",
                    credit_debit_code="CRDT",
                ),
            ),
            (
                self.changed_direction_statement,
                self._expected_availability_hash(
                    actual_date="2026-08-25",
                    amount="25000.00",
                    credit_debit_code="DBIT",
                ),
            ),
        )

        hashes: list[str] = []
        for statement, expected_hash in variants:
            with self.subTest(expected_hash=expected_hash):
                entry = statement.entries[0]
                self.assertRegex(expected_hash, _HASH_PATTERN)
                self.assertEqual(
                    getattr(entry, "entry_availability_evidence_hash", None),
                    expected_hash,
                )
                self._assert_entry_hash_binding(entry, expected_hash)
                hashes.append(expected_hash)

        self.assertEqual(len(set(hashes)), len(hashes))
        for changed_statement in (
            self.changed_date_statement,
            self.changed_amount_statement,
            self.changed_direction_statement,
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

    def test_xml_formatting_does_not_change_availability_semantics(self) -> None:
        """Insignificant XML formatting changes only raw artifact identity."""
        expected_hash = self._expected_availability_hash(
            actual_date="2026-08-25",
            amount="25000.00",
            credit_debit_code="CRDT",
        )
        first_entry = self.first_statement.entries[0]
        reformatted_entry = self.reformatted_statement.entries[0]

        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(first_entry, "entry_availability_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(reformatted_entry, "entry_availability_evidence_hash", None),
            expected_hash,
        )
        self._assert_entry_hash_binding(first_entry, expected_hash)
        self._assert_entry_hash_binding(reformatted_entry, expected_hash)
        self.assertEqual(first_entry.source_entry_hash, reformatted_entry.source_entry_hash)
        self.assertEqual(
            self.first_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_availability_requires_explicit_statement_correction(self) -> None:
        """Changed bank availability cannot silently replay one immutable statement."""
        accepted = accept_bank_statement_evidence(
            self._command(self.first_payload, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.changed_date_payload, "changed-date"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_preserves_exact_availability_evidence(self) -> None:
        """Supported reads retain the admitted availability amount/date/direction exactly."""
        expected_hash = self._expected_availability_hash(
            actual_date="2026-08-25",
            amount="25000.00",
            credit_debit_code="CRDT",
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

        self.assertEqual(entry.get("entry_availability_evidence_hash"), expected_hash)
        self.assertEqual(
            entry.get("availability_evidence"),
            [
                {
                    "date_choice": "ActlDt",
                    "date_value": "2026-08-25",
                    "amount": "25000.00",
                    "currency_code": "KRW",
                    "credit_debit_code": "CRDT",
                }
            ],
        )

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Bind canonical entry identity to semantic availability evidence, not raw XML."""
        projection = dict(bank_statement._entry_payload(entry))
        if projection.get("entry_availability_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry the exact "
                "entry_availability_evidence_hash"
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
    def _expected_availability_hash(
        *,
        actual_date: str,
        amount: str,
        credit_debit_code: str,
    ) -> str:
        """Digest the complete simple CashAvailability1 semantics used by this RED."""
        preimage = json.dumps(
            {
                "evidence_type": _ENTRY_AVAILABILITY_EVIDENCE_PURPOSE,
                "availability_records": [
                    {
                        "date_choice": "ActlDt",
                        "date_value": actual_date,
                        "amount": amount,
                        "currency_code": "KRW",
                        "credit_debit_code": credit_debit_code,
                    }
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _with_availability(
        fixture: str,
        marker: str,
        *,
        actual_date: str,
        amount: str,
        credit_debit_code: str,
    ) -> bytes:
        """Insert one CashAvailability1 after the first entry account-servicer reference."""
        return fixture.replace(
            marker,
            "      <AcctSvcrRef>ASV-1</AcctSvcrRef>\n"
            "      <Avlbty>\n"
            f"        <Dt><ActlDt>{actual_date}</ActlDt></Dt>\n"
            f"        <Amt Ccy=\"KRW\">{amount}</Amt>\n"
            f"        <CdtDbtInd>{credit_debit_code}</CdtDbtInd>\n"
            "      </Avlbty>\n"
            "      <BkTxCd>\n",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"entry-availability-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
