"""PostgreSQL REDs for camt.053 balance-date evidence identity."""

from __future__ import annotations

import unittest
import uuid

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting


class BankStatementBalanceDateEvidenceRedTests(unittest.TestCase):
    """Retain and validate the reported date of each source balance."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create source-real statements differing only in one balance date."""
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

    def test_same_statement_identity_cannot_replay_changed_opening_balance_date(self) -> None:
        """A changed reported balance date requires correction, not silent replay."""
        accept_bank_statement_evidence(
            self._command(self.first_payload, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )

        with self.assertRaises(AccountingValidationError):
            accept_bank_statement_evidence(
                self._command(self.second_payload, "second"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

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

    def _replace_balance_date(self, marker: str, reported_date: str) -> bytes:
        """Replace exactly one canonical balance-date marker."""
        prefix, _, suffix = marker.partition("2026-08-")
        original_day = "23" if marker == self.opening_marker else "24"
        self.assertEqual(suffix, f"{original_day}</Dt>\n        </Dt>")
        replacement = f"{prefix}{reported_date}</Dt>\n        </Dt>"
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
