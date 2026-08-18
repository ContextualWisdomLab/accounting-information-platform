"""Consumer-side ingest of the Billing-owned journal proposal contract."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from accounting_information_platform import (
    AccountingPolicy,
    AccountingValidationError,
    JournalProposal,
    PostingLedger,
    ingest_journal_proposal,
)


class JournalProposalIngestTests(unittest.TestCase):
    """Pin the published proposal_status field and reject non-ingestible rows."""

    def test_validated_billing_proposal_ingests_status_free_and_posts(self) -> None:
        """propose_journal validated JSON becomes a status-free JournalProposal."""
        payload = self._billing_proposal(proposal_status="validated")

        proposal = ingest_journal_proposal(payload)
        receipt = PostingLedger().post(proposal, self._policy())

        self.assertIsInstance(proposal, JournalProposal)
        self.assertFalse(hasattr(proposal, "proposal_status"))
        self.assertFalse(hasattr(proposal, "proposal_status_code"))
        self.assertEqual(proposal.intended_book_role_code, "primary_statutory")
        self.assertEqual(proposal.lines[0].account_role_code, "accounts_receivable")
        self.assertEqual(proposal.lines[0].debit_amount, Decimal("25000"))
        self.assertEqual(proposal.lines[1].account_role_code, "usage_revenue")
        self.assertEqual(proposal.lines[1].credit_amount, Decimal("25000"))
        self.assertEqual(
            proposal.idempotency_key,
            (
                "urn:cwl:tenant_001:invoice_draft:019d7b92-1aa0-7a7f-b61c-962c0f4bf612"
                ":sha256:" + "a" * 64 + ":v1"
            ),
        )
        self.assertEqual(receipt.posting_status_code, "posted")
        self.assertEqual(receipt.source_payload_hash, payload["source_payload_hash"])

    def test_exported_proposal_is_ingestible(self) -> None:
        """exported is accepted if a later Billing export path ever publishes it."""
        proposal = ingest_journal_proposal(self._billing_proposal(proposal_status="exported"))

        self.assertEqual(proposal.proposal_id, "019d7b92-1aa0-7a7f-b61c-962c0f4bf612")
        self.assertEqual(proposal.accounting_date, date(2026, 8, 31))

    def test_draft_and_rejected_statuses_fail_closed_with_next_action(self) -> None:
        """draft and rejected are schema-legal but not ingestible."""
        with self.assertRaisesRegex(AccountingValidationError, "Ask Billing to emit a validated proposal"):
            ingest_journal_proposal(self._billing_proposal(proposal_status="draft"))
        with self.assertRaisesRegex(AccountingValidationError, "Ask Billing to emit a validated proposal"):
            ingest_journal_proposal(self._billing_proposal(proposal_status="rejected"))

    def test_posted_and_unknown_statuses_are_never_accepted(self) -> None:
        """posted is not a Billing proposal state; unknown values fail closed."""
        with self.assertRaisesRegex(AccountingValidationError, "posting_receipt"):
            ingest_journal_proposal(self._billing_proposal(proposal_status="posted"))
        with self.assertRaisesRegex(AccountingValidationError, "validated or exported"):
            ingest_journal_proposal(self._billing_proposal(proposal_status="held"))

    def test_operational_reject_rows_are_not_ingested(self) -> None:
        """Billing reject receipts are not schema-valid proposals."""
        with self.assertRaisesRegex(
            AccountingValidationError, "do not ingest the reject row"
        ):
            ingest_journal_proposal(
                {
                    "proposal_contract_version": 1,
                    "journal_proposal_outcome_code": "rejected",
                    "rejection_reason_code": "invoice_draft_not_found",
                }
            )
        with self.assertRaisesRegex(
            AccountingValidationError, "do not ingest the reject row"
        ):
            ingest_journal_proposal({"rejection_reason_code": "tenant_not_found"})

    def test_published_field_name_and_payload_shape_failures_name_the_next_action(
        self,
    ) -> None:
        """Operators are told to supply proposal_status and a JSON object."""
        with self.assertRaisesRegex(AccountingValidationError, "JSON object"):
            ingest_journal_proposal(["not-an-object"])
        renamed = self._billing_proposal()
        renamed["proposal_status_code"] = renamed.pop("proposal_status")
        with self.assertRaisesRegex(
            AccountingValidationError, "proposal_status, not proposal_status_code"
        ):
            ingest_journal_proposal(renamed)
        missing_status = self._billing_proposal()
        del missing_status["proposal_status"]
        with self.assertRaisesRegex(AccountingValidationError, "proposal_status is required"):
            ingest_journal_proposal(missing_status)
        missing_proposed_at = self._billing_proposal()
        del missing_proposed_at["proposed_at"]
        with self.assertRaisesRegex(AccountingValidationError, "proposed_at is required"):
            ingest_journal_proposal(missing_proposed_at)
        empty_proposed_at = self._billing_proposal()
        empty_proposed_at["proposed_at"] = ""
        with self.assertRaisesRegex(AccountingValidationError, "proposed_at is required"):
            ingest_journal_proposal(empty_proposed_at)
        bad_date = self._billing_proposal()
        bad_date["accounting_date"] = "31-08-2026"
        with self.assertRaisesRegex(AccountingValidationError, "ISO date"):
            ingest_journal_proposal(bad_date)
        bad_lines = self._billing_proposal()
        bad_lines["lines"] = ["not-a-line"]
        with self.assertRaisesRegex(AccountingValidationError, "line object"):
            ingest_journal_proposal(bad_lines)
        bad_refs = self._billing_proposal()
        bad_refs["source_event_references"] = "urn:cwl:billing:invoice_draft:one"
        with self.assertRaisesRegex(AccountingValidationError, "source_event_references"):
            ingest_journal_proposal(bad_refs)
        bad_line_array = self._billing_proposal()
        bad_line_array["lines"] = "not-an-array"
        with self.assertRaisesRegex(AccountingValidationError, "array of journal line"):
            ingest_journal_proposal(bad_line_array)
        non_iso_type = self._billing_proposal()
        non_iso_type["transaction_date"] = 20260831
        with self.assertRaisesRegex(AccountingValidationError, "ISO date"):
            ingest_journal_proposal(non_iso_type)

    def test_missing_line_keys_and_non_string_amounts_fail_closed(self) -> None:
        """Billing KRW lines must carry integer line numbers and decimal strings."""
        missing_line_number = self._billing_proposal()
        del missing_line_number["lines"][0]["line_number"]
        with self.assertRaisesRegex(AccountingValidationError, "line_number is required"):
            ingest_journal_proposal(missing_line_number)
        missing_debit = self._billing_proposal()
        del missing_debit["lines"][0]["debit_amount"]
        with self.assertRaisesRegex(AccountingValidationError, "debit_amount is required"):
            ingest_journal_proposal(missing_debit)
        empty_credit = self._billing_proposal()
        empty_credit["lines"][1]["credit_amount"] = ""
        with self.assertRaisesRegex(AccountingValidationError, "credit_amount is required"):
            ingest_journal_proposal(empty_credit)
        float_amount = self._billing_proposal()
        float_amount["lines"][0]["debit_amount"] = 25000.5
        float_amount["lines"][1]["credit_amount"] = 25000.5
        with self.assertRaisesRegex(AccountingValidationError, "canonical decimal string"):
            ingest_journal_proposal(float_amount)
        integer_amount = self._billing_proposal()
        integer_amount["lines"][0]["debit_amount"] = 25000
        integer_amount["lines"][1]["credit_amount"] = 25000
        with self.assertRaisesRegex(AccountingValidationError, "canonical decimal string"):
            ingest_journal_proposal(integer_amount)
        bool_line_number = self._billing_proposal()
        bool_line_number["lines"][0]["line_number"] = True
        with self.assertRaisesRegex(AccountingValidationError, "line_number must be an integer"):
            ingest_journal_proposal(bool_line_number)
        string_line_number = self._billing_proposal()
        string_line_number["lines"][0]["line_number"] = "1"
        with self.assertRaisesRegex(AccountingValidationError, "line_number must be an integer"):
            ingest_journal_proposal(string_line_number)

    def test_more_than_six_fractional_digits_fail_closed_without_rounding(self) -> None:
        """A 7-place amount cannot be coerced to numeric(38, 6)."""
        too_long = self._billing_proposal()
        too_long["lines"][0]["debit_amount"] = "0.0000010"
        too_long["lines"][1]["credit_amount"] = "0.0000010"
        with self.assertRaisesRegex(
            AccountingValidationError, "at most six fractional digits"
        ):
            ingest_journal_proposal(too_long)
        six_places = self._billing_proposal(
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0.000001",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "0",
                    "credit_amount": "0.000001",
                },
            ]
        )
        two_places = self._billing_proposal(
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "25000.50",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "0",
                    "credit_amount": "25000.50",
                },
            ]
        )

        six = ingest_journal_proposal(six_places)
        two = ingest_journal_proposal(two_places)

        self.assertEqual(six.lines[0].debit_amount, Decimal("0.000001"))
        self.assertEqual(six.lines[1].credit_amount, Decimal("0.000001"))
        self.assertEqual(two.lines[0].debit_amount, Decimal("25000.50"))
        self.assertEqual(two.lines[1].credit_amount, Decimal("25000.50"))

    def test_proposal_contract_version_must_be_a_non_bool_int(self) -> None:
        """Bool and non-int contract versions fail closed before posting."""
        bool_version = self._billing_proposal(proposal_contract_version=True)
        with self.assertRaisesRegex(
            AccountingValidationError, "proposal_contract_version must be an integer"
        ):
            ingest_journal_proposal(bool_version)
        string_version = self._billing_proposal(proposal_contract_version="one")
        with self.assertRaisesRegex(
            AccountingValidationError, "proposal_contract_version must be an integer"
        ):
            ingest_journal_proposal(string_version)

    def test_billing_retained_earnings_role_is_rejected_at_ingest(self) -> None:
        """ADR 0024 close-only retained_earnings never enters from Billing ingest."""
        payload = self._billing_proposal()
        payload["lines"][1]["account_role_code"] = "retained_earnings"
        with self.assertRaisesRegex(
            AccountingValidationError, "reserved for AIS period-close"
        ):
            ingest_journal_proposal(payload)

    def test_billing_proposal_id_cannot_construct_a_reversal_key(self) -> None:
        """Billing proposal_id stays a UUID and cannot carry a :reversal suffix."""
        payload = self._billing_proposal(
            proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf612:reversal"
        )
        with self.assertRaisesRegex(
            AccountingValidationError, "proposal_id must be a UUID"
        ):
            ingest_journal_proposal(payload)

    def test_unapplied_cash_refund_ingests_published_billing_key(self) -> None:
        """Billing #59 refund JSON becomes a status-free proposal on the published key."""
        source_payload_hash = "sha256:" + "8" * 64
        refund_id = "019d7b92-8cc5-7a7f-b61c-962c0f4bf621"
        payload = self._billing_proposal(
            proposal_id=refund_id,
            idempotency_key=(
                f"urn:cwl:tenant_001:unapplied_cash_refund:{refund_id}"
                f":{source_payload_hash}:v1"
            ),
            source_payload_hash=source_payload_hash,
            source_event_references=(
                f"urn:cwl:tenant_001:unapplied_cash_refund:{refund_id}",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "unapplied_cash",
                    "debit_amount": "8000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "cash_receipt",
                    "debit_amount": "0",
                    "credit_amount": "8000",
                },
            ],
        )

        proposal = ingest_journal_proposal(payload)

        self.assertEqual(proposal.proposal_id, refund_id)
        self.assertEqual(
            proposal.idempotency_key,
            (
                f"urn:cwl:tenant_001:unapplied_cash_refund:{refund_id}"
                f":{source_payload_hash}:v1"
            ),
        )
        self.assertEqual(proposal.lines[0].account_role_code, "unapplied_cash")
        self.assertEqual(proposal.lines[0].debit_amount, Decimal("8000"))
        self.assertEqual(proposal.lines[1].account_role_code, "cash_receipt")
        self.assertEqual(proposal.lines[1].credit_amount, Decimal("8000"))
        self.assertFalse(hasattr(proposal, "proposal_status"))

    def test_unapplied_cash_park_ingests_published_billing_key(self) -> None:
        """Billing #60 park JSON uses the published unapplied_cash leftover key."""
        source_payload_hash = "sha256:" + "4" * 64
        unapplied_cash_id = "019d7b92-8cc5-7a7f-b61c-962c0f4bf622"
        payload = self._billing_proposal(
            proposal_id=unapplied_cash_id,
            idempotency_key=(
                f"urn:cwl:tenant_001:unapplied_cash:{unapplied_cash_id}"
                f":{source_payload_hash}:v1"
            ),
            source_payload_hash=source_payload_hash,
            source_event_references=(
                f"urn:cwl:tenant_001:unapplied_cash:{unapplied_cash_id}",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "cash_receipt",
                    "debit_amount": "3000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "unapplied_cash",
                    "debit_amount": "0",
                    "credit_amount": "3000",
                },
            ],
        )

        proposal = ingest_journal_proposal(payload)

        self.assertEqual(proposal.proposal_id, unapplied_cash_id)
        self.assertEqual(
            proposal.idempotency_key,
            (
                f"urn:cwl:tenant_001:unapplied_cash:{unapplied_cash_id}"
                f":{source_payload_hash}:v1"
            ),
        )
        self.assertEqual(proposal.lines[0].account_role_code, "cash_receipt")
        self.assertEqual(proposal.lines[0].debit_amount, Decimal("3000"))
        self.assertEqual(proposal.lines[1].account_role_code, "unapplied_cash")
        self.assertEqual(proposal.lines[1].credit_amount, Decimal("3000"))
        self.assertFalse(hasattr(proposal, "proposal_status"))

    def test_unapplied_cash_application_ingests_published_billing_key(self) -> None:
        """Billing #61 apply JSON uses the published unapplied_cash_application key."""
        source_payload_hash = "sha256:" + "d" * 64
        application_id = "019d7b92-8cc5-7a7f-b61c-962c0f4bf624"
        payload = self._billing_proposal(
            proposal_id=application_id,
            idempotency_key=(
                f"urn:cwl:tenant_001:unapplied_cash_application:{application_id}"
                f":{source_payload_hash}:v1"
            ),
            source_payload_hash=source_payload_hash,
            source_event_references=(
                f"urn:cwl:tenant_001:unapplied_cash_application:{application_id}",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "unapplied_cash",
                    "debit_amount": "7000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0",
                    "credit_amount": "7000",
                },
            ],
        )

        proposal = ingest_journal_proposal(payload)

        self.assertEqual(proposal.proposal_id, application_id)
        self.assertEqual(
            proposal.idempotency_key,
            (
                f"urn:cwl:tenant_001:unapplied_cash_application:{application_id}"
                f":{source_payload_hash}:v1"
            ),
        )
        self.assertEqual(proposal.lines[0].account_role_code, "unapplied_cash")
        self.assertEqual(proposal.lines[0].debit_amount, Decimal("7000"))
        self.assertEqual(proposal.lines[1].account_role_code, "accounts_receivable")
        self.assertEqual(proposal.lines[1].credit_amount, Decimal("7000"))
        self.assertFalse(hasattr(proposal, "proposal_status"))

    def _policy(self) -> AccountingPolicy:
        return AccountingPolicy(
            tenant_reference="urn:cwl:tenant_001",
            legal_entity_reference="urn:cwl:legal_entity:entity_001",
            accounting_book_reference="urn:cwl:accounting_book:primary_statutory",
            intended_book_role_code="primary_statutory",
            transaction_currency="KRW",
            functional_currency="KRW",
            open_period_start=date(2026, 8, 1),
            open_period_end=date(2026, 8, 31),
            chart_account_mapping={
                "accounts_receivable": "110100",
                "usage_revenue": "410100",
            },
            accounting_policy_version="ifrs-v1",
            posting_rule_version="billing-issued-v1",
        )

    def _billing_proposal(self, **overrides: object) -> dict[str, object]:
        source_payload_hash = "sha256:" + "a" * 64
        invoice_draft_id = "019d7b92-1aa0-7a7f-b61c-962c0f4bf612"
        values: dict[str, object] = {
            "proposal_id": invoice_draft_id,
            "proposal_contract_version": 1,
            "idempotency_key": (
                f"urn:cwl:tenant_001:invoice_draft:{invoice_draft_id}"
                f":{source_payload_hash}:v1"
            ),
            "tenant_reference": "urn:cwl:tenant_001",
            "legal_entity_reference": "urn:cwl:legal_entity:entity_001",
            "intended_book_role_code": "primary_statutory",
            "transaction_currency": "KRW",
            "transaction_date": "2026-08-31",
            "accounting_date": "2026-08-31",
            "source_payload_hash": source_payload_hash,
            "proposed_at": "2026-08-31T00:00:00Z",
            "proposal_status": "validated",
            "source_event_references": (
                f"urn:cwl:tenant_001:invoice_draft:{invoice_draft_id}",
            ),
            "lines": [
                {
                    "line_number": 1,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "25000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "0",
                    "credit_amount": "25000",
                },
            ],
        }
        values.update(overrides)
        return values


if __name__ == "__main__":
    unittest.main()
