"""REDs for source-faithful contraction of repeated referred documents."""

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
    test_postgres_bank_statement_structured_referred_document_order_evidence_red
    as document_order_contract,
)
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_identification_cardinality_evidence_red
    as rls_snapshot_contract,
)

_PARENT_TEST = (
    document_order_contract.BankStatementStructuredReferredDocumentOrderEvidenceRedTests
)
_RLS_OWNER = (
    rls_snapshot_contract.
    BankStatementStructuredLineIdentificationCardinalityEvidenceRedTests
)
_CORRECTION_ERROR = document_order_contract._CORRECTION_ERROR
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredReferredDocumentContractionEvidenceRedTests(
    unittest.TestCase
):
    """Preserve a two-to-one repeated referred-document contraction."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the real-PostgreSQL repeated-document source-order fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Remove only the later document while retaining the exact first document."""
        self.parent = _PARENT_TEST(
            "test_referred_document_source_order_is_material_to_each_canonical_evidence_hash"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)
        self.line_contract = self.parent.line_contract

        self.first_document_number = self.parent.first_document_number
        self.second_document_number = self.parent.second_document_number
        self.base_payload = self.parent.base_payload
        self.base_statement = self.parent.base_statement
        self.base_projection = deepcopy(self.parent.base_projection)
        if len(self.base_projection) != 2:
            raise AssertionError("parent must expose exactly two referred documents")
        if [item.get("document_number") for item in self.base_projection] != [
            self.first_document_number,
            self.second_document_number,
        ]:
            raise AssertionError("parent must retain the exact two-document source order")
        self.retained_document_projection = deepcopy(self.base_projection[0])
        self.removed_document_projection = deepcopy(self.base_projection[1])

        (
            self.changed_payload,
            self.removed_document_block,
            self.removed_document_offset,
        ) = self._remove_second_referred_document(self.base_payload)
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_projection = [deepcopy(self.retained_document_projection)]
        if self._restore_second_referred_document(
            self.changed_payload,
            self.removed_document_block,
            self.removed_document_offset,
        ) != self.base_payload:
            raise AssertionError(
                "exact removed RfrdDocInf bytes must restore the two-document source"
            )

    def test_document_contraction_is_material_to_each_evidence_hash(self) -> None:
        """Bind exact two-to-one document contraction to canonical evidence identity."""
        self.assertEqual(
            self._restore_second_referred_document(
                self.changed_payload,
                self.removed_document_block,
                self.removed_document_offset,
            ),
            self.base_payload,
        )
        self._assert_non_structured_normalization_unchanged()

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

    def test_two_document_baseline_reads_back_in_source_order(self) -> None:
        """Persist both documents so relational first-document truncation cannot pass."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "referred-document-contraction-two-document-baseline",
            ),
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])
        self.assertEqual(
            self.line_contract.store._artifacts,
            {self.base_statement.source_artifact_hash: self.base_payload},
        )
        statement, entries = self._lookup(
            str(accepted["bank_statement_record_id"]),
            self.base_statement,
        )
        self._assert_statement_hashes(statement, self.base_statement)
        self._assert_primary_entry(
            entries[0],
            self.base_statement.entries[0],
            self.base_projection,
            expected_document_numbers=(
                self.first_document_number,
                self.second_document_number,
            ),
        )
        self._assert_sibling(entries[1], self.base_statement.entries[1])

    def test_rejected_document_contraction_leaves_no_evidence_residue(self) -> None:
        """Reject document removal through tenant RLS without persistence residue."""
        runtime_url = _RLS_OWNER._restricted_bank_statement_runtime_url(self)
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.base_payload,
                "referred-document-contraction-base",
            ),
            runtime_url,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])
        record_id = str(accepted["bank_statement_record_id"])
        before_rows = _RLS_OWNER._tenant_statement_rows(self, runtime_url)
        expected_artifacts = {
            self.base_statement.source_artifact_hash: self.base_payload,
        }
        self.assertEqual(self.line_contract.store._artifacts, expected_artifacts)
        self.assertTrue(before_rows["bank_statement_artifact"])
        self.assertTrue(before_rows["bank_statement_entry"])
        self.assertTrue(before_rows["bank_statement_entry_detail"])
        self.assertTrue(
            any(
                str(row[0]) == record_id
                for row in before_rows["bank_statement_record"]
            )
        )

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self.line_contract._command(
                    self.changed_payload,
                    "referred-document-contraction-changed",
                ),
                runtime_url,
                self.line_contract.case.policy.tenant_reference,
                artifact_store=self.line_contract.store,
            )

        self.assertEqual(
            _RLS_OWNER._tenant_statement_rows(self, runtime_url),
            before_rows,
        )
        self.assertEqual(self.line_contract.store._artifacts, expected_artifacts)
        self.assertNotIn(
            self.changed_statement.source_artifact_hash,
            self.line_contract.store._artifacts,
        )

    def test_contracted_document_reads_back_only_retained_source_document(self) -> None:
        """Persist one referred document without stale second-document evidence."""
        accepted = accept_bank_statement_evidence(
            self.line_contract._command(
                self.changed_payload,
                "referred-document-contraction-lookup",
            ),
            posting.DATABASE_URL,
            self.line_contract.case.policy.tenant_reference,
            artifact_store=self.line_contract.store,
        )
        self.assertFalse(accepted["replayed"])
        self.assertEqual(
            self.line_contract.store._artifacts,
            {self.changed_statement.source_artifact_hash: self.changed_payload},
        )
        statement, entries = self._lookup(
            str(accepted["bank_statement_record_id"]),
            self.changed_statement,
        )
        self._assert_statement_hashes(statement, self.changed_statement)
        self._assert_primary_entry(
            entries[0],
            self.changed_statement.entries[0],
            self.changed_projection,
            expected_document_numbers=(self.first_document_number,),
        )
        self._assert_sibling(entries[1], self.changed_statement.entries[1])

    def _remove_second_referred_document(
        self,
        payload: bytes,
    ) -> tuple[bytes, str, int]:
        """Remove the later complete RfrdDocInf block and retain exact source bytes."""
        text = payload.decode("utf-8")
        first_start, first_end = self.parent._document_bounds(
            text,
            self.first_document_number,
        )
        second_start, second_end = self.parent._document_bounds(
            text,
            self.second_document_number,
        )
        if first_start >= second_start or first_end > second_start:
            raise AssertionError("referred documents must be distinct and source ordered")
        removed = text[second_start:second_end]
        if removed.count("<RfrdDocInf>") != 1 or removed.count("</RfrdDocInf>") != 1:
            raise AssertionError("target must be exactly one complete referred document")
        if f"<Nb>{self.second_document_number}</Nb>" not in removed:
            raise AssertionError("target block must be the exact second document")
        if self.first_document_number in removed:
            raise AssertionError("target block must not contain the retained document")
        changed = (text[:second_start] + text[second_end:]).encode("utf-8")
        if self._source_document_numbers(changed) != [self.first_document_number]:
            raise AssertionError("contraction must retain only the first document")
        return changed, removed, second_start

    def _restore_second_referred_document(
        self,
        payload: bytes,
        removed: str,
        offset: int,
    ) -> bytes:
        """Reinsert exact second-document bytes at their original source offset."""
        text = payload.decode("utf-8")
        if not 0 <= offset <= len(text):
            raise AssertionError("removed document offset must remain within source bounds")
        restored = (text[:offset] + removed + text[offset:]).encode("utf-8")
        if self._source_document_numbers(restored) != [
            self.first_document_number,
            self.second_document_number,
        ]:
            raise AssertionError("restoration must recover original document source order")
        return restored

    def _source_document_numbers(self, payload: bytes) -> list[str]:
        """Return known referred-document numbers still present in source order."""
        text = payload.decode("utf-8")
        present: list[tuple[int, str]] = []
        for number in (self.first_document_number, self.second_document_number):
            marker = f"<Nb>{number}</Nb>"
            if text.count(marker) > 1:
                raise AssertionError("referred-document number marker must stay unique")
            if marker in text:
                present.append((text.index(marker), number))
        return [number for _, number in sorted(present)]

    def _assert_primary_entry(
        self,
        persisted_entry: dict[str, object],
        expected_entry: object,
        expected_projection: list[dict[str, object]],
        *,
        expected_document_numbers: tuple[str, ...],
    ) -> None:
        """Assert exact document population, hashes, and transaction truth."""
        persisted_details = persisted_entry["entry_details"]
        self.assertEqual(
            len(persisted_details),
            len(expected_entry.entry_details),
        )
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
        persisted_projection = persisted_detail[_STRUCTURED_EVIDENCE_KEY]
        self.assertEqual(
            tuple(str(item.get("document_number")) for item in persisted_projection),
            expected_document_numbers,
        )
        if expected_document_numbers == (self.first_document_number,):
            self.assertEqual(persisted_projection, [self.retained_document_projection])
            self.assertNotIn(self.removed_document_projection, persisted_projection)
        elif expected_document_numbers == (
            self.first_document_number,
            self.second_document_number,
        ):
            self.assertEqual(
                persisted_projection,
                [self.retained_document_projection, self.removed_document_projection],
            )
        else:
            raise AssertionError("unexpected document contraction oracle")

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
        """Keep referred-document evidence isolated from every sibling detail."""
        persisted_details = persisted_entry["entry_details"]
        self.assertEqual(len(persisted_details), len(expected_entry.entry_details))
        self.assertEqual(
            persisted_entry["source_entry_hash"],
            expected_entry.source_entry_hash,
        )
        self.assertEqual(
            Decimal(str(persisted_entry["entry_amount"])),
            expected_entry.entry_amount,
        )
        self.assertEqual(
            persisted_entry["entry_currency_code"],
            expected_entry.entry_currency_code,
        )
        for persisted_detail, expected_detail in zip(
            persisted_details,
            expected_entry.entry_details,
            strict=True,
        ):
            self.assertEqual(
                persisted_detail["source_detail_hash"],
                expected_detail.source_detail_hash,
            )
            self.assertEqual(
                Decimal(str(persisted_detail["detail_amount"])),
                expected_detail.detail_amount,
            )
            self.assertEqual(
                persisted_detail["detail_currency_code"],
                expected_detail.detail_currency_code,
            )
            self.assertNotIn(_STRUCTURED_EVIDENCE_KEY, persisted_detail)

    def _lookup(
        self,
        record_id: str,
        expected_statement: object,
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        """Read one statement and assert complete entry cardinality."""
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

    def _assert_non_structured_normalization_unchanged(self) -> None:
        """Prove document removal does not alter unrelated normalized accounting fields."""
        statement_fields = (
            "message_definition_identifier",
            "statement_identity_reference",
            "electronic_sequence_number",
            "legal_sequence_number",
            "period_start_at",
            "period_end_at",
            "opening_balance_hash",
            "closing_balance_hash",
            "account_currency_code",
            "account_identifier_hash",
        )
        self.assertEqual(
            tuple(getattr(self.base_statement, name) for name in statement_fields),
            tuple(getattr(self.changed_statement, name) for name in statement_fields),
        )
        self.assertEqual(
            len(self.base_statement.entries),
            len(self.changed_statement.entries),
        )
        entry_fields = (
            "source_entry_identity",
            "entry_sequence_number",
            "source_locator_path",
            "booking_occurred_at",
            "value_occurred_at",
            "entry_amount",
            "entry_currency_code",
            "credit_debit_code",
            "reversal_indicator",
            "bank_transaction_domain_code",
            "bank_transaction_family_code",
            "bank_transaction_subfamily_code",
            "end_to_end_reference",
            "account_servicer_reference",
            "mandate_reference",
            "cheque_reference",
            "remittance_evidence_text",
            "counterparty_evidence_hash",
        )
        detail_fields = (
            "detail_sequence_number",
            "source_locator_path",
            "detail_amount",
            "detail_currency_code",
            "credit_debit_code",
            "end_to_end_reference",
            "account_servicer_reference",
            "remittance_evidence_text",
        )
        for base_entry, changed_entry in zip(
            self.base_statement.entries,
            self.changed_statement.entries,
            strict=True,
        ):
            self.assertEqual(
                tuple(getattr(base_entry, name) for name in entry_fields),
                tuple(getattr(changed_entry, name) for name in entry_fields),
            )
            self.assertEqual(
                len(base_entry.entry_details),
                len(changed_entry.entry_details),
            )
            for base_detail, changed_detail in zip(
                base_entry.entry_details,
                changed_entry.entry_details,
                strict=True,
            ):
                self.assertEqual(
                    tuple(getattr(base_detail, name) for name in detail_fields),
                    tuple(getattr(changed_detail, name) for name in detail_fields),
                )


if __name__ == "__main__":
    unittest.main()
