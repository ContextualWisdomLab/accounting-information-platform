"""HTTP status regressions for HomeTax request validation versus catalog lookup."""

from __future__ import annotations

import unittest
import uuid

from tests import test_postgres_posting as posting


class HomeTaxHttpStatusBoundaryTests(unittest.TestCase):
    """Keep malformed commands distinct from missing accounting catalog scope."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_missing_source_provenance_is_422_not_not_found(self) -> None:
        """Malformed HomeTax command evidence is a validation failure, not a 404 lookup miss."""
        server = self.case._start_http_server()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        payload = {
            "tenant_reference": self.case.policy.tenant_reference,
            "legal_entity_reference": self.case.policy.legal_entity_reference,
            "book_reference": self.case.policy.accounting_book_reference,
            "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
            "idempotency_key": f"urn:cwl:home_tax_submission:test:{uuid.uuid4().hex}:v1",
        }

        status, document = self.case._http_json("POST", "/home-tax-submissions", payload)

        self.assertEqual(status, 422)
        self.assertIn("source_payload_hash", str(document))
        self.assertEqual(
            self.case._count_table("accounting_integration.home_tax_submission"),
            0,
        )


if __name__ == "__main__":
    unittest.main()
