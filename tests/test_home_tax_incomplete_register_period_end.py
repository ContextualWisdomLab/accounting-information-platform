"""PostgreSQL regression for incomplete HomeTax register receipt dating."""

from __future__ import annotations

import unittest
import uuid
from unittest import mock

from accounting_information_platform.persistence import PostgresPostingLedger
import tests.test_postgres_posting as postgres_posting


class HomeTaxIncompleteRegisterPeriodEndTests(unittest.TestCase):
    """Rejected HomeTax receipts use the resolved accounting cutoff, never date.min."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the checked-in foundation on the real PostgreSQL test database."""
        postgres_posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Seed an isolated tenant using the canonical PostgreSQL fixture."""
        self.case = postgres_posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_incomplete_register_receipt_uses_resolved_period_end_not_date_min(self) -> None:
        """A durable register_unavailable receipt is scoped to the fiscal period end."""
        server = self.case._start_http_server()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        command_key = f"urn:cwl:home_tax_submission:incomplete:{uuid.uuid4().hex}:v1"

        with mock.patch.object(
            PostgresPostingLedger,
            "load_vat_period_register",
            return_value={"tenant_reference": self.case.policy.tenant_reference},
        ):
            status, document = self.case._http_home_tax_submission(
                idempotency_key=command_key
            )

        listed_status, listed = self.case._http_home_tax_submissions()
        self.assertEqual(status, 422)
        self.assertEqual(document["rejection_reason_code"], "register_unavailable")
        self.assertEqual(listed_status, 200)
        self.assertEqual(len(listed["home_tax_submissions"]), 1)
        stored_register = listed["home_tax_submissions"][0]["vat_period_register"]
        expected_period_end = self.case.policy.open_period_end.isoformat()
        self.assertEqual(stored_register["as_of_date"], expected_period_end)
        self.assertNotEqual(stored_register["as_of_date"], "0001-01-01")
        self.assertEqual(stored_register["closing_amount"], "0")


if __name__ == "__main__":
    unittest.main()
