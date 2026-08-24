"""Real PostgreSQL RED regressions for accounting-book relational scope."""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

import psycopg

from tests import test_postgres_posting as posting


class PostgresBookScopeIntegrityTests(unittest.TestCase):
    """Require database-owned legal-entity and chart-account book scope."""

    @classmethod
    def setUpClass(cls) -> None:
        """Rebuild the PostgreSQL fixture with the current migration chain."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Seed one isolated accounting tenant for each scope regression."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_general_journal_rejects_book_from_different_legal_entity(self) -> None:
        """A journal cannot pair one legal entity with another entity's book."""
        connection = psycopg.connect(posting.DATABASE_URL)
        try:
            legal_entity_id, book_id, period_id = self._primary_scope(connection)
            mismatched_entity_id = connection.execute(
                """
                INSERT INTO accounting_core.legal_entity_record (
                    tenant_account_id,
                    legal_entity_code,
                    entity_name,
                    functional_currency_code,
                    valid_from
                )
                VALUES (%s, %s, 'Scope mismatch entity', 'KRW', %s)
                RETURNING legal_entity_id
                """,
                (
                    self.case.tenant_id,
                    f"scope_entity_{uuid.uuid4().hex[:12]}",
                    datetime(2026, 1, 1, tzinfo=timezone.utc),
                ),
            ).fetchone()[0]
            proposal_record_id = self._proposal_record(connection, "entity-book")

            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                self._insert_journal_header(
                    connection,
                    legal_entity_id=mismatched_entity_id,
                    book_id=book_id,
                    period_id=period_id,
                    proposal_record_id=proposal_record_id,
                    suffix="entity-book",
                )
        finally:
            connection.rollback()
            connection.close()

        self.assertIsNotNone(legal_entity_id)

    def test_journal_line_rejects_chart_account_from_different_book(self) -> None:
        """A journal line cannot borrow a chart account from another book."""
        connection = psycopg.connect(posting.DATABASE_URL)
        try:
            legal_entity_id, primary_book_id, period_id = self._primary_scope(connection)
            other_book_id = connection.execute(
                """
                INSERT INTO accounting_core.accounting_book (
                    tenant_account_id,
                    legal_entity_id,
                    book_role_code,
                    book_name,
                    reporting_currency_code,
                    valid_from
                )
                VALUES (%s, %s, %s, 'Scope mismatch book', 'KRW', %s)
                RETURNING accounting_book_id
                """,
                (
                    self.case.tenant_id,
                    legal_entity_id,
                    f"scope_book_{uuid.uuid4().hex[:10]}",
                    datetime(2026, 1, 1, tzinfo=timezone.utc),
                ),
            ).fetchone()[0]
            other_chart_account_id = connection.execute(
                """
                INSERT INTO accounting_core.chart_account (
                    tenant_account_id,
                    accounting_book_id,
                    chart_account_code,
                    account_name,
                    normal_balance_code,
                    valid_from,
                    account_class_code
                )
                VALUES (%s, %s, %s, 'Scope mismatch account', 'debit', %s, 'asset')
                RETURNING chart_account_id
                """,
                (
                    self.case.tenant_id,
                    other_book_id,
                    f"9{uuid.uuid4().hex[:11]}",
                    datetime(2026, 1, 1, tzinfo=timezone.utc),
                ),
            ).fetchone()[0]
            proposal_record_id = self._proposal_record(connection, "line-book")
            journal_id = self._insert_journal_header(
                connection,
                legal_entity_id=legal_entity_id,
                book_id=primary_book_id,
                period_id=period_id,
                proposal_record_id=proposal_record_id,
                suffix="line-book",
            )

            with self.assertRaises(psycopg.errors.CheckViolation):
                connection.execute(
                    """
                    INSERT INTO accounting_core.journal_entry_line (
                        tenant_account_id,
                        general_journal_id,
                        line_number,
                        chart_account_id,
                        account_role_code,
                        debit_amount,
                        credit_amount
                    )
                    VALUES (%s, %s, 1, %s, 'accounts_receivable', 1, 0)
                    """,
                    (self.case.tenant_id, journal_id, other_chart_account_id),
                )
        finally:
            connection.rollback()
            connection.close()

    def _primary_scope(
        self,
        connection: psycopg.Connection,
    ) -> tuple[object, object, object]:
        """Return the fixture legal entity, primary book, and August period ids."""
        row = connection.execute(
            """
            SELECT legal_entity_record.legal_entity_id,
                   accounting_book.accounting_book_id,
                   fiscal_period.fiscal_period_id
            FROM accounting_core.legal_entity_record
            JOIN accounting_core.accounting_book
              ON accounting_book.tenant_account_id = legal_entity_record.tenant_account_id
             AND accounting_book.legal_entity_id = legal_entity_record.legal_entity_id
            JOIN accounting_core.fiscal_period
              ON fiscal_period.tenant_account_id = legal_entity_record.tenant_account_id
            WHERE legal_entity_record.tenant_account_id = %s
              AND accounting_book.valid_to IS NULL
              AND fiscal_period.period_code = '2026-08'
            ORDER BY accounting_book.accounting_book_id, fiscal_period.fiscal_period_id
            LIMIT 1
            """,
            (self.case.tenant_id,),
        ).fetchone()
        self.assertIsNotNone(row)
        assert row is not None
        return row[0], row[1], row[2]

    def _proposal_record(
        self,
        connection: psycopg.Connection,
        suffix: str,
    ) -> object:
        """Create one source proposal record for a direct journal scope probe."""
        return connection.execute(
            """
            INSERT INTO accounting_integration.journal_proposal_record (
                tenant_account_id,
                external_proposal_id,
                proposal_contract_version,
                idempotency_key,
                source_payload_hash,
                proposal_status_code,
                processed_at
            )
            VALUES (%s, uuidv7(), 1, %s, %s, 'posted', clock_timestamp())
            RETURNING proposal_record_id
            """,
            (
                self.case.tenant_id,
                f"scope-probe:{suffix}:{uuid.uuid4()}",
                "sha256:" + "7" * 64,
            ),
        ).fetchone()[0]

    def _insert_journal_header(
        self,
        connection: psycopg.Connection,
        *,
        legal_entity_id: object,
        book_id: object,
        period_id: object,
        proposal_record_id: object,
        suffix: str,
    ) -> object:
        """Insert one journal header with caller-selected relational scope ids."""
        return connection.execute(
            """
            INSERT INTO accounting_core.general_journal (
                tenant_account_id,
                legal_entity_id,
                accounting_book_id,
                fiscal_period_id,
                journal_reference,
                journal_status_code,
                transaction_currency_code,
                functional_currency_code,
                transaction_date,
                accounting_date,
                source_proposal_record_id,
                accounting_policy_version,
                posting_rule_version
            )
            VALUES (%s, %s, %s, %s, %s, 'posted', 'KRW', 'KRW',
                    DATE '2026-08-31', DATE '2026-08-31', %s,
                    'ifrs-v1', 'billing-issued-v1')
            RETURNING general_journal_id
            """,
            (
                self.case.tenant_id,
                legal_entity_id,
                book_id,
                period_id,
                f"urn:cwl:accounting:general_journal:scope:{suffix}:{uuid.uuid4()}",
                proposal_record_id,
            ),
        ).fetchone()[0]


if __name__ == "__main__":
    unittest.main()
