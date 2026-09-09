"""Real PostgreSQL RED for conflicting account-ledger book query aliases."""

from __future__ import annotations

import unittest
import urllib.parse

from tests import test_postgres_posting as posting


class HttpAccountLedgerBookAliasRedTests(unittest.TestCase):
    """Keep account-ledger HTTP admission bound to exactly one accounting-book identity."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL posting fixture for HTTP admission."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one isolated tenant/book case and preserve fixture cleanup."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_http_rejects_conflicting_book_reference_aliases(self) -> None:
        """Two different book spellings must fail before choosing either accounting scope."""
        self.case.ledger.post(self.case._two_line_proposal(), self.case.policy)
        server = self.case._start_http_server()
        try:
            query = urllib.parse.urlencode(
                {
                    "legal_entity_reference": self.case.policy.legal_entity_reference,
                    "book_reference": self.case.policy.accounting_book_reference,
                    "accounting_book_reference": "urn:cwl:accounting_book:conflicting_alias",
                    "chart_account_code": "110100",
                }
            )
            status, document = self.case._http_json(
                "GET", f"/account-ledgers?{query}", None
            )
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(status, 400)
        self.assertIn("book_reference", str(document))


if __name__ == "__main__":
    unittest.main()
