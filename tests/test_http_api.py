"""HTTP buyer proofs that stay outside the PostgreSQL posting suite module."""

from __future__ import annotations

import http.client
import json
import unittest
from decimal import Decimal

from accounting_information_platform import AccountingValidationError
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
