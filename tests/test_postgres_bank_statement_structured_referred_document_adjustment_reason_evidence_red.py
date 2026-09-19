"""REDs for structured referred-document adjustment reason preservation."""

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


class BankStatementStructuredAdjustmentReasonEvidenceRedTests(unittest.TestCase):
    """Retain adjustment reason metadata as Bank Reconciliation evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare statements that vary only one later adjustment reason field."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "<Ustrd>Invoice 1001</Ustrd>"
        if fixture.count(marker) != 1:
            raise AssertionError("canonical Ustrd marker must occur exactly once")

        self.first_amount = "100.00"
        self.second_amount = "200.00"
        self.first_direction = "DBIT"
        self.second_direction = "CRDT"
        self.first_reason = "DISC"
        self.base_second_reason = "ADJT"
        self.changed_second_reason = "FEES"
        self.first_additional_information = "Early payment discount"
        self.base_second_additional_information = "Invoice correction adjustment"
        self.changed_second_additional_information = "Bank fee correction adjustment"

        self.base_payload = self._with_adjustment_reason_metadata(
            fixture,
            marker,
            self.base_second_reason,
            self.base_second_additional_information,
        )
        self.changed_reason_payload = self._with_adjustment_reason_metadata(
            fixture,
            marker,
            self.changed_second_reason,
            self.base_second_additional_information,
        )
        self.changed_additional_information_payload = self._with_adjustment_reason_metadata(
            fixture,
            marker,
            self.base_second_reason,
            self.changed_second_additional_information,
        )
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_reason_statement = parse_bank_statement_payload(
            self.changed_reason_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_additional_information_statement = parse_bank_statement_payload(
            self.changed_additional_information_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        self.bank_account_reference = (
            f"urn:cwl:bank_account:structured-adjustment-reason:{uuid.uuid4().hex}"
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

    def test_later_reason_fields_are_material_to_evidence_identity(self) -> None:
        """Changing Rsn or AddtlInf must change only the owning evidence chain."""
        for changed in (
            self.changed_reason_statement,
            self.changed_additional_information_statement,
        ):
            with self.subTest(changed_hash=changed.normalized_payload_hash):
                base_entry = self.base_statement.entries[0]
                changed_entry = changed.entries[0]
                base_detail = base_entry.entry_details[0]
                changed_detail = changed_entry.entry_details[0]

                for value in (
                    base_detail.source_detail_hash,
                    changed_detail.source_detail_hash,
                    base_entry.source_entry_hash,
                    changed_entry.source_entry_hash,
                    self.base_statement.normalized_payload_hash,
                    changed.normalized_payload_hash,
                    self.base_statement.account_identifier_hash,
                    changed.account_identifier_hash,
                    self.base_statement.entries[1].source_entry_hash,
                    changed.entries[1].source_entry_hash,
                ):
                    self._assert_sha256(value)

                self.assertNotEqual(
                    base_detail.source_detail_hash,
                    changed_detail.source_detail_hash,
                )
                self.assertNotEqual(
                    base_entry.source_entry_hash,
                    changed_entry.source_entry_hash,
                )
                self.assertNotEqual(
                    self.base_statement.normalized_payload_hash,
                    changed.normalized_payload_hash,
                )
                self.assertEqual(
                    self.base_statement.account_identifier_hash,
                    changed.account_identifier_hash,
                )
                self.assertEqual(
                    self.base_statement.entries[1].source_entry_hash,
                    changed.entries[1].source_entry_hash,
                )
                self._assert_exact_transaction_amount(base_entry, base_detail)
                self._assert_exact_transaction_amount(changed_entry, changed_detail)

    def test_changed_later_reason_fields_require_explicit_statement_correction(self) -> None:
        """A changed reason or explanation cannot silently replay one statement."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "reason-base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        for suffix, payload in (
            ("reason-changed", self.changed_reason_payload),
            ("additional-information-changed", self.changed_additional_information_payload),
        ):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                    accept_bank_statement_evidence(
                        self._command(payload, suffix),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_buyer_read_keeps_adjustment_reason_associations_in_source_order(self) -> None:
        """Buyer reads retain amount, direction, reason, and explanation associations."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "reason-lookup"),
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
            raise AssertionError("buyer read must retain adjustment reason evidence")

        paired_order = re.compile(
            rf"{re.escape(self.first_amount)}.*?"
            rf"{re.escape(self.first_direction)}.*?"
            rf"{re.escape(self.first_reason)}.*?"
            rf"{re.escape(self.first_additional_information)}.*?"
            rf"{re.escape(self.second_amount)}.*?"
            rf"{re.escape(self.second_direction)}.*?"
            rf"{re.escape(self.base_second_reason)}.*?"
            rf"{re.escape(self.base_second_additional_information)}",
            re.DOTALL,
        )
        self.assertRegex(text, paired_order)
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _with_adjustment_reason_metadata(
        self,
        fixture: str,
        marker: str,
        second_reason: str,
        second_additional_information: str,
    ) -> bytes:
        """Insert two source-ordered adjustments whose later reason fields can vary."""
        structured = (
            f"{marker}\n"
            "              <Strd>\n"
            "                <RfrdDocInf>\n"
            "                  <Tp>\n"
            "                    <CdOrPrtry>\n"
            "                      <Cd>CINV</Cd>\n"
            "                    </CdOrPrtry>\n"
            "                  </Tp>\n"
            "                  <Nb>INV-2026-1001</Nb>\n"
            "                </RfrdDocInf>\n"
            "                <RfrdDocAmt>\n"
            "                  <AdjstmntAmtAndRsn>\n"
            f'                    <Amt Ccy="KRW">{self.first_amount}</Amt>\n'
            f"                    <CdtDbtInd>{self.first_direction}</CdtDbtInd>\n"
            f"                    <Rsn>{self.first_reason}</Rsn>\n"
            f"                    <AddtlInf>{self.first_additional_information}</AddtlInf>\n"
            "                  </AdjstmntAmtAndRsn>\n"
            "                  <AdjstmntAmtAndRsn>\n"
            f'                    <Amt Ccy="KRW">{self.second_amount}</Amt>\n'
            f"                    <CdtDbtInd>{self.second_direction}</CdtDbtInd>\n"
            f"                    <Rsn>{second_reason}</Rsn>\n"
            f"                    <AddtlInf>{second_additional_information}</AddtlInf>\n"
            "                  </AdjstmntAmtAndRsn>\n"
            "                </RfrdDocAmt>\n"
            "              </Strd>"
        )
        return fixture.replace(marker, structured, 1).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"structured-adjustment-reason-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

    @staticmethod
    def _assert_sha256(value: object) -> None:
        """Require canonical digest syntax before equality comparisons."""
        if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
            raise AssertionError("evidence hash must be canonical SHA-256")

    @staticmethod
    def _assert_exact_transaction_amount(entry: object, detail: object) -> None:
        """Keep reason metadata separate from exact transaction amount truth."""
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
