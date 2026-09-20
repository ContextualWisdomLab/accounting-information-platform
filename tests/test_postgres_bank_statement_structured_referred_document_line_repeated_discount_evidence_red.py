"""REDs for repeated line-level discount evidence in structured remittance."""

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
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_details_evidence_red
    as line_details_contract,
)

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredRepeatedLineDiscountEvidenceRedTests(unittest.TestCase):
    """Retain every repeated LineDtls discount with its exact source line and order."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare two-line statements whose second line carries two discounts."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "<Ustrd>Invoice 1001</Ustrd>"
        if fixture.count(marker) != 1:
            raise AssertionError("canonical Ustrd marker must occur exactly once")

        self.document_number = "INV-2026-1001"
        self.line_related_date = "2026-09-01"
        self.first_line_number = "Stockitem1"
        self.second_line_number = "Stockitem2"
        self.first_line_description = "Annual support service"
        self.second_line_description = "Quarterly data service"
        self.repeated_discount_type = "STDS"
        self.base_repeated_discount_amount = Decimal("50.00")
        self.changed_repeated_discount_amount = Decimal("51.00")

        self.line_contract = (
            line_details_contract.BankStatementStructuredLineDetailsEvidenceRedTests(
                "test_line_projection_is_material_to_each_canonical_evidence_hash"
            )
        )
        self.line_contract.document_number = self.document_number
        self.line_contract.first_line_number = self.first_line_number
        self.line_contract.first_line_description = self.first_line_description
        self.line_contract.line_related_date = self.line_related_date

        single_discount_payload = self.line_contract._with_line_details(
            fixture,
            marker,
            self.second_line_number,
            self.second_line_description,
        )
        self.base_payload = self._append_second_line_discount(
            single_discount_payload,
            self.base_repeated_discount_amount,
        )
        self.changed_payload = self._replace_repeated_second_line_discount_amount(
            self.base_payload,
            self.base_repeated_discount_amount,
            self.changed_repeated_discount_amount,
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
            f"urn:cwl:bank_account:structured-line-repeated-discount:{uuid.uuid4().hex}"
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

    def test_later_repeated_line_discount_is_material_to_each_canonical_evidence_hash(self) -> None:
        """Bind the later repeated discount to detail, entry, and statement identity."""
        self.assertEqual(
            self._replace_repeated_second_line_discount_amount(
                self.changed_payload,
                self.changed_repeated_discount_amount,
                self.base_repeated_discount_amount,
            ),
            self.base_payload,
        )

        base_entry = self.base_statement.entries[0]
        changed_entry = self.changed_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]
        base_projection = self._structured_projection(self.base_repeated_discount_amount)
        changed_projection = self._structured_projection(
            self.changed_repeated_discount_amount
        )

        self.assertNotEqual(base_projection, changed_projection)
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
            self.line_contract._assert_sha256(value)

        self.assertEqual(
            base_detail.source_detail_hash,
            self.line_contract._expected_detail_hash(base_detail, base_projection),
        )
        self.assertEqual(
            changed_detail.source_detail_hash,
            self.line_contract._expected_detail_hash(changed_detail, changed_projection),
        )
        self.assertEqual(
            base_entry.source_entry_hash,
            self.line_contract._expected_entry_hash(base_entry, {1: base_projection}),
        )
        self.assertEqual(
            changed_entry.source_entry_hash,
            self.line_contract._expected_entry_hash(changed_entry, {1: changed_projection}),
        )
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.line_contract._expected_statement_hash(
                self.base_statement,
                {(1, 1): base_projection},
            ),
        )
        self.assertEqual(
            self.changed_statement.normalized_payload_hash,
            self.line_contract._expected_statement_hash(
                self.changed_statement,
                {(1, 1): changed_projection},
            ),
        )

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
        self.assertEqual(
            self.base_statement.entries[1].source_entry_hash,
            self.line_contract._expected_entry_hash(self.base_statement.entries[1], {}),
        )
        self.line_contract._assert_exact_transaction_amount(base_entry, base_detail)
        self.line_contract._assert_exact_transaction_amount(changed_entry, changed_detail)

    def test_changed_later_repeated_discount_requires_explicit_statement_correction(self) -> None:
        """A later repeated-discount change cannot silently replay one statement identity."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "repeated-line-discount-base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.changed_payload, "repeated-line-discount-changed"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_keeps_both_second_line_discounts_in_source_order(self) -> None:
        """Buyer reads retain both repeated discounts inside the exact source line."""
        accepted = accept_bank_statement_evidence(
            self._command(self.changed_payload, "repeated-line-discount-lookup"),
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

        self.assertEqual(
            detail.get(_STRUCTURED_EVIDENCE_KEY),
            self._structured_projection(self.changed_repeated_discount_amount),
        )
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _structured_projection(
        self,
        repeated_discount_amount: Decimal,
    ) -> list[dict[str, object]]:
        """Return the ordered projection with two discounts on the second line."""
        projection = self.line_contract._structured_projection(
            self.second_line_number,
            self.second_line_description,
        )
        line_details = projection[0]["line_details"]
        if not isinstance(line_details, list) or len(line_details) != 2:
            raise AssertionError("canonical line projection must contain exactly two lines")
        second_discounts = line_details[1]["discount_applied_amounts"]
        if not isinstance(second_discounts, list) or len(second_discounts) != 1:
            raise AssertionError("canonical second line must begin with one discount")
        first_discount = second_discounts[0]
        if first_discount != {
            "type_code": "APDS",
            "amount": "100",
            "currency_code": "KRW",
        }:
            raise AssertionError("canonical first discount must remain APDS / 100 KRW")
        second_discounts.append(
            {
                "type_code": self.repeated_discount_type,
                "amount": self._canonical_decimal(repeated_discount_amount),
                "currency_code": "KRW",
            }
        )
        return projection

    def _append_second_line_discount(
        self,
        payload: bytes,
        amount: Decimal,
    ) -> bytes:
        """Append one STDS discount after the existing discount on the second line."""
        text = payload.decode("utf-8")
        line_start, line_end, line_segment = self._second_line_segment(text)
        matches = list(
            re.finditer(r"<DscntApldAmt>.*?</DscntApldAmt>", line_segment, re.DOTALL)
        )
        if len(matches) != 1:
            raise AssertionError("canonical second line must contain exactly one discount")
        if "<Cd>APDS</Cd>" not in matches[0].group(0):
            raise AssertionError("canonical second-line discount must be APDS")

        insertion = (
            "<DscntApldAmt>"
            "<Tp><Cd>STDS</Cd></Tp>"
            f'<Amt Ccy="KRW">{format(amount, "f")}</Amt>'
            "</DscntApldAmt>"
        )
        insert_at = matches[0].end()
        changed_line = line_segment[:insert_at] + insertion + line_segment[insert_at:]
        return (text[:line_start] + changed_line + text[line_end:]).encode("utf-8")

    def _replace_repeated_second_line_discount_amount(
        self,
        payload: bytes,
        current_amount: Decimal,
        replacement_amount: Decimal,
    ) -> bytes:
        """Replace only the later discount amount in the second source line."""
        text = payload.decode("utf-8")
        line_start, line_end, line_segment = self._second_line_segment(text)
        discounts = list(
            re.finditer(r"<DscntApldAmt>.*?</DscntApldAmt>", line_segment, re.DOTALL)
        )
        if len(discounts) != 2:
            raise AssertionError("second line must contain exactly two discounts")
        first_discount = discounts[0].group(0)
        second_discount = discounts[1].group(0)
        if "<Cd>APDS</Cd>" not in first_discount:
            raise AssertionError("first second-line discount must remain APDS")
        if f"<Cd>{self.repeated_discount_type}</Cd>" not in second_discount:
            raise AssertionError("later second-line discount must remain STDS")

        amount_pattern = re.compile(r'(<Amt Ccy="KRW">)([^<]+)(</Amt>)')
        amount_matches = list(amount_pattern.finditer(second_discount))
        if len(amount_matches) != 1:
            raise AssertionError("later second-line discount must contain exactly one KRW amount")
        if Decimal(amount_matches[0].group(2)) != current_amount:
            raise AssertionError("later discount amount did not match expected source value")

        changed_discount = amount_pattern.sub(
            rf"\g<1>{format(replacement_amount, 'f')}\g<3>",
            second_discount,
            count=1,
        )
        changed_line = (
            line_segment[: discounts[1].start()]
            + changed_discount
            + line_segment[discounts[1].end() :]
        )
        return (text[:line_start] + changed_line + text[line_end:]).encode("utf-8")

    def _second_line_segment(self, text: str) -> tuple[int, int, str]:
        """Return the unique LineDtls segment identified by Stockitem2."""
        line_marker = f"<Nb>{self.second_line_number}</Nb>"
        if text.count(line_marker) != 1:
            raise AssertionError("second line number marker must occur exactly once")
        marker_index = text.index(line_marker)
        line_start = text.rfind("<LineDtls>", 0, marker_index)
        line_end = text.index("</LineDtls>", marker_index) + len("</LineDtls>")
        if line_start < 0:
            raise AssertionError("second line opening tag must precede its number")
        return line_start, line_end, text[line_start:line_end]

    @staticmethod
    def _canonical_decimal(value: Decimal) -> str:
        """Render exact decimal evidence without insignificant trailing zeroes."""
        rendered = format(value, "f")
        if "." in rendered:
            rendered = rendered.rstrip("0").rstrip(".")
        return rendered or "0"

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"structured-line-repeated-discount-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
