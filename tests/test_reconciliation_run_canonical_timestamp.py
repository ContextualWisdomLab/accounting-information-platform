"""Canonical UTC command-identity regressions for reconciliation runs."""

from __future__ import annotations

import unittest
import unittest.mock as mock
import uuid

from accounting_information_platform import AccountingValidationError, accept_reconciliation_run
from accounting_information_platform.reconciliation_run import _command_hash, _parse_timestamp


class ReconciliationRunCanonicalTimestampTests(unittest.TestCase):
    """Keep externally supplied cutoff strings canonical before command hashing or I/O."""

    @staticmethod
    def _command() -> dict[str, object]:
        return {
            "tenant_reference": "urn:cwl:tenant:timestamp-contract",
            "bank_statement_record_id": str(uuid.UUID(int=1)),
            "legal_entity_reference": "urn:cwl:legal_entity:timestamp-contract",
            "accounting_book_reference": "urn:cwl:accounting_book:timestamp-contract",
            "bank_cutoff_at": "2026-08-24T23:59:59Z",
            "book_cutoff_at": "2026-08-24T23:59:59Z",
            "matching_policy_version": "deterministic-v1",
            "knowledge_cutoff_at": "2026-09-01T00:00:00Z",
            "reconciliation_idempotency_key": "timestamp-contract-key",
            "source_payload_hash": "sha256:" + "1" * 64,
        }

    def test_each_cutoff_rejects_forbidden_zero_offset_forms_before_database(self) -> None:
        """Equivalent but noncanonical UTC spellings never become persisted command evidence."""
        invalid_values = (
            "2026-08-24T23:59:59-00:00",
            "2026-08-24 23:59:59Z",
            "2026-08-24T23:59:59+0000",
        )
        for field_name in ("bank_cutoff_at", "book_cutoff_at", "knowledge_cutoff_at"):
            for invalid_value in invalid_values:
                with self.subTest(field=field_name, value=invalid_value):
                    command = self._command()
                    command[field_name] = invalid_value
                    with mock.patch(
                        "accounting_information_platform.reconciliation_run.PostgresPostingLedger"
                    ) as ledger_type:
                        with self.assertRaisesRegex(
                            AccountingValidationError, "canonical UTC timestamp"
                        ):
                            accept_reconciliation_run(
                                command,
                                "postgresql://must-not-be-used",
                                str(command["tenant_reference"]),
                            )
                        ledger_type.assert_not_called()

    def test_allowed_utc_spellings_share_one_command_hash(self) -> None:
        """Literal Z and +00:00 normalize identically while forbidden -00:00 is rejected."""
        zulu = _parse_timestamp("2026-08-24T23:59:59Z", "bank_cutoff_at")
        explicit = _parse_timestamp("2026-08-24T23:59:59+00:00", "bank_cutoff_at")
        with self.assertRaisesRegex(AccountingValidationError, "canonical UTC timestamp"):
            _parse_timestamp("2026-08-24T23:59:59-00:00", "bank_cutoff_at")

        common = {
            "tenant_reference": "urn:cwl:tenant:timestamp-contract",
            "statement_id": uuid.UUID(int=1),
            "legal_entity_reference": "urn:cwl:legal_entity:timestamp-contract",
            "accounting_book_reference": "urn:cwl:accounting_book:timestamp-contract",
            "book_cutoff_at": explicit,
            "matching_policy_version": "deterministic-v1",
            "knowledge_cutoff_at": _parse_timestamp(
                "2026-09-01T00:00:00Z", "knowledge_cutoff_at"
            ),
            "idempotency_key": "timestamp-contract-key",
            "source_payload_hash": "sha256:" + "1" * 64,
            "assignment_id": uuid.UUID(int=2),
            "normalized_payload_hash": "sha256:" + "2" * 64,
        }
        self.assertEqual(
            _command_hash(bank_cutoff_at=zulu, **common),
            _command_hash(bank_cutoff_at=explicit, **common),
        )

    def test_canonical_grammar_still_rejects_invalid_calendar_instants(self) -> None:
        """A canonical spelling must still name a real calendar instant."""
        with self.assertRaisesRegex(AccountingValidationError, "ISO-8601 timestamp"):
            _parse_timestamp("2026-02-30T23:59:59Z", "bank_cutoff_at")


if __name__ == "__main__":
    unittest.main()
