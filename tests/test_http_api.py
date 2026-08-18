"""HTTP buyer proofs that stay outside the PostgreSQL posting suite module."""

from __future__ import annotations

import unittest
from decimal import Decimal

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
