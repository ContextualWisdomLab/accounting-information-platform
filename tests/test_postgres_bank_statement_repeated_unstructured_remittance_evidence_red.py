"""PostgreSQL REDs for repeated camt.053 unstructured remittance evidence."""

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


class BankStatementRepeatedUnstructuredRemittanceEvidenceRedTests(unittest.TestCase):
    """Retain every source-ordered Ustrd value instead of collapsing to the first value."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create source-real statements differing only in a second Ustrd value."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "<Ustrd>Invoice 1001</Ustrd>"
        if fixture.count(marker) != 1:
            raise AssertionError("canonical Ustrd marker must occur exactly once")

        self.first_line = "Invoice 1001"
        self.first_second_line = "Reference FIRST"
        self.second_second_line = "Reference SECOND"
        for value in (self.first_line, self.first_second_line, self.second_second_line):
            if not 1 <= len(value) <= 140:
                raise AssertionError("each Ustrd fixture value must satisfy Max140Text")

        self.first_payload = fixture.replace(
            marker,
            f"<Ustrd>{self.first_line}</Ustrd>\n"
            f"              <Ustrd>{self.first_second_line}</Ustrd>",
            1,
        ).encode("utf-8")
        self.second_payload = fixture.replace(
            marker,
            f"<Ustrd>{self.first_line}</Ustrd>\n"
            f"              <Ustrd>{self.second_second_line}</Ustrd>",
            1,
        ).encode("utf-8")
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

    def test_second_unstructured_remittance_changes_canonical_hashes(self) -> None:
        """Changing only the second Ustrd changes detail, entry, and statement identity."""
        first_entry = self.first_statement.entries[0]
        second_entry = self.second_statement.entries[0]
        first_detail = first_entry.entry_details[0]
        second_detail = second_entry.entry_details[0]

        for value in (
            first_detail.source_detail_hash,
            second_detail.source_detail_hash,
            first_entry.source_entry_hash,
            second_entry.source_entry_hash,
            self.first_statement.normalized_payload_hash,
            self.second_statement.normalized_payload_hash,
            self.first_statement.account_identifier_hash,
            self.second_statement.account_identifier_hash,
            self.first_statement.entries[1].source_entry_hash,
            self.second_statement.entries[1].source_entry_hash,
        ):
            self._assert_sha256(value)

        first_text = first_detail.remittance_evidence_text
        second_text = second_detail.remittance_evidence_text
        if not isinstance(first_text, str) or not isinstance(second_text, str):
            raise AssertionError("admitted Ustrd evidence must remain buyer-readable text")
        self.assertIn(self.first_line, first_text)
        self.assertIn(self.first_second_line, first_text)
        self.assertIn(self.first_line, second_text)
        self.assertIn(self.second_second_line, second_text)
        self.assertLess(first_text.index(self.first_line), first_text.index(self.first_second_line))
        self.assertLess(second_text.index(self.first_line), second_text.index(self.second_second_line))
        self.assertNotEqual(first_text, second_text)

        self.assertNotEqual(first_detail.source_detail_hash, second_detail.source_detail_hash)
        self.assertNotEqual(first_entry.source_entry_hash, second_entry.source_entry_hash)
        self.assertNotEqual(
            self.first_statement.normalized_payload_hash,
            self.second_statement.normalized_payload_hash,
        )
        self.assertEqual(
            self.first_statement.account_identifier_hash,
            self.second_statement.account_identifier_hash,
        )
        self.assertEqual(
            self.first_statement.entries[1].source_entry_hash,
            self.second_statement.entries[1].source_entry_hash,
        )
        self._assert_exact_amount(first_entry, first_detail)
        self._assert_exact_amount(second_entry, second_detail)

    def test_same_statement_identity_cannot_replay_changed_second_unstructured_remittance(
        self,
    ) -> None:
        """Changed repeated Ustrd evidence requires correction, not silent replay."""
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

    def test_buyer_read_retains_all_unstructured_remittance_in_source_order(self) -> None:
        """Tenant readback retains the later Ustrd without changing exact amount truth."""
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
        detail = entry["entry_details"][0]
        text = detail.get("remittance_evidence_text")
        if not isinstance(text, str):
            raise AssertionError("buyer read must retain unstructured remittance evidence")
        self.assertIn(self.first_line, text)
        self.assertIn(self.first_second_line, text)
        self.assertLess(text.index(self.first_line), text.index(self.first_second_line))
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"repeated-ustrd-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

    @staticmethod
    def _assert_sha256(value: object) -> None:
        """Require canonical digest syntax before comparing evidence identities."""
        if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
            raise AssertionError("evidence hash must be canonical SHA-256")

    @staticmethod
    def _assert_exact_amount(entry: object, detail: object) -> None:
        """Keep accounting amount truth independent from remittance evidence."""
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
