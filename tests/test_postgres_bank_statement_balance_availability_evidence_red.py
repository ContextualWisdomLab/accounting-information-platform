"""PostgreSQL REDs for camt.053 balance-availability provenance."""

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
_BALANCE_AVAILABILITY_PURPOSE = "camt.053.001.14/Stmt/Bal/Avlbty"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementBalanceAvailabilityEvidenceRedTests(unittest.TestCase):
    """Retain reported balance availability without treating it as ledger authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare statements that differ only in one optional CLAV availability record."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.entry_marker = "      <Ntry>\n        <NtryRef>NTRY-1</NtryRef>"
        self.assertEqual(self.fixture.count(self.entry_marker), 1)

        self.base_payload = self._with_clav_availability(
            actual_date="2026-08-25",
            amount="114000.00",
            credit_debit_code="CRDT",
        )
        self.changed_date_payload = self._with_clav_availability(
            actual_date="2026-08-26",
            amount="114000.00",
            credit_debit_code="CRDT",
        )
        self.changed_amount_payload = self._with_clav_availability(
            actual_date="2026-08-25",
            amount="113999.99",
            credit_debit_code="CRDT",
        )
        self.changed_direction_payload = self._with_clav_availability(
            actual_date="2026-08-25",
            amount="114000.00",
            credit_debit_code="DBIT",
        )

        formatting_anchor = (
            "        <Avlbty>\n"
            "          <Dt><ActlDt>2026-08-25</ActlDt></Dt>\n"
            "          <Amt Ccy=\"KRW\">114000.00</Amt>\n"
            "          <CdtDbtInd>CRDT</CdtDbtInd>\n"
            "        </Avlbty>\n"
        ).encode("utf-8")
        self.assertEqual(self.base_payload.count(formatting_anchor), 1)
        self.reformatted_payload = self.base_payload.replace(
            formatting_anchor,
            (
                "        <Avlbty>\n"
                "          <Dt>\n"
                "            <ActlDt>2026-08-25</ActlDt>\n"
                "          </Dt>\n"
                "          <Amt Ccy=\"KRW\">114000.00</Amt>\n"
                "          <CdtDbtInd>CRDT</CdtDbtInd>\n"
                "        </Avlbty>\n"
            ).encode("utf-8"),
            1,
        )

        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_date_statement = parse_bank_statement_payload(
            self.changed_date_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_amount_statement = parse_bank_statement_payload(
            self.changed_amount_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_direction_statement = parse_bank_statement_payload(
            self.changed_direction_payload, CAMT053_MESSAGE_DEFINITION
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

    def test_balance_availability_semantics_are_material_statement_evidence(self) -> None:
        """Availability date, exact amount, and direction each change retained balance evidence."""
        variants = (
            (
                self.base_statement,
                self._expected_availability_hash("2026-08-25", "114000.00", "CRDT"),
            ),
            (
                self.changed_date_statement,
                self._expected_availability_hash("2026-08-26", "114000.00", "CRDT"),
            ),
            (
                self.changed_amount_statement,
                self._expected_availability_hash("2026-08-25", "113999.99", "CRDT"),
            ),
            (
                self.changed_direction_statement,
                self._expected_availability_hash("2026-08-25", "114000.00", "DBIT"),
            ),
        )

        hashes: list[str] = []
        for statement, expected_hash in variants:
            with self.subTest(expected_hash=expected_hash):
                balance = self._clav_balance(statement)
                self.assertRegex(expected_hash, _HASH_PATTERN)
                self.assertEqual(
                    getattr(balance, "balance_availability_evidence_hash", None),
                    expected_hash,
                )
                self._assert_balance_hash_binding(balance, expected_hash)
                hashes.append(expected_hash)

        self.assertEqual(len(set(hashes)), len(hashes))
        for changed in (
            self.changed_date_statement,
            self.changed_amount_statement,
            self.changed_direction_statement,
        ):
            self.assertEqual(
                self.base_statement.account_identifier_hash,
                changed.account_identifier_hash,
            )
            self.assertEqual(
                self.base_statement.entries[0].source_entry_hash,
                changed.entries[0].source_entry_hash,
            )
            self.assertNotEqual(
                self._clav_balance(self.base_statement).source_balance_hash,
                self._clav_balance(changed).source_balance_hash,
            )
            self.assertNotEqual(
                self.base_statement.normalized_payload_hash,
                changed.normalized_payload_hash,
            )

    def test_xml_layout_does_not_change_balance_availability_semantics(self) -> None:
        """Whitespace-only XML layout changes may alter raw bytes, not availability semantics."""
        expected_hash = self._expected_availability_hash(
            "2026-08-25", "114000.00", "CRDT"
        )
        base_balance = self._clav_balance(self.base_statement)
        reformatted_balance = self._clav_balance(self.reformatted_statement)

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(base_balance, "balance_availability_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(reformatted_balance, "balance_availability_evidence_hash", None),
            expected_hash,
        )
        self._assert_balance_hash_binding(base_balance, expected_hash)
        self._assert_balance_hash_binding(reformatted_balance, expected_hash)
        self.assertEqual(base_balance.source_balance_hash, reformatted_balance.source_balance_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_every_material_balance_availability_change_requires_correction(self) -> None:
        """Changed availability cannot silently replay one immutable statement identity."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        for suffix, payload in (
            ("changed-date", self.changed_date_payload),
            ("changed-amount", self.changed_amount_payload),
            ("changed-direction", self.changed_direction_payload),
        ):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                    accept_bank_statement_evidence(
                        self._command(payload, suffix),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_buyer_read_preserves_exact_balance_availability_evidence(self) -> None:
        """Statement readback retains CLAV availability without inferring accounting policy."""
        expected_hash = self._expected_availability_hash(
            "2026-08-25", "114000.00", "CRDT"
        )
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "lookup"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        document = lookup_bank_statement(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        balances = document.get("balances")
        self.assertIsInstance(balances, list)
        clav = next(
            balance
            for balance in balances
            if balance.get("balance_type_code") == "CLAV"
            and balance.get("balance_type_source_code") == "cd"
        )
        self.assertEqual(clav.get("balance_availability_evidence_hash"), expected_hash)
        self.assertEqual(
            clav.get("availability_evidence"),
            [
                {
                    "date_choice": "ActlDt",
                    "date_value": "2026-08-25",
                    "amount": "114000.00",
                    "currency_code": "KRW",
                    "credit_debit_code": "CRDT",
                }
            ],
        )

    @staticmethod
    def _clav_balance(statement: object) -> object:
        """Return the single standard CLAV balance from the focused statement."""
        balances = getattr(statement, "balances", ())
        matches = [
            balance
            for balance in balances
            if getattr(balance, "balance_type_code", None) == "CLAV"
            and getattr(balance, "balance_type_source_code", None) == "cd"
        ]
        if len(matches) != 1:
            raise AssertionError("focused statement must retain exactly one standard CLAV balance")
        return matches[0]

    @staticmethod
    def _assert_balance_hash_binding(balance: object, expected_hash: str) -> None:
        """Bind balance identity to semantic availability, never raw XML representation."""
        projection = dict(bank_statement._balance_payload(balance))
        if projection.get("balance_availability_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical balance projection must carry balance_availability_evidence_hash"
            )
        projection_without_hash = dict(projection)
        source_hash = projection_without_hash.pop("source_balance_hash", None)
        if source_hash != balance.source_balance_hash:
            raise AssertionError("balance projection source hash must match retained balance")
        preimage = json.dumps(
            projection_without_hash,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        expected_balance_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if balance.source_balance_hash != expected_balance_hash:
            raise AssertionError(
                "source_balance_hash must digest the canonical balance projection"
            )

    @staticmethod
    def _expected_availability_hash(
        actual_date: str, amount: str, credit_debit_code: str
    ) -> str:
        """Digest one focused CashAvailability1 record under its explicit purpose."""
        preimage = json.dumps(
            {
                "evidence_type": _BALANCE_AVAILABILITY_PURPOSE,
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

    def _with_clav_availability(
        self, *, actual_date: str, amount: str, credit_debit_code: str
    ) -> bytes:
        """Insert one schema-shaped CLAV balance with one availability record."""
        balance = (
            "      <Bal>\n"
            "        <Tp>\n"
            "          <CdOrPrtry>\n"
            "            <Cd>CLAV</Cd>\n"
            "          </CdOrPrtry>\n"
            "        </Tp>\n"
            "        <Amt Ccy=\"KRW\">114000.00</Amt>\n"
            "        <CdtDbtInd>CRDT</CdtDbtInd>\n"
            "        <Dt>\n"
            "          <Dt>2026-08-24</Dt>\n"
            "        </Dt>\n"
            "        <Avlbty>\n"
            f"          <Dt><ActlDt>{actual_date}</ActlDt></Dt>\n"
            f"          <Amt Ccy=\"KRW\">{amount}</Amt>\n"
            f"          <CdtDbtInd>{credit_debit_code}</CdtDbtInd>\n"
            "        </Avlbty>\n"
            "      </Bal>\n"
        )
        return self.fixture.replace(
            self.entry_marker,
            balance + self.entry_marker,
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"balance-availability-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
