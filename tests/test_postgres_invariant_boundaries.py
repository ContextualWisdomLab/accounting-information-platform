"""Real PostgreSQL regressions for database-owned ledger and close authorization."""

from __future__ import annotations

import uuid
import unittest
from pathlib import Path

import psycopg
from psycopg import sql

from tests import test_postgres_posting as posting


ROOT = Path(__file__).resolve().parents[1]
PERIOD_GUARD_MIGRATION = ROOT / "database/migrations/0005_closed_period_guard.sql"


class PostgresInvariantBoundaryTests(unittest.TestCase):
    """Prove accounting invariants at the PostgreSQL commit and role boundaries."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_period_guard_migration_reasserts_nologin_and_replaces_constraint_triggers(self) -> None:
        """A pre-existing LOGIN closer is demoted and the guard migration is replayable."""
        migration = PERIOD_GUARD_MIGRATION.read_text(encoding="utf-8")
        with psycopg.connect(
            posting.DATABASE_URL,
            autocommit=True,
            cursor_factory=psycopg.ClientCursor,
        ) as connection:
            self.addCleanup(self._restore_closing_writer_nologin)
            connection.execute("ALTER ROLE accounting_closing_writer LOGIN")
            connection.execute(migration)
            role_can_login = connection.execute(
                "SELECT rolcanlogin FROM pg_roles WHERE rolname = 'accounting_closing_writer'"
            ).fetchone()[0]
            trigger_rows = connection.execute(
                """
                SELECT tgname
                FROM pg_trigger
                WHERE tgname IN (
                    'general_journal_balance_guard',
                    'journal_entry_balance_guard'
                )
                  AND NOT tgisinternal
                ORDER BY tgname
                """
            ).fetchall()

        self.assertFalse(role_can_login)
        self.assertEqual(
            [row[0] for row in trigger_rows],
            ["general_journal_balance_guard", "journal_entry_balance_guard"],
        )

    def test_unbalanced_and_empty_direct_journals_cannot_commit(self) -> None:
        """Deferred database triggers reject both one-sided and line-less journals."""
        for line_mode in ("one_sided", "empty"):
            with self.subTest(line_mode=line_mode):
                journal_reference = f"urn:cwl:accounting:general_journal:db_guard:{uuid.uuid4()}"
                connection = psycopg.connect(posting.DATABASE_URL)
                try:
                    journal_id = self._insert_direct_journal_header(
                        connection,
                        journal_reference,
                    )
                    if line_mode == "one_sided":
                        chart_account_id = connection.execute(
                            """
                            SELECT chart_account_id
                            FROM accounting_core.chart_account
                            WHERE tenant_account_id = %s
                              AND accounting_book_id = (
                                  SELECT accounting_book_id
                                  FROM accounting_core.general_journal
                                  WHERE tenant_account_id = %s
                                    AND general_journal_id = %s
                              )
                              AND chart_account_code = '110100'
                              AND valid_to IS NULL
                            """,
                            (self.case.tenant_id, self.case.tenant_id, journal_id),
                        ).fetchone()[0]
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
                            (self.case.tenant_id, journal_id, chart_account_id),
                        )
                    with self.assertRaises(psycopg.errors.CheckViolation):
                        connection.commit()
                    connection.rollback()
                finally:
                    connection.close()

                with psycopg.connect(posting.DATABASE_URL) as verification:
                    remaining = verification.execute(
                        """
                        SELECT count(*)
                        FROM accounting_core.general_journal
                        WHERE tenant_account_id = %s AND journal_reference = %s
                        """,
                        (self.case.tenant_id, journal_reference),
                    ).fetchone()[0]
                self.assertEqual(remaining, 0)

    def test_soft_close_bypass_requires_closing_writer_membership(self) -> None:
        """The GUC alone is denied; the purpose-limited database role admits the same insert."""
        plain_role = f"accounting_plain_{uuid.uuid4().hex[:10]}"
        closer_role = f"accounting_closer_{uuid.uuid4().hex[:10]}"
        password = f"Ais-{uuid.uuid4().hex}!"
        ids = self._soft_close_insert_ids()

        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            admin.execute(
                "UPDATE accounting_core.fiscal_period SET period_status_code = 'soft_closed' "
                "WHERE tenant_account_id = %s AND fiscal_period_id = %s",
                (self.case.tenant_id, ids[2]),
            )
            for role_name in (plain_role, closer_role):
                admin.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(role_name),
                        sql.Literal(password),
                    )
                )
                admin.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA accounting_core TO {}").format(
                        sql.Identifier(role_name)
                    )
                )
                admin.execute(
                    sql.SQL(
                        "GRANT SELECT ON accounting_core.fiscal_period TO {}"
                    ).format(sql.Identifier(role_name))
                )
                admin.execute(
                    sql.SQL(
                        "GRANT INSERT ON accounting_core.general_journal TO {}"
                    ).format(sql.Identifier(role_name))
                )
            admin.execute(
                sql.SQL("GRANT accounting_closing_writer TO {}").format(
                    sql.Identifier(closer_role)
                )
            )
            for role_name in (plain_role, closer_role):
                role_oid = admin.execute(
                    "SELECT oid FROM pg_catalog.pg_roles WHERE rolname = %s",
                    (role_name,),
                ).fetchone()[0]
                admin.execute(
                    """
                    INSERT INTO accounting_core.runtime_tenant_binding (
                        runtime_role_oid,
                        runtime_role_name,
                        tenant_account_id
                    ) VALUES (%s, %s, %s)
                    """,
                    (role_oid, role_name, self.case.tenant_id),
                )

        self.addCleanup(self._drop_test_roles, plain_role, closer_role)

        with psycopg.connect(
            posting.DATABASE_URL,
            user=plain_role,
            password=password,
        ) as connection:
            self._bind_soft_close_session(connection)
            with self.assertRaises(psycopg.errors.CheckViolation):
                self._insert_soft_close_header(connection, ids, "plain")
            connection.rollback()

        with psycopg.connect(
            posting.DATABASE_URL,
            user=closer_role,
            password=password,
        ) as connection:
            self._bind_soft_close_session(connection)
            self._insert_soft_close_header(connection, ids, "closer")
            connection.rollback()

    def _insert_direct_journal_header(
        self,
        connection: psycopg.Connection,
        journal_reference: str,
    ) -> object:
        """Insert a journal header inside the caller transaction and return its id."""
        connection.execute(
            "SELECT set_config('app.tenant_account_id', %s, false)",
            (self.case.tenant_id,),
        )
        legal_entity_id, book_id, period_id = connection.execute(
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
              AND fiscal_period.period_code = '2026-08'
            """,
            (self.case.tenant_id,),
        ).fetchone()
        proposal_record_id = connection.execute(
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
                f"db-guard:{uuid.uuid4()}",
                "sha256:" + "d" * 64,
            ),
        ).fetchone()[0]
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
                journal_reference,
                proposal_record_id,
            ),
        ).fetchone()[0]

    def _soft_close_insert_ids(self) -> tuple[object, object, object, object]:
        """Return legal-entity, book, period, and source-proposal ids for the fixture."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (self.case.tenant_id,),
            )
            legal_entity_id, book_id, period_id = connection.execute(
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
                  AND fiscal_period.period_code = '2026-08'
                """,
                (self.case.tenant_id,),
            ).fetchone()
            proposal_record_id = connection.execute(
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
                    f"soft-close-role:{uuid.uuid4()}",
                    "sha256:" + "e" * 64,
                ),
            ).fetchone()[0]
            connection.commit()
        return legal_entity_id, book_id, period_id, proposal_record_id

    def _bind_soft_close_session(self, connection: psycopg.Connection) -> None:
        """Set legacy tenant/classification GUCs while DB login binding remains authoritative."""
        connection.execute(
            "SELECT set_config('app.tenant_account_id', %s, false)",
            (self.case.tenant_id,),
        )
        connection.execute(
            "SELECT set_config('accounting_core.journal_write_role', 'adjusting', false)"
        )

    def _insert_soft_close_header(
        self,
        connection: psycopg.Connection,
        ids: tuple[object, object, object, object],
        suffix: str,
    ) -> None:
        """Attempt the same soft-close exception insert under one session role."""
        legal_entity_id, book_id, period_id, proposal_record_id = ids
        connection.execute(
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
            """,
            (
                self.case.tenant_id,
                legal_entity_id,
                book_id,
                period_id,
                f"urn:cwl:accounting:general_journal:soft_close:{suffix}:{uuid.uuid4()}",
                proposal_record_id,
            ),
        )

    @staticmethod
    def _restore_closing_writer_nologin() -> None:
        """Restore the closing capability to NOLOGIN even if migration replay fails."""
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            admin.execute("ALTER ROLE accounting_closing_writer NOLOGIN")

    @staticmethod
    def _drop_test_roles(plain_role: str, closer_role: str) -> None:
        """Remove purpose-limited test login roles even when an assertion fails."""
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            admin.execute(
                "DELETE FROM accounting_core.runtime_tenant_binding "
                "WHERE runtime_role_name = ANY(%s)",
                ([plain_role, closer_role],),
            )
            admin.execute(
                sql.SQL("REVOKE accounting_closing_writer FROM {}").format(
                    sql.Identifier(closer_role)
                )
            )
            for role_name in (closer_role, plain_role):
                admin.execute(
                    sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role_name))
                )
                admin.execute(
                    sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role_name))
                )


if __name__ == "__main__":
    unittest.main()
