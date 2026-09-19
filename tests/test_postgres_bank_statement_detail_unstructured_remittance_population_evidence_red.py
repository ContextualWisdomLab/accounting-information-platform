"""REDs for repeated camt.053 unstructured-remittance evidence semantics."""

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


class BankStatementDetailUnstructuredRemittancePopulationEvidenceRedTests(
    unittest.TestCase
):
    """Preserve every source-ordered RmtInf/Ustrd value as reconciliation evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare two schema-valid statements differing only in the second Ustrd value."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        self.marker = (
            "            <RmtInf>\n"
            "              <Ustrd>Invoice 1001</Ustrd>\n"
            "            </RmtInf>"
        )
        if fixture.count(self.marker) != 1:
            raise AssertionError("canonical RmtInf marker must occur exactly once")

        self.first_line = "Invoice 1001"
        self.base_second_line = "Purchase order PO-1001"
        self.changed_second_line = "Purchase order PO-1002"
        for value in (
            self.first_line,
            self.base_second_line,
            self.changed_second_line,
        ):
            if not 1 <= len(value) <= 140:
                raise AssertionError("each Ustrd fixture value must satisfy Max140Text")

        self.base_payload = self._with_unstructured_lines(
            fixture,
            (self.first_line, self.base_second_line),
        )
        self.changed_payload = self._with_unstructured_lines(
            fixture,
            (self.first_line, self.changed_second_line),
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
            "urn:cwl:bank_account:unstructured-remittance-population:"
            f"{uuid.uuid4().hex}"
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

    def test_second_unstructured_line_is_material_to_normalized_evidence_identity(self) -> None:
        """A later Ustrd value cannot alias when the first Ustrd and accounting facts match."""
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

        base_text = base_detail.remittance_evidence_text
        changed_text = changed_detail.remittance_evidence_text
        if not isinstance(base_text, str) or not isinstance(changed_text, str):
            raise AssertionError("admitted Ustrd evidence must remain buyer-readable text")
        self.assertIn(self.first_line, base_text)
        self.assertIn(self.base_second_line, base_text)
        self.assertIn(self.first_line, changed_text)
        self.assertIn(self.changed_second_line, changed_text)
        self.assertLess(base_text.index(self.first_line), base_text.index(self.base_second_line))
        self.assertLess(
            changed_text.index(self.first_line),
            changed_text.index(self.changed_second_line),
        )
        self.assertNotEqual(base_text, changed_text)

        self.assertNotEqual(base_detail.source_detail_hash, changed_detail.source_detail_hash)
        self.assertNotEqual(base_entry.source_entry_hash, changed_entry.source_entry_hash)
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
        self._assert_exact_amount(base_entry, base_detail)
        self._assert_exact_amount(changed_entry, changed_detail)

    def test_changed_later_unstructured_line_requires_explicit_statement_correction(self) -> None:
        """A changed later Ustrd line cannot be silently replayed under one statement identity."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.changed_payload, "changed-second-line"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_keeps_all_unstructured_lines_in_source_order(self) -> None:
        """Reconciliation reads retain later Ustrd evidence without changing amount truth."""
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
        text = detail.get("remittance_evidence_text")
        if not isinstance(text, str):
            raise AssertionError("buyer read must retain unstructured remittance evidence")
        self.assertIn(self.first_line, text)
        self.assertIn(self.base_second_line, text)
        self.assertLess(text.index(self.first_line), text.index(self.base_second_line))
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _with_unstructured_lines(self, fixture: str, lines: tuple[str, ...]) -> bytes:
        """Replace the canonical RmtInf with source-ordered, schema-valid Ustrd lines."""
        chunks = ["            <RmtInf>\n"]
        chunks.extend(f"              <Ustrd>{line}</Ustrd>\n" for line in lines)
        chunks.append("            </RmtInf>")
        replacement = "".join(chunks)
        return fixture.replace(self.marker, replacement, 1).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Build one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"detail-unstructured-remittance-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

    @staticmethod
    def _assert_sha256(value: object) -> None:
        """Require canonical purpose-chain digest syntax before equality comparisons."""
        if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
            raise AssertionError("evidence hash must be canonical SHA-256")

    @staticmethod
    def _assert_exact_amount(entry: object, detail: object) -> None:
        """Keep exact accounting amount truth independent from remittance evidence."""
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
