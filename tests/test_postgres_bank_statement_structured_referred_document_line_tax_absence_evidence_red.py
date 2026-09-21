"""REDs for source-faithful absence of line Tax Amount evidence."""

from __future__ import annotations

import hashlib
import unittest
from copy import deepcopy
from decimal import Decimal

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    accept_bank_statement_evidence,
    lookup_bank_statement,
    lookup_bank_statement_entries,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_repeated_tax_contraction_evidence_red
    as tax_contraction_contract,
)
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_discount_absence_sibling_readback_red
    as sibling_readback_contract,
)

_CORRECTION_ERROR = tax_contraction_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"
_TAX_KEY = "tax_amounts"
_STAT = {"type_code": "STAT", "amount": "950", "currency_code": "KRW"}


class BankStatementStructuredLineTaxAbsenceEvidenceRedTests(unittest.TestCase):
    """Preserve a one-to-zero TaxAmt contraction on the exact source line."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL repeated-tax contraction fixture."""
        tax_contraction_contract.BankStatementStructuredLineRepeatedTaxContractionEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Start from #123's one-tax source and remove only the retained STAT member."""
        self.parent = (
            tax_contraction_contract.BankStatementStructuredLineRepeatedTaxContractionEvidenceRedTests(
                "test_buyer_read_keeps_only_the_source_retained_tax"
            )
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        self.base_payload = self.parent.changed_payload
        self.base_statement = self.parent.changed_statement
        self.base_projection = deepcopy(self.parent.changed_projection)
        first_line, second_line = self.parent.parent._line_details(self.base_projection)
        if second_line.get(_TAX_KEY) != [_STAT]:
            raise AssertionError(
                "#123 contracted source must retain exactly STAT / 950 KRW"
            )
        if second_line.get("discount_applied_amounts") != [
            {"type_code": "APDS", "amount": "100", "currency_code": "KRW"}
        ]:
            raise AssertionError(
                "Stockitem2 must retain APDS / 100 KRW so direct Amount survives"
            )
        self.first_line_projection = deepcopy(first_line)
        self.second_line_without_tax = deepcopy(second_line)
        self.second_line_without_tax.pop(_TAX_KEY)

        (
            self.changed_payload,
            self.removed_tax_block,
            self.removed_tax_offset,
        ) = self._remove_remaining_second_line_tax(self.base_payload)
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_projection = deepcopy(self.base_projection)
        changed_lines = self.parent.parent._line_details(self.changed_projection)
        removed = changed_lines[1].pop(_TAX_KEY, None)
        if removed != [_STAT]:
            raise AssertionError(
                "changed projection must remove exact STAT / 950-KRW population"
            )
        if self._restore_remaining_second_line_tax(
            self.changed_payload,
            self.removed_tax_block,
            self.removed_tax_offset,
        ) != self.base_payload:
            raise AssertionError(
                "exact removed TaxAmt bytes must restore the one-tax source"
            )

    def test_line_tax_absence_is_material_to_each_evidence_hash(self) -> None:
        """Bind one-to-zero TaxAmt contraction to raw and canonical evidence identity."""
        self.assertEqual(
            self._restore_remaining_second_line_tax(
                self.changed_payload,
                self.removed_tax_block,
                self.removed_tax_offset,
            ),
            self.base_payload,
        )
        normalization_contract = tax_contraction_contract._NORMALIZATION_PARENT_TEST
        normalization_contract._assert_non_structured_normalization_unchanged(self)

        self.assertEqual(
            self.base_statement.source_artifact_hash,
            "sha256:" + hashlib.sha256(self.base_payload).hexdigest(),
        )
        self.assertEqual(
            self.changed_statement.source_artifact_hash,
            "sha256:" + hashlib.sha256(self.changed_payload).hexdigest(),
        )
        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.changed_statement.source_artifact_hash,
        )

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
            self.line_contract._assert_sha256(value)

        self.assertEqual(
            base_detail.source_detail_hash,
            self.line_contract._expected_detail_hash(
                base_detail,
                self.base_projection,
            ),
        )
        self.assertEqual(
            changed_detail.source_detail_hash,
            self.line_contract._expected_detail_hash(
                changed_detail,
                self.changed_projection,
            ),
        )
        self.assertEqual(
            base_entry.source_entry_hash,
            self.line_contract._expected_entry_hash(
                base_entry,
                {1: self.base_projection},
            ),
        )
        self.assertEqual(
            changed_entry.source_entry_hash,
            self.line_contract._expected_entry_hash(
                changed_entry,
                {1: self.changed_projection},
            ),
        )
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.line_contract._expected_statement_hash(
                self.base_statement,
                {(1, 1): self.base_projection},
            ),
        )
        self.assertEqual(
            self.changed_statement.normalized_payload_hash,
            self.line_contract._expected_statement_hash(
                self.changed_statement,
                {(1, 1): self.changed_projection},
            ),
        )
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
        self.line_contract._assert_exact_transaction_amount(base_entry, base_detail)
        self.line_contract._assert_exact_transaction_amount(
            changed_entry,
            changed_detail,
        )

    def test_tax_present_baseline_reads_back_before_zero_population(self) -> None:
        """Persist STAT first so unconditional TaxAmt loss cannot false-GREEN."""
        statement, entries = self._persist_and_read(
            self.base_payload,
            self.base_statement,
            "line-tax-absence-present-baseline",
        )
        self._assert_statement_hashes(statement, self.base_statement)
        self._assert_primary_entry(
            entries[0],
            self.base_statement.entries[0],
            self.base_projection,
            expect_tax=True,
        )
        self._assert_sibling(entries[1], self.base_statement.entries[1])

    def test_tax_absence_reads_back_without_stale_member(self) -> None:
        """Persist zero TaxAmt while APDS keeps the direct Amount group populated."""
        statement, entries = self._persist_and_read(
            self.changed_payload,
            self.changed_statement,
            "line-tax-absence-lookup",
        )
        self._assert_statement_hashes(statement, self.changed_statement)
        self._assert_primary_entry(
            entries[0],
            self.changed_statement.entries[0],
            self.changed_projection,
            expect_tax=False,
        )
        self._assert_sibling(entries[1], self.changed_statement.entries[1])

    def test_rejected_tax_absence_leaves_no_evidence_residue(self) -> None:
        """Reject source contraction through tenant RLS without persistence residue."""
        restricted_owner = (
            self.parent.parent.parent.parent.parent.parent.parent._ancestor_with(
                "_restricted_bank_statement_runtime_url",
                "_tenant_statement_rows",
            )
        )
        runtime_url = restricted_owner._restricted_bank_statement_runtime_url()
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "line-tax-absence-base",
            ),
            runtime_url,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])
        before_rows = restricted_owner._tenant_statement_rows(runtime_url)
        expected_artifacts = {
            self.base_statement.source_artifact_hash: self.base_payload,
        }
        self.assertEqual(self.line_contract.store._artifacts, expected_artifacts)
        self.assertTrue(before_rows["bank_statement_artifact"])
        self.assertTrue(before_rows["bank_statement_entry"])
        self.assertTrue(before_rows["bank_statement_entry_detail"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self.line_contract._command(
                    self.changed_payload,
                    "line-tax-absence-changed",
                ),
                runtime_url,
                self.line_contract.case.policy.tenant_reference,
                artifact_store=self.line_contract.store,
            )

        self.assertEqual(
            restricted_owner._tenant_statement_rows(runtime_url),
            before_rows,
        )
        self.assertEqual(self.line_contract.store._artifacts, expected_artifacts)
        self.assertNotIn(
            self.changed_statement.source_artifact_hash,
            self.line_contract.store._artifacts,
        )

    def _remove_remaining_second_line_tax(
        self,
        payload: bytes,
    ) -> tuple[bytes, bytes, int]:
        """Remove the exact retained STAT / 950-KRW complete TaxAmt block."""
        blocks = self.parent._second_line_tax_blocks(payload)
        if len(blocks) != 1:
            raise AssertionError(
                "#123 contracted source must expose exactly one TaxAmt block"
            )
        block = blocks[0]
        if "<Cd>STAT</Cd>" not in block or '">950.00</Amt>' not in block:
            raise AssertionError("retained TaxAmt must be exact STAT / 950 KRW")
        segment, segment_start, _ = self.parent.parent._line_segment(
            payload,
            "Stockitem2",
        )
        if segment.count(block) != 1:
            raise AssertionError("retained TaxAmt block must occur once in Stockitem2")
        block_bytes = block.encode("utf-8")
        segment_bytes = segment.encode("utf-8")
        local_offset = segment_bytes.index(block_bytes)
        prefix_bytes = payload.decode("utf-8")[:segment_start].encode("utf-8")
        offset = len(prefix_bytes) + local_offset
        if payload[offset : offset + len(block_bytes)] != block_bytes:
            raise AssertionError("TaxAmt byte offset must identify exact source bytes")
        changed = payload[:offset] + payload[offset + len(block_bytes) :]
        if self.parent._second_line_tax_blocks(changed):
            raise AssertionError("Stockitem2 must expose zero TaxAmt after contraction")
        changed_segment, _, _ = self.parent.parent._line_segment(
            changed,
            "Stockitem2",
        )
        if "<DscntApldAmt>" not in changed_segment:
            raise AssertionError(
                "APDS must keep Stockitem2 direct Amount populated after TaxAmt removal"
            )
        return changed, block_bytes, offset

    def _restore_remaining_second_line_tax(
        self,
        payload: bytes,
        block: bytes,
        offset: int,
    ) -> bytes:
        """Reinsert the exact removed TaxAmt source bytes at their original byte offset."""
        if not 0 <= offset <= len(payload):
            raise AssertionError("removed TaxAmt offset must stay within byte bounds")
        restored = payload[:offset] + block + payload[offset:]
        blocks = self.parent._second_line_tax_blocks(restored)
        if len(blocks) != 1 or "<Cd>STAT</Cd>" not in blocks[0]:
            raise AssertionError("restoration must recover the single STAT TaxAmt")
        return restored

    def _persist_and_read(
        self,
        payload: bytes,
        expected_statement: object,
        idempotency_key: str,
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        """Persist one source and return complete statement and entry readback."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(payload, idempotency_key),
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])
        self.assertEqual(
            self.line_contract.store._artifacts,
            {expected_statement.source_artifact_hash: payload},
        )
        record_id = str(accepted["bank_statement_record_id"])
        statement = lookup_bank_statement(
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            record_id,
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            record_id,
        )
        entries = document["bank_statement_entries"]
        self.assertEqual(len(entries), len(expected_statement.entries))
        return statement, entries

    def _assert_statement_hashes(
        self,
        persisted: dict[str, object],
        expected: object,
    ) -> None:
        """Bind persisted raw and normalized statement hashes to parsed evidence."""
        self.assertEqual(
            persisted["source_artifact_hash"],
            expected.source_artifact_hash,
        )
        self.assertEqual(
            persisted["normalized_payload_hash"],
            expected.normalized_payload_hash,
        )

    def _assert_primary_entry(
        self,
        persisted_entry: dict[str, object],
        expected_entry: object,
        expected_projection: list[dict[str, object]],
        *,
        expect_tax: bool,
    ) -> None:
        """Assert exact line-tax projection, hashes, and transaction truth."""
        persisted_details = persisted_entry["entry_details"]
        self.assertEqual(len(persisted_details), len(expected_entry.entry_details))
        persisted_detail = persisted_details[0]
        expected_detail = expected_entry.entry_details[0]
        self.assertEqual(
            persisted_entry["source_entry_hash"],
            expected_entry.source_entry_hash,
        )
        self.assertEqual(
            persisted_detail["source_detail_hash"],
            expected_detail.source_detail_hash,
        )
        self.assertEqual(
            persisted_detail[_STRUCTURED_EVIDENCE_KEY],
            expected_projection,
        )

        first_line, second_line = self.parent.parent._line_details(
            persisted_detail[_STRUCTURED_EVIDENCE_KEY]
        )
        self.assertEqual(first_line, self.first_line_projection)
        if expect_tax:
            self.assertEqual(second_line.get(_TAX_KEY), [_STAT])
        else:
            self.assertNotIn(_TAX_KEY, second_line)
            self.assertEqual(second_line, self.second_line_without_tax)
        self.assertEqual(
            second_line.get("discount_applied_amounts"),
            [{"type_code": "APDS", "amount": "100", "currency_code": "KRW"}],
        )
        self.assertEqual(
            Decimal(str(persisted_entry["entry_amount"])),
            Decimal("25000.00"),
        )
        self.assertEqual(persisted_entry["entry_currency_code"], "KRW")
        self.assertEqual(
            Decimal(str(persisted_detail["detail_amount"])),
            Decimal("25000.00"),
        )
        self.assertEqual(persisted_detail["detail_currency_code"], "KRW")

    def _assert_sibling(
        self,
        persisted_entry: dict[str, object],
        expected_entry: object,
    ) -> None:
        """Compare every buyer-visible field on the unrelated sibling entry."""
        helper = (
            sibling_readback_contract.
            BankStatementStructuredLineDiscountAbsenceSiblingReadbackRedTests(
                "test_discount_present_baseline_reads_complete_sibling_projection"
            )
        )
        helper._assert_complete_sibling(persisted_entry, expected_entry)


if __name__ == "__main__":
    unittest.main()