"""HTTP buyer proofs that stay outside the PostgreSQL posting suite module."""

from __future__ import annotations

import http.client
import json
import unittest
from decimal import Decimal

from accounting_information_platform import AccountingValidationError
from accounting_information_platform.billing_pull import accept_billing_proposal_pull
from accounting_information_platform.accept import (
    accept_period_close,
    lookup_account_ledger,
    lookup_audit_events,
    lookup_journal_reversals,
    lookup_outbox_events,
    lookup_period_closes,
)

import tests.test_postgres_posting as postgres_posting


class CollectionWriteOffAgingHttpTests(unittest.TestCase):
    """GET /receivable-agings drops by a posted Billing #51 write-off."""

    @classmethod
    def setUpClass(cls) -> None:
        postgres_posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.case = postgres_posting.PostgresPostingTests("setUp")
        self.case.setUp()

    def test_http_collection_write_off_reduces_receivable_aging(self) -> None:
        """Invoice AR then a collection write-off lowers entity aging by that amount."""
        case = self.case
        invoice = case._billing_validated_payload()
        write_off = case._billing_write_off_payload()
        later_write_off = case._billing_write_off_payload(
            proposal_id="019d7b92-5ee4-7a7f-b61c-962c0f4bf618",
            idempotency_key=(
                f"{case.policy.tenant_reference}:collection_write_off:"
                f"019d7b92-5ee4-7a7f-b61c-962c0f4bf618:sha256:{'6' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "6" * 64,
            source_event_references=(
                f"{case.policy.tenant_reference}:collection_write_off:"
                "019d7b92-5ee4-7a7f-b61c-962c0f4bf618",
            ),
        )
        server = case._start_http_server()

        invoice_status, _invoice = case._http_json("POST", "/journal-proposals", invoice)
        before_status, before = case._http_receivable_aging()
        write_off_status, _receipt = case._http_json("POST", "/journal-proposals", write_off)
        after_status, after = case._http_receivable_aging()
        balances_status, balances = case._http_account_balances(chart_account_code="110100")
        mapping_status, mappings = case._http_account_role_mappings()
        billing_status, billing_list = case._http_period_journals(journal_source_code="billing")
        mapping_by_role = {
            str(item["account_role_code"]): item for item in mappings["mappings"]
        }
        receivable_net = case._account_balance_net(balances, "110100")

        self.assertEqual(invoice_status, 200)
        self.assertEqual(before_status, 200)
        self.assertEqual(before["current_amount"], "25000")
        self.assertEqual(before["days_31_60_amount"], "0")
        self.assertEqual(before["days_61_90_amount"], "0")
        self.assertEqual(before["days_over_90_amount"], "0")
        self.assertEqual(before["total_outstanding_amount"], "25000")
        self.assertNotIn("unapplied_credit_amount", before)
        self.assertEqual(write_off_status, 200)
        self.assertEqual(after_status, 200)
        self.assertEqual(balances_status, 200)
        self.assertEqual(after["current_amount"], "18000")
        self.assertEqual(after["days_31_60_amount"], "0")
        self.assertEqual(after["days_61_90_amount"], "0")
        self.assertEqual(after["days_over_90_amount"], "0")
        self.assertEqual(after["total_outstanding_amount"], "18000")
        self.assertEqual(
            Decimal(str(after["total_outstanding_amount"])),
            Decimal(str(before["total_outstanding_amount"])) - Decimal("7000"),
        )
        self.assertEqual(Decimal(str(after["total_outstanding_amount"])), receivable_net)
        self.assertNotIn("unapplied_credit_amount", after)
        self.assertNotIn("party_reference", after)
        self.assertEqual(mapping_status, 200)
        self.assertEqual(mapping_by_role["write_off_expense"]["chart_account_code"], "510100")
        self.assertEqual(billing_status, 200)
        self.assertIn(
            write_off["idempotency_key"],
            [item["idempotency_key"] for item in billing_list["journals"]],
        )

        soft_status, _soft = case._http_json(
            "POST",
            "/period-closes",
            case._period_close_payload(period_status_code="soft_closed"),
        )
        rejected_status, rejected = case._http_json(
            "POST", "/journal-proposals", later_write_off
        )
        hard_status, _hard = case._http_json("POST", "/period-closes", case._period_close_payload())
        closed_status, closed = case._http_receivable_aging()
        closed_balances_status, closed_balances = case._http_account_balances(
            chart_account_code="110100"
        )

        self.assertEqual(soft_status, 200)
        self.assertEqual(rejected_status, 422)
        self.assertIn("open period", str(rejected["error_message"]))
        self.assertEqual(hard_status, 200)
        self.assertEqual(closed_status, 200)
        self.assertEqual(closed_balances_status, 200)
        self.assertEqual(closed["total_outstanding_amount"], "18000")
        self.assertEqual(
            Decimal(str(closed["total_outstanding_amount"])),
            case._account_balance_net(closed_balances, "110100"),
        )
        server.shutdown()
        server.server_close()


class UnappliedCashRefundHttpTests(unittest.TestCase):
    """POST /journal-proposals accepts Billing #59 unapplied_cash against 210200."""

    @classmethod
    def setUpClass(cls) -> None:
        postgres_posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.case = postgres_posting.PostgresPostingTests("setUp")
        self.case.setUp()

    def test_http_unapplied_cash_refund_posts_and_stays_off_payable_aging(self) -> None:
        """Refund and park post on the catalog; 210200 is not a payable-aging account."""
        case = self.case
        refund = case._billing_unapplied_cash_refund_payload()
        park = case._billing_unapplied_cash_park_payload()
        later_refund = case._billing_unapplied_cash_refund_payload(
            proposal_id="019d7b92-8cc5-7a7f-b61c-962c0f4bf623",
            idempotency_key=(
                f"{case.policy.tenant_reference}:unapplied_cash_refund:"
                f"019d7b92-8cc5-7a7f-b61c-962c0f4bf623:sha256:{'2' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "2" * 64,
            source_event_references=(
                f"{case.policy.tenant_reference}:unapplied_cash_refund:"
                "019d7b92-8cc5-7a7f-b61c-962c0f4bf623",
            ),
        )
        server = case._start_http_server()

        mapping_status, mappings = case._http_account_role_mappings()
        refund_status, receipt = case._http_json("POST", "/journal-proposals", refund)
        replay_status, replay = case._http_json("POST", "/journal-proposals", refund)
        park_status, park_receipt = case._http_json("POST", "/journal-proposals", park)
        park_replay_status, park_replay = case._http_json("POST", "/journal-proposals", park)
        journal_status, journal = case._http_journal(
            idempotency_key=str(refund["idempotency_key"])
        )
        payable_status, payable = case._http_payable_aging()
        clearing_payable = case._http_payable_aging(chart_account_code="210200")
        mapping_by_role = {
            str(item["account_role_code"]): item for item in mappings["mappings"]
        }

        self.assertEqual(mapping_status, 200)
        self.assertIn("unapplied_cash", mapping_by_role)
        self.assertEqual(mapping_by_role["unapplied_cash"]["chart_account_code"], "210200")
        self.assertEqual(refund_status, 200)
        self.assertEqual(journal_status, 200)
        by_code = {str(item["chart_account_code"]): item for item in journal["lines"]}
        self.assertEqual(replay_status, 200)
        self.assertEqual(receipt, replay)
        self.assertEqual(
            park["idempotency_key"],
            (
                f"{case.policy.tenant_reference}:unapplied_cash:"
                f"{park['proposal_id']}:{park['source_payload_hash']}:v1"
            ),
        )
        self.assertEqual(park_status, 200)
        self.assertEqual(park_replay_status, 200)
        self.assertEqual(park_receipt, park_replay)
        self.assertEqual(park_receipt["idempotency_key"], park["idempotency_key"])
        self.assertNotEqual(park_receipt["idempotency_key"], receipt["idempotency_key"])
        self.assertEqual(journal_status, 200)
        self.assertEqual(set(by_code), {"210200", "110200"})
        self.assertEqual(Decimal(str(by_code["210200"]["debit_amount"])), Decimal("8000"))
        self.assertEqual(payable_status, 200)
        self.assertEqual(payable["chart_account_code"], "210100")
        self.assertEqual(clearing_payable[0], 422)

        soft_status, _soft = case._http_json(
            "POST",
            "/period-closes",
            case._period_close_payload(period_status_code="soft_closed"),
        )
        rejected_status, rejected = case._http_json(
            "POST", "/journal-proposals", later_refund
        )
        hard_status, _hard = case._http_json(
            "POST", "/period-closes", case._period_close_payload()
        )
        closed_balances_status, closed_balances = case._http_account_balances(
            chart_account_code="210200"
        )

        self.assertEqual(soft_status, 200)
        self.assertEqual(rejected_status, 422)
        self.assertIn("open period", str(rejected["error_message"]))
        self.assertEqual(hard_status, 200)
        self.assertEqual(closed_balances_status, 200)
        self.assertEqual(case._account_balance_net(closed_balances, "210200"), Decimal("5000"))
        self.assertEqual(case._count_closing_journals(), 0)
        server.shutdown()
        server.server_close()

    def test_http_unapplied_cash_apply_reduces_receivable_aging(self) -> None:
        """Park then #61 apply drops entity AR aging by the applied amount."""
        case = self.case
        invoice = case._billing_validated_payload()
        park = case._billing_unapplied_cash_park_payload(
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "cash_receipt",
                    "debit_amount": "7000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "unapplied_cash",
                    "debit_amount": "0",
                    "credit_amount": "7000",
                },
            ],
        )
        apply_payload = case._billing_unapplied_cash_application_payload()
        later_apply = case._billing_unapplied_cash_application_payload(
            proposal_id="019d7b92-8cc5-7a7f-b61c-962c0f4bf625",
            idempotency_key=(
                f"{case.policy.tenant_reference}:unapplied_cash_application:"
                f"019d7b92-8cc5-7a7f-b61c-962c0f4bf625:sha256:{'e' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "e" * 64,
            source_event_references=(
                f"{case.policy.tenant_reference}:unapplied_cash_application:"
                "019d7b92-8cc5-7a7f-b61c-962c0f4bf625",
            ),
        )
        server = case._start_http_server()

        invoice_status, _invoice = case._http_json("POST", "/journal-proposals", invoice)
        park_status, _park = case._http_json("POST", "/journal-proposals", park)
        before_status, before = case._http_receivable_aging()
        apply_status, receipt = case._http_json("POST", "/journal-proposals", apply_payload)
        replay_status, replay = case._http_json("POST", "/journal-proposals", apply_payload)
        after_status, after = case._http_receivable_aging()
        balances_status, balances = case._http_account_balances(chart_account_code="110100")
        payable_status, payable = case._http_payable_aging()

        self.assertEqual(
            apply_payload["idempotency_key"],
            (
                f"{case.policy.tenant_reference}:unapplied_cash_application:"
                f"{apply_payload['proposal_id']}:{apply_payload['source_payload_hash']}:v1"
            ),
        )
        self.assertEqual(apply_payload["lines"][0]["account_role_code"], "unapplied_cash")
        self.assertEqual(apply_payload["lines"][1]["account_role_code"], "accounts_receivable")
        self.assertEqual(invoice_status, 200)
        self.assertEqual(park_status, 200)
        self.assertEqual(before_status, 200)
        self.assertEqual(before["total_outstanding_amount"], "25000")
        self.assertNotIn("unapplied_credit_amount", before)
        self.assertEqual(apply_status, 200)
        self.assertEqual(replay_status, 200)
        self.assertEqual(receipt, replay)
        self.assertEqual(after_status, 200)
        self.assertEqual(balances_status, 200)
        self.assertEqual(after["current_amount"], "18000")
        self.assertEqual(after["days_31_60_amount"], "0")
        self.assertEqual(after["days_61_90_amount"], "0")
        self.assertEqual(after["days_over_90_amount"], "0")
        self.assertEqual(after["total_outstanding_amount"], "18000")
        self.assertEqual(
            Decimal(str(after["total_outstanding_amount"])),
            Decimal(str(before["total_outstanding_amount"])) - Decimal("7000"),
        )
        self.assertEqual(
            Decimal(str(after["total_outstanding_amount"])),
            case._account_balance_net(balances, "110100"),
        )
        self.assertNotIn("unapplied_credit_amount", after)
        self.assertNotIn("party_reference", after)
        self.assertEqual(payable_status, 200)
        self.assertEqual(payable["chart_account_code"], "210100")

        soft_status, _soft = case._http_json(
            "POST",
            "/period-closes",
            case._period_close_payload(period_status_code="soft_closed"),
        )
        rejected_status, rejected = case._http_json(
            "POST", "/journal-proposals", later_apply
        )
        hard_status, _hard = case._http_json(
            "POST", "/period-closes", case._period_close_payload()
        )
        closed_status, closed = case._http_receivable_aging()

        self.assertEqual(soft_status, 200)
        self.assertEqual(rejected_status, 422)
        self.assertIn("open period", str(rejected["error_message"]))
        self.assertEqual(hard_status, 200)
        self.assertEqual(closed_status, 200)
        self.assertEqual(closed["total_outstanding_amount"], "18000")
        server.shutdown()
        server.server_close()


class PeriodClosePackageHttpTests(unittest.TestCase):
    """GET /period-close-packages includes the standalone payable-aging document."""

    @classmethod
    def setUpClass(cls) -> None:
        postgres_posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.case = postgres_posting.PostgresPostingTests("setUp")
        self.case.setUp()

    def test_http_period_close_package_includes_payable_aging(self) -> None:
        """Package payable_aging matches GET /payable-agings on the same entity period."""
        case = self.case
        server = case._start_http_server()
        empty_status, empty_package = case._http_period_close_package()
        empty_payable_status, empty_payable = case._http_payable_aging()

        self.assertEqual(empty_status, 200)
        self.assertEqual(empty_payable_status, 200)
        self.assertEqual(empty_package["payable_aging"], empty_payable)
        self.assertEqual(empty_package["payable_aging"]["total_outstanding_amount"], "0")
        self.assertEqual(
            empty_package["payable_aging"]["as_of_date"],
            empty_package["receivable_aging"]["as_of_date"],
        )

        taxed_status, _taxed = case._http_json(
            "POST", "/journal-proposals", case._billing_taxed_payload()
        )
        open_status, opened = case._http_period_close_package()
        payable_status, payable_aging = case._http_payable_aging()
        receivable_status, receivable_aging = case._http_receivable_aging()

        self.assertEqual(taxed_status, 200)
        self.assertEqual(open_status, 200)
        self.assertEqual(payable_status, 200)
        self.assertEqual(receivable_status, 200)
        self.assertEqual(opened["payable_aging"], payable_aging)
        self.assertEqual(opened["receivable_aging"], receivable_aging)
        self.assertEqual(opened["payable_aging"]["chart_account_code"], "210100")
        self.assertEqual(opened["payable_aging"]["total_outstanding_amount"], "2500")
        self.assertEqual(opened["payable_aging"]["current_amount"], "2500")
        self.assertEqual(opened["payable_aging"]["as_of_date"], opened["receivable_aging"]["as_of_date"])
        self.assertEqual(
            set(opened),
            {
                "tenant_reference",
                "legal_entity_reference",
                "accounting_book_reference",
                "book_reference",
                "fiscal_period_reference",
                "fiscal_period",
                "trial_balance",
                "financial_statement_package",
                "receivable_aging",
                "payable_aging",
                "period_close",
            },
        )

        soft_status, _soft = case._http_json(
            "POST",
            "/period-closes",
            case._period_close_payload(period_status_code="soft_closed"),
        )
        soft_status_code, soft_package = case._http_period_close_package()
        self.assertEqual(soft_status, 200)
        self.assertEqual(soft_status_code, 200)
        self.assertEqual(soft_package["payable_aging"], case._http_payable_aging()[1])
        self.assertIsNone(soft_package["period_close"])

        hard_status, _hard = case._http_json("POST", "/period-closes", case._period_close_payload())
        hard_status_code, hard_package = case._http_period_close_package()
        self.assertEqual(hard_status, 200)
        self.assertEqual(hard_status_code, 200)
        self.assertEqual(hard_package["payable_aging"], case._http_payable_aging()[1])
        self.assertEqual(
            hard_package["payable_aging"]["as_of_date"],
            hard_package["receivable_aging"]["as_of_date"],
        )
        self.assertEqual(hard_package["payable_aging"]["total_outstanding_amount"], "2500")
        self.assertIsNotNone(hard_package["period_close"])

        cross_status, _cross = case._http_period_close_package(
            tenant_header="urn:cwl:tenant_other"
        )
        self.assertEqual(cross_status, 403)
        server.shutdown()
        server.server_close()


class BillingIngestFailClosedHttpTests(unittest.TestCase):
    """Bad Billing proposals and HTTP accept stay 4xx and write zero journals."""

    @classmethod
    def setUpClass(cls) -> None:
        postgres_posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.case = postgres_posting.PostgresPostingTests("setUp")
        self.case.setUp()

    def test_http_rejects_malformed_billing_lines_and_posts_query_string(self) -> None:
        """Missing keys, JSON-number amounts, and reserved RE are 422; ?trace=1 posts."""
        case = self.case
        server = case._start_http_server()
        journals_before = case._count_table("accounting_core.general_journal")
        missing = case._billing_validated_payload()
        del missing["lines"][0]["line_number"]
        float_amount = case._billing_validated_payload()
        float_amount["lines"][0]["debit_amount"] = 25000.5
        float_amount["lines"][1]["credit_amount"] = 25000.5
        integer_amount = case._billing_validated_payload()
        integer_amount["lines"][0]["debit_amount"] = 25000
        integer_amount["lines"][1]["credit_amount"] = 25000
        bool_line = case._billing_validated_payload()
        bool_line["lines"][0]["line_number"] = True
        bool_version = case._billing_validated_payload(proposal_contract_version=True)
        string_version = case._billing_validated_payload(proposal_contract_version="one")
        retained = case._billing_validated_payload()
        retained["lines"][1]["account_role_code"] = "retained_earnings"

        missing_status, missing_body = case._http_json("POST", "/journal-proposals", missing)
        float_status, float_body = case._http_json("POST", "/journal-proposals", float_amount)
        integer_status, integer_body = case._http_json(
            "POST", "/journal-proposals", integer_amount
        )
        bool_line_status, bool_line_body = case._http_json(
            "POST", "/journal-proposals", bool_line
        )
        bool_version_status, bool_version_body = case._http_json(
            "POST", "/journal-proposals", bool_version
        )
        string_version_status, string_version_body = case._http_json(
            "POST", "/journal-proposals", string_version
        )
        retained_status, retained_body = case._http_json(
            "POST", "/journal-proposals", retained
        )
        posted_status, posted = case._http_json(
            "POST",
            "/journal-proposals?trace=1",
            case._billing_validated_payload(),
        )

        self.assertEqual(missing_status, 422)
        self.assertIn("line_number is required", str(missing_body["error_message"]))
        self.assertIn("then retry", str(missing_body["error_message"]))
        self.assertEqual(float_status, 422)
        self.assertIn("canonical decimal string", str(float_body["error_message"]))
        self.assertEqual(integer_status, 422)
        self.assertIn("canonical decimal string", str(integer_body["error_message"]))
        self.assertEqual(bool_line_status, 422)
        self.assertIn("line_number must be an integer", str(bool_line_body["error_message"]))
        self.assertEqual(bool_version_status, 422)
        self.assertIn(
            "proposal_contract_version must be an integer",
            str(bool_version_body["error_message"]),
        )
        self.assertEqual(string_version_status, 422)
        self.assertIn(
            "proposal_contract_version must be an integer",
            str(string_version_body["error_message"]),
        )
        self.assertEqual(retained_status, 422)
        self.assertIn("reserved for AIS period-close", str(retained_body["error_message"]))
        self.assertEqual(posted_status, 200)
        self.assertEqual(posted["posting_status_code"], "posted")
        self.assertEqual(case._count_table("accounting_core.general_journal"), journals_before + 1)
        server.shutdown()
        server.server_close()

    def test_http_rejects_empty_period_status_and_naive_cursors_and_oversize(self) -> None:
        """Empty close status is 422, naive list cursors are 422, oversize is 413."""
        case = self.case
        server = case._start_http_server()
        journals_before = case._count_table("accounting_core.general_journal")
        empty_close = case._period_close_payload(period_status_code="")
        null_close = case._period_close_payload(period_status_code=None)
        naive_cursor = "2026-08-31T00:00:00|urn:cwl:accounting:general_journal:x"
        naive_ledger = "2026-08-31T00:00:00|urn:cwl:accounting:general_journal:x|1"
        naive_outbox = "2026-08-31T00:00:00|01900000-0000-7000-8000-000000000001"

        empty_status, empty_body = case._http_json("POST", "/period-closes", empty_close)
        null_status, null_body = case._http_json("POST", "/period-closes", null_close)
        open_status, opened = case._http_fiscal_period()
        ledger_status, ledger_body = case._http_account_ledger("110100", cursor=naive_ledger)
        reversal_status, reversal_body = case._http_journal_reversals(cursor=naive_cursor)
        close_list_status, close_list_body = case._http_period_closes(
            cursor=f"{naive_outbox}"
        )
        outbox_status, outbox_body = case._http_outbox_events(
            "posting_receipt", cursor=naive_outbox
        )
        audit_status, audit_body = case._http_audit_events(cursor=naive_outbox)
        oversize_status, oversize_body = self._http_oversize(case)

        with self.assertRaisesRegex(AccountingValidationError, "period_status_code"):
            accept_period_close(empty_close, postgres_posting.DATABASE_URL, case.policy.tenant_reference)
        with self.assertRaisesRegex(AccountingValidationError, "UTC offset"):
            lookup_account_ledger(
                postgres_posting.DATABASE_URL,
                case.policy.tenant_reference,
                case.policy.legal_entity_reference,
                "110100",
                cursor=naive_ledger,
            )
        with self.assertRaisesRegex(AccountingValidationError, "UTC offset"):
            lookup_journal_reversals(
                postgres_posting.DATABASE_URL,
                case.policy.tenant_reference,
                case.policy.legal_entity_reference,
                cursor=naive_cursor,
            )
        with self.assertRaisesRegex(AccountingValidationError, "UTC offset"):
            lookup_period_closes(
                postgres_posting.DATABASE_URL,
                case.policy.tenant_reference,
                case.policy.legal_entity_reference,
                cursor=naive_outbox,
            )
        with self.assertRaisesRegex(AccountingValidationError, "UTC offset"):
            lookup_outbox_events(
                postgres_posting.DATABASE_URL,
                case.policy.tenant_reference,
                "posting_receipt",
                cursor=naive_outbox,
            )
        with self.assertRaisesRegex(AccountingValidationError, "UTC offset"):
            lookup_audit_events(
                postgres_posting.DATABASE_URL,
                case.policy.tenant_reference,
                cursor=naive_outbox,
            )

        self.assertEqual(empty_status, 422)
        self.assertIn("period_status_code", str(empty_body["error_message"]))
        self.assertIn("then retry", str(empty_body["error_message"]))
        self.assertEqual(null_status, 422)
        self.assertIn("period_status_code", str(null_body["error_message"]))
        self.assertEqual(open_status, 200)
        self.assertEqual(opened["period_status_code"], "open")
        self.assertEqual(ledger_status, 422)
        self.assertIn("UTC offset", str(ledger_body["error_message"]))
        self.assertEqual(reversal_status, 422)
        self.assertIn("UTC offset", str(reversal_body["error_message"]))
        self.assertEqual(close_list_status, 422)
        self.assertIn("UTC offset", str(close_list_body["error_message"]))
        self.assertEqual(outbox_status, 422)
        self.assertIn("UTC offset", str(outbox_body["error_message"]))
        self.assertEqual(audit_status, 422)
        self.assertIn("UTC offset", str(audit_body["error_message"]))
        self.assertEqual(oversize_status, 413)
        self.assertIn("1 MiB", str(oversize_body["error_message"]))
        self.assertIn("then retry", str(oversize_body["error_message"]))
        self.assertEqual(case._count_table("accounting_core.general_journal"), journals_before)
        server.shutdown()
        server.server_close()

    def _http_oversize(
        self, case: postgres_posting.PostgresPostingTests
    ) -> tuple[int, dict[str, object]]:
        connection = http.client.HTTPConnection("127.0.0.1", case._http_port())
        try:
            connection.putrequest("POST", "/journal-proposals")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("X-CWL-Tenant-Reference", case.policy.tenant_reference)
            connection.putheader("Content-Length", str(1024 * 1024 + 1))
            connection.endheaders()
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()


class BillingPullRejectedProposalHttpTests(unittest.TestCase):
    """POST /billing-proposal-pulls keeps rejected Billing rows and fails stuck pages."""

    @classmethod
    def setUpClass(cls) -> None:
        postgres_posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.case = postgres_posting.PostgresPostingTests("setUp")
        self.case.setUp()

    def test_http_pull_returns_receipt_and_rejected_unknown_role(self) -> None:
        """One valid plus one unknown-role page posts one journal and lists the reject."""
        case = self.case
        invoice = case._billing_validated_payload()
        unknown = case._billing_validated_payload(
            proposal_id="019d7b92-6ff5-7a7f-b61c-962c0f4bf619",
            source_payload_hash="sha256:" + "7" * 64,
            idempotency_key=(
                f"{case.policy.tenant_reference}:invoice_draft:"
                f"019d7b92-6ff5-7a7f-b61c-962c0f4bf619:sha256:{'7' * 64}:v1"
            ),
            proposed_at="2026-08-31T12:00:00Z",
            source_event_references=(
                f"{case.policy.tenant_reference}:invoice_draft:"
                "019d7b92-6ff5-7a7f-b61c-962c0f4bf619",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "25000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "contract_liability",
                    "debit_amount": "0",
                    "credit_amount": "25000",
                },
            ],
        )
        billing_url = case._start_fake_billing([invoice, unknown])
        server = case._start_http_server()
        journals_before = case._count_table("accounting_core.general_journal")

        status, body = case._http_json(
            "POST",
            "/billing-proposal-pulls",
            {"tenant_reference": case.policy.tenant_reference, "billing_base_url": billing_url},
        )
        replay = accept_billing_proposal_pull(
            {"tenant_reference": case.policy.tenant_reference, "billing_base_url": billing_url},
            postgres_posting.DATABASE_URL,
            case.policy.tenant_reference,
        )

        self.assertEqual(status, 200)
        self.assertEqual(set(body), {"posting_receipts", "rejected_proposals"})
        self.assertEqual(len(body["posting_receipts"]), 1)
        self.assertEqual(
            body["posting_receipts"][0]["idempotency_key"], invoice["idempotency_key"]
        )
        self.assertEqual(len(body["rejected_proposals"]), 1)
        rejected = body["rejected_proposals"][0]
        self.assertEqual(
            set(rejected),
            {
                "proposal_id",
                "idempotency_key",
                "rejection_reason_code",
                "rejection_message",
            },
        )
        self.assertEqual(rejected["proposal_id"], unknown["proposal_id"])
        self.assertEqual(rejected["idempotency_key"], unknown["idempotency_key"])
        self.assertEqual(rejected["rejection_reason_code"], "unknown_account_role")
        self.assertIn("then retry", str(rejected["rejection_message"]))
        self.assertEqual(replay["posting_receipts"], body["posting_receipts"])
        self.assertEqual(replay["rejected_proposals"], body["rejected_proposals"])
        self.assertEqual(case._count_table("accounting_core.general_journal"), journals_before + 1)
        server.shutdown()
        server.server_close()

    def test_http_pull_empty_page_includes_rejected_proposals(self) -> None:
        """An empty Billing page still returns rejected_proposals []."""
        case = self.case
        billing_url = case._start_fake_billing([])
        server = case._start_http_server()

        status, body = case._http_json(
            "POST",
            "/billing-proposal-pulls",
            {"tenant_reference": case.policy.tenant_reference, "billing_base_url": billing_url},
        )

        self.assertEqual(status, 200)
        self.assertEqual(body["posting_receipts"], [])
        self.assertEqual(body["rejected_proposals"], [])
        self.assertEqual(set(body), {"posting_receipts", "rejected_proposals"})
        server.shutdown()
        server.server_close()

    def test_http_pull_stuck_cursor_and_page_cap_are_422(self) -> None:
        """A repeating next_cursor or a 21-page list stops the HTTP pull thread."""
        case = self.case
        server = case._start_http_server()
        stuck_url = case._start_fake_billing(
            [],
            list_raw=json.dumps(
                {
                    "journal_proposals": [],
                    "next_cursor": "2026-08-31T00:00:00Z|stuck",
                }
            ).encode("utf-8"),
        )
        long_items = [
            case._billing_validated_payload(
                proposal_id=f"019d7b92-7aa0-7a7f-b61c-962c0f4bf6{index:02d}",
                source_payload_hash="sha256:" + f"{index:064d}",
                idempotency_key=(
                    f"{case.policy.tenant_reference}:invoice_draft:"
                    f"019d7b92-7aa0-7a7f-b61c-962c0f4bf6{index:02d}:"
                    f"sha256:{index:064d}:v1"
                ),
                proposed_at=f"2026-08-31T00:{index:02d}:00Z",
                source_event_references=(
                    f"{case.policy.tenant_reference}:invoice_draft:"
                    f"019d7b92-7aa0-7a7f-b61c-962c0f4bf6{index:02d}",
                ),
                lines=[
                    {
                        "line_number": 1,
                        "account_role_code": "accounts_receivable",
                        "debit_amount": "25000",
                        "credit_amount": "0",
                    },
                    {
                        "line_number": 2,
                        "account_role_code": "contract_liability",
                        "debit_amount": "0",
                        "credit_amount": "25000",
                    },
                ],
            )
            for index in range(21)
        ]
        long_url = case._start_fake_billing(long_items)
        journals_before = case._count_table("accounting_core.general_journal")

        stuck_status, stuck_body = case._http_json(
            "POST",
            "/billing-proposal-pulls",
            {"tenant_reference": case.policy.tenant_reference, "billing_base_url": stuck_url},
        )
        cap_status, cap_body = case._http_json(
            "POST",
            "/billing-proposal-pulls",
            {
                "tenant_reference": case.policy.tenant_reference,
                "billing_base_url": long_url,
                "page_limit": 1,
            },
        )

        self.assertEqual(stuck_status, 422)
        self.assertIn("list cursor", str(stuck_body["error_message"]))
        self.assertIn("then retry", str(stuck_body["error_message"]))
        self.assertEqual(cap_status, 422)
        self.assertIn("20 pages", str(cap_body["error_message"]))
        self.assertIn("then retry", str(cap_body["error_message"]))
        self.assertEqual(case._count_table("accounting_core.general_journal"), journals_before)
        server.shutdown()
        server.server_close()
