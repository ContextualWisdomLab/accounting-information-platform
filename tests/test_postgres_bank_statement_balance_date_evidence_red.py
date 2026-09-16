"""PostgreSQL REDs for camt.053 balance-date evidence identity."""

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
_BALANCE_DATE_PURPOSE = "camt.053.001.14/Stmt/Bal/Dt"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementBalanceDateEvidenceRedTests(unittest.TestCase):
    """Retain the reported date choice of each source balance without inventing precision."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create source-real statements differing only in one balance-date semantic."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.opening_marker = (
            "        <CdtDbtInd>CRDT</CdtDbtInd>\n"
            "        <Dt>\n"
            "          <Dt>2026-08-23</Dt>\n"
            "        </Dt>"
        )
        self.closing_marker = (
            "        <CdtDbtInd>CRDT</CdtDbtInd>\n"
            "        <Dt>\n"
            "          <Dt>2026-08-24</Dt>\n"
            "        </Dt>"
        )
        self.assertEqual(self.fixture.count(self.opening_marker), 1)
        self.assertEqual(self.fixture.count(self.closing_marker), 1)

        self.first_balance_date = "2026-08-22"
        self.second_balance_date = "2026-08-21"
        self.first_payload = self._replace_balance_date(
            self.opening_marker,
            self.first_balance_date,
        )
        self.second_payload = self._replace_balance_date(
            self.opening_marker,
            self.second_balance_date,
        )
        self.first_statement = parse_bank_statement_payload(
            self.first_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.second_statement = parse_bank_statement_payload(
            self.second_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        self.opening_date_payload = self.fixture.encode("utf-8")
        self.opening_datetime_payload = self._replace_balance_date_choice(
            self.opening_marker,
            "<DtTm>2026-08-23T00:00:00Z</DtTm>",
        )
        self.opening_datetime_offset_payload = self._replace_balance_date_choice(
            self.opening_marker,
            "<DtTm>2026-08-23T00:00:00+00:00</DtTm>",
        )
        self.opening_date_statement = parse_bank_statement_payload(
            self.opening_date_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.opening_datetime_statement = parse_bank_statement_payload(
            self.opening_datetime_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.opening_datetime_offset_statement = parse_bank_statement_payload(
            self.opening_datetime_offset_payload,
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

    def test_opening_balance_date_changes_balance_and_statement_digest(self) -> None:
        """Changing only OPBD Bal/Dt changes that balance and statement evidence identity."""
        self.assertNotEqual(self.first_payload, self.second_payload)
        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.second_statement.source_artifact_hash,
        )
        self.assertNotEqual(
            self.first_statement.opening_balance_hash,
            self.second_statement.opening_balance_hash,
        )
        self.assertEqual(
            self.first_statement.closing_balance_hash,
            self.second_statement.closing_balance_hash,
        )
        self.assertNotEqual(
            self.first_statement.normalized_payload_hash,
            self.second_statement.normalized_payload_hash,
        )

    def test_closing_balance_date_changes_balance_and_statement_digest(self) -> None:
        """Changing only CLBD Bal/Dt changes that balance and statement evidence identity."""
        first_payload = self._replace_balance_date(self.closing_marker, "2026-08-25")
        second_payload = self._replace_balance_date(self.closing_marker, "2026-08-26")
        first_statement = parse_bank_statement_payload(
            first_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        second_statement = parse_bank_statement_payload(
            second_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        self.assertNotEqual(
            first_statement.source_artifact_hash,
            second_statement.source_artifact_hash,
        )
        self.assertEqual(
            first_statement.opening_balance_hash,
            second_statement.opening_balance_hash,
        )
        self.assertNotEqual(
            first_statement.closing_balance_hash,
            second_statement.closing_balance_hash,
        )
        self.assertNotEqual(
            first_statement.normalized_payload_hash,
            second_statement.normalized_payload_hash,
        )

    def test_balance_date_choice_is_material_evidence(self) -> None:
        """A calendar date and an explicit midnight instant remain distinct source evidence."""
        date_hash = self._expected_balance_date_hash("Dt", "2026-08-23")
        datetime_hash = self._expected_balance_date_hash(
            "DtTm",
            "2026-08-23T00:00:00Z",
        )
        self.assertRegex(date_hash, _HASH_PATTERN)
        self.assertRegex(datetime_hash, _HASH_PATTERN)
        self.assertNotEqual(date_hash, datetime_hash)

        date_balance = self._opening_balance(self.opening_date_statement)
        datetime_balance = self._opening_balance(self.opening_datetime_statement)

        self.assertEqual(
            getattr(date_balance, "balance_date_evidence_hash", None),
            date_hash,
        )
        self.assertEqual(
            getattr(datetime_balance, "balance_date_evidence_hash", None),
            datetime_hash,
        )
        self.assertEqual(getattr(date_balance, "balance_date_choice", None), "Dt")
        self.assertEqual(
            getattr(date_balance, "balance_date_value", None),
            "2026-08-23",
        )
        self.assertEqual(
            getattr(datetime_balance, "balance_date_choice", None),
            "DtTm",
        )
        self.assertEqual(
            getattr(datetime_balance, "balance_date_value", None),
            "2026-08-23T00:00:00Z",
        )
        self._assert_balance_hash_binding(date_balance, date_hash)
        self._assert_balance_hash_binding(datetime_balance, datetime_hash)
        self.assertNotEqual(
            date_balance.source_balance_hash,
            datetime_balance.source_balance_hash,
        )
        self.assertNotEqual(
            self.opening_date_statement.normalized_payload_hash,
            self.opening_datetime_statement.normalized_payload_hash,
        )
        self.assertEqual(
            self.opening_date_statement.account_identifier_hash,
            self.opening_datetime_statement.account_identifier_hash,
        )
        self.assertEqual(
            self.opening_date_statement.entries[0].source_entry_hash,
            self.opening_datetime_statement.entries[0].source_entry_hash,
        )

    def test_equivalent_balance_datetime_offsets_have_one_semantic_identity(self) -> None:
        """Z and +00:00 are one instant and must not fork canonical balance evidence."""
        expected_hash = self._expected_balance_date_hash(
            "DtTm",
            "2026-08-23T00:00:00Z",
        )
        z_balance = self._opening_balance(self.opening_datetime_statement)
        offset_balance = self._opening_balance(self.opening_datetime_offset_statement)

        self.assertNotEqual(
            self.opening_datetime_statement.source_artifact_hash,
            self.opening_datetime_offset_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(z_balance, "balance_date_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(offset_balance, "balance_date_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(offset_balance, "balance_date_value", None),
            "2026-08-23T00:00:00Z",
        )
        self.assertEqual(z_balance.source_balance_hash, offset_balance.source_balance_hash)
        self.assertEqual(
            self.opening_datetime_statement.normalized_payload_hash,
            self.opening_datetime_offset_statement.normalized_payload_hash,
        )

    def test_same_statement_identity_cannot_replay_changed_opening_balance_date(self) -> None:
        """A changed reported balance date requires correction, not silent replay."""
        accepted = accept_bank_statement_evidence(
            self._command(self.first_payload, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.second_payload, "second"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_balance_date_choice_change_requires_explicit_correction(self) -> None:
        """One statement identity cannot silently reinterpret Dt as DtTm."""
        accepted = accept_bank_statement_evidence(
            self._command(self.opening_date_payload, "choice-date"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.opening_datetime_payload, "choice-datetime"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_preserves_balance_date_choice_without_inferred_precision(self) -> None:
        """Statement readback retains the reported date choice and canonical value."""
        expected_hash = self._expected_balance_date_hash(
            "DtTm",
            "2026-08-23T00:00:00Z",
        )
        accepted = accept_bank_statement_evidence(
            self._command(self.opening_datetime_offset_payload, "lookup"),
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
        opening = next(
            balance
            for balance in balances
            if balance.get("balance_type_code") == "OPBD"
            and balance.get("balance_type_source_code") == "cd"
        )
        self.assertEqual(opening.get("balance_date_evidence_hash"), expected_hash)
        self.assertEqual(opening.get("balance_date_choice"), "DtTm")
        self.assertEqual(opening.get("balance_date_value"), "2026-08-23T00:00:00Z")

    def test_invalid_balance_date_is_rejected_before_evidence_admission(self) -> None:
        """An invalid Bal/Dt lexical value cannot become retained balance evidence."""
        invalid_payload = self._replace_balance_date(
            self.opening_marker,
            "2026-99-99",
        )

        with self.assertRaises(AccountingValidationError):
            parse_bank_statement_payload(
                invalid_payload,
                CAMT053_MESSAGE_DEFINITION,
            )
        with self.assertRaises(AccountingValidationError):
            accept_bank_statement_evidence(
                self._command(invalid_payload, "invalid"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    @staticmethod
    def _expected_balance_date_hash(date_choice: str, date_value: str) -> str:
        """Digest one reported balance date under the explicit evidence purpose."""
        preimage = json.dumps(
            {
                "evidence_type": _BALANCE_DATE_PURPOSE,
                "date_choice": date_choice,
                "date_value": date_value,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _opening_balance(statement: object) -> object:
        """Return the single standard OPBD balance from the focused statement."""
        balances = getattr(statement, "balances", ())
        matches = [
            balance
            for balance in balances
            if getattr(balance, "balance_type_code", None) == "OPBD"
            and getattr(balance, "balance_type_source_code", None) == "cd"
        ]
        if len(matches) != 1:
            raise AssertionError("focused statement must retain exactly one standard OPBD balance")
        return matches[0]

    @staticmethod
    def _assert_balance_hash_binding(balance: object, expected_hash: str) -> None:
        """Bind balance identity to the typed date semantic, not raw XML bytes."""
        projection = dict(bank_statement._balance_payload(balance))
        if projection.get("balance_date_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical balance projection must carry balance_date_evidence_hash"
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

    def _replace_balance_date(self, marker: str, reported_date: str) -> bytes:
        """Replace exactly one canonical balance-date marker."""
        prefix, _, suffix = marker.partition("2026-08-")
        original_day = "23" if marker == self.opening_marker else "24"
        self.assertEqual(suffix, f"{original_day}</Dt>\n        </Dt>")
        replacement = f"{prefix}{reported_date}</Dt>\n        </Dt>"
        return self.fixture.replace(marker, replacement, 1).encode("utf-8")

    def _replace_balance_date_choice(self, marker: str, choice_xml: str) -> bytes:
        """Replace one date-only balance choice while preserving its surrounding balance."""
        prefix = "        <CdtDbtInd>CRDT</CdtDbtInd>\n"
        self.assertTrue(marker.startswith(prefix))
        replacement = (
            prefix
            + "        <Dt>\n"
            + f"          {choice_xml}\n"
            + "        </Dt>"
        )
        return self.fixture.replace(marker, replacement, 1).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"balance-date-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
