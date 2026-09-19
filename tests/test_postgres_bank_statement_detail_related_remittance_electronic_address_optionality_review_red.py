"""Focused REDs for RemittanceLocationData2 electronic-address optionality."""

from __future__ import annotations

import re
import unittest

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    accept_bank_statement_evidence,
    lookup_bank_statement_entries,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting
from tests.test_postgres_bank_statement_detail_related_remittance_evidence_red import (
    BankStatementDetailRelatedRemittanceEvidenceRedTests,
)

_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailRelatedRemittanceElectronicAddressOptionalityReviewRedTests(
    BankStatementDetailRelatedRemittanceEvidenceRedTests
):
    """Preserve a method-only RemittanceLocationData2 without inventing an address."""

    def setUp(self) -> None:
        """Remove only ElctrncAdr from one otherwise unchanged location detail."""
        super().setUp()
        populated = (
            b"              <RmtLctnDtls>\n"
            b"                <Mtd>EMAL</Mtd>\n"
            b"                <ElctrncAdr>cash-application@example.test</ElctrncAdr>\n"
            b"              </RmtLctnDtls>\n"
        )
        method_only = (
            b"              <RmtLctnDtls>\n"
            b"                <Mtd>EMAL</Mtd>\n"
            b"              </RmtLctnDtls>\n"
        )
        if self.base_payload.count(populated) != 1:
            raise AssertionError("canonical related-remittance location marker must occur once")
        self.method_only_payload = self.base_payload.replace(populated, method_only, 1)
        self.method_only_records = (
            (
                self.base_records[0][0],
                (
                    ("EMAL", None),
                    self.base_records[0][1][1],
                ),
            ),
            self.base_records[1],
        )
        self.method_only_statement = parse_bank_statement_payload(
            self.method_only_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.method_only_expected_hash = self._expected_hash(self.method_only_records)

    def test_absent_electronic_address_is_material_related_remittance_evidence(self) -> None:
        """ElctrncAdr absence changes only the targeted remittance evidence chain."""
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_entry = self.method_only_statement.entries[0]
        changed_detail = changed_entry.entry_details[0]

        base_digest = getattr(base_detail, "related_remittance_evidence_hash", None)
        changed_digest = getattr(changed_detail, "related_remittance_evidence_hash", None)
        for value in (
            base_digest,
            changed_digest,
            base_detail.source_detail_hash,
            changed_detail.source_detail_hash,
            base_entry.source_entry_hash,
            changed_entry.source_entry_hash,
            self.base_statement.normalized_payload_hash,
            self.method_only_statement.normalized_payload_hash,
            self.base_statement.account_identifier_hash,
            self.method_only_statement.account_identifier_hash,
            self.base_statement.entries[1].source_entry_hash,
            self.method_only_statement.entries[1].source_entry_hash,
        ):
            self._assert_sha256(value)

        self.assertEqual(changed_digest, self.method_only_expected_hash)
        self.assertNotEqual(base_digest, changed_digest)
        self.assertNotEqual(base_detail.source_detail_hash, changed_detail.source_detail_hash)
        self.assertNotEqual(base_entry.source_entry_hash, changed_entry.source_entry_hash)
        self.assertNotEqual(
            self.base_statement.normalized_payload_hash,
            self.method_only_statement.normalized_payload_hash,
        )
        self.assertEqual(
            self.base_statement.account_identifier_hash,
            self.method_only_statement.account_identifier_hash,
        )
        self.assertEqual(
            self.base_statement.entries[1].source_entry_hash,
            self.method_only_statement.entries[1].source_entry_hash,
        )
        self.assertEqual(base_entry.entry_amount, changed_entry.entry_amount)
        self.assertEqual(base_entry.entry_currency_code, changed_entry.entry_currency_code)
        self.assertEqual(base_detail.detail_amount, changed_detail.detail_amount)
        self.assertEqual(base_detail.detail_currency_code, changed_detail.detail_currency_code)
        self._assert_entry_hash_binding(changed_entry, self.method_only_expected_hash)

    def test_electronic_address_removal_reaches_explicit_correction_boundary(self) -> None:
        """An accepted routing address cannot disappear silently under one statement identity."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "electronic-address-base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.method_only_payload, "electronic-address-absent"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_retains_method_only_location_without_fabricating_address_value(
        self,
    ) -> None:
        """Tenant readback retains the method-only location and a null address value."""
        accepted = accept_bank_statement_evidence(
            self._command(self.method_only_payload, "electronic-address-lookup"),
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
        actual = detail.get("related_remittance_information")
        expected = self._expected_records(self.method_only_records)

        self.assertEqual(detail.get("related_remittance_evidence_hash"), self.method_only_expected_hash)
        self.assertEqual(actual, expected)
        if not isinstance(actual, list) or not actual:
            raise AssertionError("related remittance readback must retain the source record")
        location_details = actual[0].get("remittance_location_details")
        if not isinstance(location_details, list) or not location_details:
            raise AssertionError("method-only location detail must remain buyer-visible")
        first_location = location_details[0]
        if not isinstance(first_location, dict):
            raise AssertionError("method-only location detail must be a mapping")
        self.assertEqual(first_location.get("method"), "EMAL")
        self.assertIsNone(first_location.get("electronic_address"))
        self.assertIsNone(first_location.get("postal_address"))
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    @staticmethod
    def _assert_sha256(value: object) -> None:
        """Require canonical purpose-bound SHA-256 evidence and stability hashes."""
        if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
            raise AssertionError("evidence hash must be canonical SHA-256")


if __name__ == "__main__":
    unittest.main()
