"""Real PostgreSQL RED for General Ledger accounting-book scope."""

from __future__ import annotations

from dataclasses import replace
import unittest
import urllib.parse

import psycopg

from tests import test_postgres_posting as posting


class PostgresAccountLedgerBookScopeRedTests(unittest.TestCase):
    """Prove one ledger read never mixes sibling accounting books."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_http_account_ledger_requires_book_reference(self) -> None:
        """A ledger inquiry must identify one accounting book before reading facts."""
        primary = self.case._two_line_proposal()
        self.case.ledger.post(primary, self.case.policy)

        server = self.case._start_http_server()
        try:
            query = urllib.parse.urlencode(
                {
                    "legal_entity_reference": self.case.policy.legal_entity_reference,
                    "chart_account_code": "110100",
                }
            )
            status, document = self.case._http_json(
                "GET", f"/account-ledgers?{query}", None
            )
        finally:
            server.shutdown()

        self.assertEqual(status, 400)
        self.assertIn("book_reference", str(document))

    def test_http_account_ledger_book_reference_excludes_sibling_book(self) -> None:
        """The requested book owns both ledger lines and full-scope totals."""
        primary = self.case._two_line_proposal()
        primary_receipt = self.case.ledger.post(primary, self.case.policy)
        management_book_reference = self._seed_management_book()
        management_policy = replace(
            self.case.policy,
            accounting_book_reference=management_book_reference,
            intended_book_role_code="management",
            accounting_policy_version="management-v1",
            posting_rule_version="management-v1",
        )
        management = replace(
            self.case._two_line_proposal(),
            proposal_id="019d7b92-3cc2-7a7f-b61c-962c0f4bf702",
            idempotency_key=(
                f"{self.case.policy.tenant_reference}:management-ledger-book-scope:v1"
            ),
            intended_book_role_code="management",
            source_payload_hash="sha256:" + "7" * 64,
            source_event_references=(
                f"{self.case.policy.tenant_reference}:management-ledger-book-scope",
            ),
        )
        self.case.ledger.post(management, management_policy)

        server = self.case._start_http_server()
        try:
            query = urllib.parse.urlencode(
                {
                    "legal_entity_reference": self.case.policy.legal_entity_reference,
                    "book_reference": self.case.policy.accounting_book_reference,
                    "chart_account_code": "110100",
                }
            )
            status, document = self.case._http_json(
                "GET", f"/account-ledgers?{query}", None
            )
        finally:
            server.shutdown()

        self.assertEqual(status, 200)
        self.assertEqual(document["period_debit_total"], "25000")
        self.assertEqual(document["period_credit_total"], "0")
        self.assertEqual(len(document["ledger_lines"]), 1)
        self.assertEqual(
            document["ledger_lines"][0]["journal_reference"],
            primary_receipt.journal_reference,
        )

    def _seed_management_book(self) -> str:
        """Create a sibling book that deliberately reuses the statutory account codes."""
        tenant_suffix = str(self.case.tenant_id).replace("-", "")[:8]
        book_reference = f"urn:cwl:accounting_book:management_{tenant_suffix}"
        with psycopg.connect(posting.DATABASE_URL) as connection:
            legal_entity_id = connection.execute(
                """
                SELECT legal_entity_id
                FROM accounting_core.legal_entity_record
                WHERE tenant_account_id = %s
                  AND legal_entity_code = %s
                  AND valid_to IS NULL
                """,
                (self.case.tenant_id, self.case.policy.legal_entity_reference),
            ).fetchone()[0]
            book_id = connection.execute(
                """
                INSERT INTO accounting_core.accounting_book (
                    tenant_account_id,
                    legal_entity_id,
                    book_role_code,
                    book_name,
                    reporting_currency_code,
                    valid_from
                ) VALUES (%s, %s, 'management', %s, 'KRW', %s)
                RETURNING accounting_book_id
                """,
                (
                    self.case.tenant_id,
                    legal_entity_id,
                    book_reference,
                    posting.VALID_FROM,
                ),
            ).fetchone()[0]
            for (
                chart_account_code,
                account_name,
                normal_balance_code,
                account_class_code,
                account_role_code,
            ) in (
                ("110100", "Management receivable", "debit", "asset", "accounts_receivable"),
                ("410100", "Management revenue", "credit", "revenue", "usage_revenue"),
            ):
                chart_account_id = connection.execute(
                    """
                    INSERT INTO accounting_core.chart_account (
                        tenant_account_id,
                        accounting_book_id,
                        chart_account_code,
                        account_name,
                        normal_balance_code,
                        valid_from,
                        account_class_code
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING chart_account_id
                    """,
                    (
                        self.case.tenant_id,
                        book_id,
                        chart_account_code,
                        account_name,
                        normal_balance_code,
                        posting.VALID_FROM,
                        account_class_code,
                    ),
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO accounting_core.account_role_mapping (
                        tenant_account_id,
                        accounting_book_id,
                        account_role_code,
                        chart_account_id,
                        accounting_policy_version,
                        posting_rule_version,
                        valid_from
                    ) VALUES (%s, %s, %s, %s, 'management-v1', 'management-v1', %s)
                    """,
                    (
                        self.case.tenant_id,
                        book_id,
                        account_role_code,
                        chart_account_id,
                        posting.VALID_FROM,
                    ),
                )
        return book_reference


if __name__ == "__main__":
    unittest.main()
