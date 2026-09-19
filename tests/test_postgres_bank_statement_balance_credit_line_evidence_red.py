"""PostgreSQL REDs for camt.053 balance credit-line provenance."""

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
_BALANCE_CREDIT_LINE_PURPOSE = "camt.053.001.14/Stmt/Bal/CdtLine"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementBalanceCreditLineEvidenceRedTests(unittest.TestCase):
    """Retain reported credit-line evidence without treating it as posting authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare balances that differ only in one CreditLine3 semantic field."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.entry_marker = "      <Ntry>\n        <NtryRef>NTRY-1</NtryRef>"
        self.assertEqual(self.fixture.count(self.entry_marker), 1)

        self.base_lines = (
            {
                "included": True,
                "type_value": "REVOLVING",
                "amount": "50000.00",
                "date_value": "2026-08-31",
            },
            {
                "included": False,
                "type_value": "TEMPORARY",
                "amount": "25000.00",
                "date_value": "2026-09-15",
            },
        )
        self.base_payload = self._with_clav_credit_lines(self.base_lines)
        self.changed_included_payload = self._with_clav_credit_lines(
            self._changed_first(included=False)
        )
        self.changed_type_payload = self._with_clav_credit_lines(
            self._changed_first(type_value="REVOLVING-ALT")
        )
        self.changed_amount_payload = self._with_clav_credit_lines(
            self._changed_first(amount="50000.01")
        )
        self.changed_date_payload = self._with_clav_credit_lines(
            self._changed_first(date_value="2026-09-01")
        )

        formatting_anchor = (
            "        <CdtLine>\n"
            "          <Incl>true</Incl>\n"
            "          <Tp><Prtry>REVOLVING</Prtry></Tp>\n"
            "          <Amt Ccy=\"KRW\">50000.00</Amt>\n"
            "          <Dt><Dt>2026-08-31</Dt></Dt>\n"
            "        </CdtLine>\n"
        ).encode("utf-8")
        self.assertEqual(self.base_payload.count(formatting_anchor), 1)
        self.reformatted_payload = self.base_payload.replace(
            formatting_anchor,
            (
                "        <CdtLine>\n"
                "          <Incl>true</Incl>\n"
                "          <Tp>\n"
                "            <Prtry>REVOLVING</Prtry>\n"
                "          </Tp>\n"
                "          <Amt Ccy=\"KRW\">50000.00</Amt>\n"
                "          <Dt>\n"
                "            <Dt>2026-08-31</Dt>\n"
                "          </Dt>\n"
                "        </CdtLine>\n"
            ).encode("utf-8"),
            1,
        )

        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_included_statement = parse_bank_statement_payload(
            self.changed_included_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_type_statement = parse_bank_statement_payload(
            self.changed_type_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_amount_statement = parse_bank_statement_payload(
            self.changed_amount_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_date_statement = parse_bank_statement_payload(
            self.changed_date_payload, CAMT053_MESSAGE_DEFINITION
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

    def test_credit_line_semantics_are_material_balance_evidence(self) -> None:
        """Included, type, amount, and date each change retained balance evidence."""
        variants = (
            (self.base_statement, self.base_lines),
            (self.changed_included_statement, self._changed_first(included=False)),
            (self.changed_type_statement, self._changed_first(type_value="REVOLVING-ALT")),
            (self.changed_amount_statement, self._changed_first(amount="50000.01")),
            (self.changed_date_statement, self._changed_first(date_value="2026-09-01")),
        )

        hashes: list[str] = []
        for statement, lines in variants:
            expected_hash = self._expected_credit_line_hash(lines)
            with self.subTest(expected_hash=expected_hash):
                balance = self._clav_balance(statement)
                self.assertRegex(expected_hash, _HASH_PATTERN)
                self.assertEqual(
                    getattr(balance, "balance_credit_line_evidence_hash", None),
                    expected_hash,
                )
                self._assert_balance_hash_binding(balance, expected_hash)
                hashes.append(expected_hash)

        self.assertEqual(len(set(hashes)), len(hashes))
        for changed in (
            self.changed_included_statement,
            self.changed_type_statement,
            self.changed_amount_statement,
            self.changed_date_statement,
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

    def test_xml_layout_does_not_change_credit_line_semantics(self) -> None:
        """Whitespace-only layout changes may alter raw bytes, not credit-line semantics."""
        expected_hash = self._expected_credit_line_hash(self.base_lines)
        base_balance = self._clav_balance(self.base_statement)
        reformatted_balance = self._clav_balance(self.reformatted_statement)

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(base_balance, "balance_credit_line_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(reformatted_balance, "balance_credit_line_evidence_hash", None),
            expected_hash,
        )
        self._assert_balance_hash_binding(base_balance, expected_hash)
        self._assert_balance_hash_binding(reformatted_balance, expected_hash)
        self.assertEqual(base_balance.source_balance_hash, reformatted_balance.source_balance_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_every_material_credit_line_change_requires_correction(self) -> None:
        """Changed credit-line provenance cannot silently replay one statement identity."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        for suffix, payload in (
            ("changed-included", self.changed_included_payload),
            ("changed-type", self.changed_type_payload),
            ("changed-amount", self.changed_amount_payload),
            ("changed-date", self.changed_date_payload),
        ):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                    accept_bank_statement_evidence(
                        self._command(payload, suffix),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_buyer_read_preserves_repeatable_credit_line_evidence(self) -> None:
        """Statement readback retains each CreditLine3 record in source order."""
        expected_hash = self._expected_credit_line_hash(self.base_lines)
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
        self.assertEqual(clav.get("balance_credit_line_evidence_hash"), expected_hash)
        self.assertEqual(
            clav.get("credit_line_evidence"),
            [
                self._expected_credit_line_projection(line)
                for line in self.base_lines
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
        """Bind balance identity to semantic credit-line evidence, never raw XML layout."""
        projection = dict(bank_statement._balance_payload(balance))
        if projection.get("balance_credit_line_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical balance projection must carry balance_credit_line_evidence_hash"
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

    @classmethod
    def _expected_credit_line_hash(cls, lines: tuple[dict[str, object], ...]) -> str:
        """Digest ordered CreditLine3 records under an explicit evidence purpose."""
        preimage = json.dumps(
            {
                "evidence_type": _BALANCE_CREDIT_LINE_PURPOSE,
                "credit_lines": [
                    cls._expected_credit_line_projection(line)
                    for line in lines
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _expected_credit_line_projection(line: dict[str, object]) -> dict[str, object]:
        """Return the focused canonical CreditLine3 projection."""
        return {
            "included": bool(line["included"]),
            "type_choice": "Prtry",
            "type_value": str(line["type_value"]),
            "amount": str(line["amount"]),
            "currency_code": "KRW",
            "date_choice": "Dt",
            "date_value": str(line["date_value"]),
        }

    def _changed_first(self, **updates: object) -> tuple[dict[str, object], ...]:
        """Return the two focused credit lines with only the first record changed."""
        first = dict(self.base_lines[0])
        first.update(updates)
        return (first, dict(self.base_lines[1]))

    def _with_clav_credit_lines(self, lines: tuple[dict[str, object], ...]) -> bytes:
        """Insert one CLAV balance carrying repeatable schema-shaped CreditLine3 records."""
        credit_lines = "".join(
            (
                "        <CdtLine>\n"
                f"          <Incl>{str(bool(line['included'])).lower()}</Incl>\n"
                f"          <Tp><Prtry>{line['type_value']}</Prtry></Tp>\n"
                f"          <Amt Ccy=\"KRW\">{line['amount']}</Amt>\n"
                f"          <Dt><Dt>{line['date_value']}</Dt></Dt>\n"
                "        </CdtLine>\n"
            )
            for line in lines
        )
        balance = (
            "      <Bal>\n"
            "        <Tp>\n"
            "          <CdOrPrtry>\n"
            "            <Cd>CLAV</Cd>\n"
            "          </CdOrPrtry>\n"
            "        </Tp>\n"
            f"{credit_lines}"
            "        <Amt Ccy=\"KRW\">114000.00</Amt>\n"
            "        <CdtDbtInd>CRDT</CdtDbtInd>\n"
            "        <Dt>\n"
            "          <Dt>2026-08-24</Dt>\n"
            "        </Dt>\n"
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
            "ingestion_idempotency_key": f"balance-credit-line-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
