"""PostgreSQL REDs for CreditLine3 date-choice provenance."""

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


class BankStatementBalanceCreditLineDateChoiceRedTests(unittest.TestCase):
    """Preserve CreditLine3 Dt/DtTm semantics as bank-reported evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create equivalent-date credit lines that differ only by ISO choice semantics."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.entry_marker = "      <Ntry>\n        <NtryRef>NTRY-1</NtryRef>"
        self.assertEqual(self.fixture.count(self.entry_marker), 1)

        self.date_projection = self._projection(
            date_choice="Dt",
            date_value="2026-08-31",
        )
        self.datetime_projection = self._projection(
            date_choice="DtTm",
            date_value="2026-08-31T00:00:00Z",
        )

        self.date_payload = self._with_credit_line(
            "<Dt><Dt>2026-08-31</Dt></Dt>"
        )
        self.datetime_payload = self._with_credit_line(
            "<Dt><DtTm>2026-08-31T00:00:00Z</DtTm></Dt>"
        )
        self.datetime_offset_payload = self._with_credit_line(
            "<Dt><DtTm>2026-08-31T00:00:00+00:00</DtTm></Dt>"
        )

        self.date_statement = parse_bank_statement_payload(
            self.date_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.datetime_statement = parse_bank_statement_payload(
            self.datetime_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.datetime_offset_statement = parse_bank_statement_payload(
            self.datetime_offset_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.date_statement.account_currency_code,
                "account_identifier_hash": self.date_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_credit_line_date_choice_is_material_evidence(self) -> None:
        """Calendar date and explicit midnight instant remain distinct evidence."""
        date_hash = self._expected_hash(self.date_projection)
        datetime_hash = self._expected_hash(self.datetime_projection)
        self.assertRegex(date_hash, _HASH_PATTERN)
        self.assertRegex(datetime_hash, _HASH_PATTERN)
        self.assertNotEqual(date_hash, datetime_hash)

        date_balance = self._clav_balance(self.date_statement)
        datetime_balance = self._clav_balance(self.datetime_statement)

        self.assertEqual(
            getattr(date_balance, "balance_credit_line_evidence_hash", None),
            date_hash,
        )
        self.assertEqual(
            getattr(datetime_balance, "balance_credit_line_evidence_hash", None),
            datetime_hash,
        )
        self._assert_hash_binding(date_balance, date_hash)
        self._assert_hash_binding(datetime_balance, datetime_hash)
        self.assertNotEqual(date_balance.source_balance_hash, datetime_balance.source_balance_hash)
        self.assertNotEqual(
            self.date_statement.normalized_payload_hash,
            self.datetime_statement.normalized_payload_hash,
        )
        self.assertEqual(
            self.date_statement.account_identifier_hash,
            self.datetime_statement.account_identifier_hash,
        )
        self.assertEqual(
            self.date_statement.entries[0].source_entry_hash,
            self.datetime_statement.entries[0].source_entry_hash,
        )

    def test_equivalent_utc_datetime_lexemes_have_one_semantic_identity(self) -> None:
        """Z and +00:00 describe one instant and must not fork canonical evidence."""
        expected_hash = self._expected_hash(self.datetime_projection)
        z_balance = self._clav_balance(self.datetime_statement)
        offset_balance = self._clav_balance(self.datetime_offset_statement)

        self.assertNotEqual(
            self.datetime_statement.source_artifact_hash,
            self.datetime_offset_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(z_balance, "balance_credit_line_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(offset_balance, "balance_credit_line_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(z_balance.source_balance_hash, offset_balance.source_balance_hash)
        self.assertEqual(
            self.datetime_statement.normalized_payload_hash,
            self.datetime_offset_statement.normalized_payload_hash,
        )

    def test_date_choice_change_requires_explicit_correction(self) -> None:
        """One statement identity cannot silently replay Dt as DtTm."""
        accepted = accept_bank_statement_evidence(
            self._command(self.date_payload, "date"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.datetime_payload, "datetime"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_preserves_datetime_choice_and_canonical_instant(self) -> None:
        """Readback exposes the reported choice and a canonical UTC instant."""
        expected_hash = self._expected_hash(self.datetime_projection)
        accepted = accept_bank_statement_evidence(
            self._command(self.datetime_offset_payload, "lookup"),
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
            [self.datetime_projection],
        )

    @staticmethod
    def _projection(*, date_choice: str, date_value: str) -> dict[str, object]:
        """Return the canonical focused CreditLine3 projection."""
        return {
            "included": True,
            "type_choice": "Prtry",
            "type_value": "REVOLVING",
            "amount": "50000.00",
            "currency_code": "KRW",
            "date_choice": date_choice,
            "date_value": date_value,
        }

    @classmethod
    def _expected_hash(cls, projection: dict[str, object]) -> str:
        """Digest one ordered credit-line record under the credit-line evidence purpose."""
        preimage = json.dumps(
            {
                "evidence_type": _BALANCE_CREDIT_LINE_PURPOSE,
                "credit_lines": [projection],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

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
    def _assert_hash_binding(balance: object, expected_hash: str) -> None:
        """Bind balance identity to semantic CreditLine3 evidence."""
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

    def _with_credit_line(self, date_xml: str) -> bytes:
        """Insert one schema-shaped CLAV balance with one proprietary credit line."""
        balance = (
            "      <Bal>\n"
            "        <Tp>\n"
            "          <CdOrPrtry>\n"
            "            <Cd>CLAV</Cd>\n"
            "          </CdOrPrtry>\n"
            "        </Tp>\n"
            "        <CdtLine>\n"
            "          <Incl>true</Incl>\n"
            "          <Tp><Prtry>REVOLVING</Prtry></Tp>\n"
            "          <Amt Ccy=\"KRW\">50000.00</Amt>\n"
            f"          {date_xml}\n"
            "        </CdtLine>\n"
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
            "ingestion_idempotency_key": (
                f"balance-credit-line-date-choice-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
