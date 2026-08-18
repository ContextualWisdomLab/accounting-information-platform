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
