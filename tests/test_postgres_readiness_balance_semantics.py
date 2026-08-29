"""Real PostgreSQL proof that readiness detects loss of journal-balance semantics."""

from __future__ import annotations

import unittest
import uuid

import psycopg

from accounting_information_platform import AccountingValidationError, PostgresPostingLedger
from tests import test_postgres_posting as posting
from tests import test_postgres_runtime_rls as runtime_rls


class PostgresReadinessBalanceSemanticsTests(unittest.TestCase):
    """Prove readiness detects semantic drift that actually disables balance enforcement."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the checked-in PostgreSQL foundation once for this regression module."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Seed one isolated accounting tenant and its normal runtime binding inputs."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_same_signature_drift_is_causally_tied_to_balance_enforcement_loss(self) -> None:
        """A no-op balance function admits bad data while readiness fails closed."""
        role_name = f"accounting_runtime_{uuid.uuid4().hex[:10]}"
        password = f"AisRuntime{uuid.uuid4().hex}"
        runtime_rls.PostgresRuntimeRlsTests._create_runtime_role(
            role_name, password, self.case.tenant_id
        )
        self.addCleanup(
            runtime_rls.PostgresRuntimeRlsTests._drop_runtime_role,
            role_name,
        )
        runtime_ledger = PostgresPostingLedger(
            runtime_rls.PostgresRuntimeRlsTests._runtime_database_url(
                role_name, password
            ),
            self.case.policy.tenant_reference,
        )

        with self.assertRaisesRegex(
            psycopg.errors.CheckViolation,
            "journal must contain lines whose debit and credit totals are equal",
        ):
            self._exercise_unbalanced_journal()

        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            canonical_definition = admin.execute(
                """
                SELECT pg_get_functiondef(
                    'accounting_core.assert_journal_balance()'::regprocedure
                )
                """
            ).fetchone()[0]
            admin.execute(
                """
                CREATE OR REPLACE FUNCTION accounting_core.assert_journal_balance()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $drift$
                BEGIN
                    RETURN NULL;
                END;
                $drift$
                """
            )

        try:
            # The same unbalanced journal now crosses the deferred database
            # constraint boundary. The transaction is rolled back deliberately
            # so this causal proof does not persist an invalid ledger fact.
            self._exercise_unbalanced_journal()
            with self.assertRaisesRegex(
                AccountingValidationError,
                "accounting database schema is incomplete",
            ):
                runtime_ledger.check_readiness()
        finally:
            with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
                admin.execute(canonical_definition)

        with self.assertRaisesRegex(
            psycopg.errors.CheckViolation,
            "journal must contain lines whose debit and credit totals are equal",
        ):
            self._exercise_unbalanced_journal()
        runtime_ledger.check_readiness()

    def _exercise_unbalanced_journal(self) -> None:
        """Force one unbalanced journal through the deferred DB constraint boundary."""
        connection = psycopg.connect(posting.DATABASE_URL)
        try:
            legal_entity_id = connection.execute(
                """
                SELECT legal_entity_id
                FROM accounting_core.legal_entity_record
                WHERE tenant_account_id = %s
                ORDER BY legal_entity_id
                LIMIT 1
                """,
                (self.case.tenant_id,),
            ).fetchone()[0]
            accounting_book_id = connection.execute(
                """
                SELECT accounting_book_id
                FROM accounting_core.accounting_book
                WHERE tenant_account_id = %s
                  AND legal_entity_id = %s
                ORDER BY accounting_book_id
                LIMIT 1
                """,
                (self.case.tenant_id, legal_entity_id),
            ).fetchone()[0]
            fiscal_period_id = connection.execute(
                """
                SELECT fiscal_period_id
                FROM accounting_core.fiscal_period
                WHERE tenant_account_id = %s
                  AND period_status_code = 'open'
                ORDER BY period_start_date, fiscal_period_id
                LIMIT 1
                """,
                (self.case.tenant_id,),
            ).fetchone()[0]
            chart_account_id = connection.execute(
                """
                SELECT chart_account_id
                FROM accounting_core.chart_account
                WHERE tenant_account_id = %s
                ORDER BY chart_account_code, chart_account_id
                LIMIT 1
                """,
                (self.case.tenant_id,),
            ).fetchone()[0]

            proposal_record_id = uuid.uuid4()
            general_journal_id = uuid.uuid4()
            marker = uuid.uuid4().hex
            connection.execute(
                """
                INSERT INTO accounting_integration.journal_proposal_record (
                    proposal_record_id,
                    tenant_account_id,
                    external_proposal_id,
                    proposal_contract_version,
                    idempotency_key,
                    source_payload_hash,
                    proposal_status_code
                ) VALUES (%s, %s, %s, 1, %s, %s, 'validated')
                """,
                (
                    proposal_record_id,
                    self.case.tenant_id,
                    uuid.uuid4(),
                    f"readiness-unbalanced:{marker}",
                    "sha256:" + "a" * 64,
                ),
            )
            connection.execute(
                """
                INSERT INTO accounting_core.general_journal (
                    general_journal_id,
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
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, 'posted',
                    'KRW', 'KRW', DATE '2026-08-31', DATE '2026-08-31',
                    %s, %s, %s
                )
                """,
                (
                    general_journal_id,
                    self.case.tenant_id,
                    legal_entity_id,
                    accounting_book_id,
                    fiscal_period_id,
                    f"readiness-unbalanced-{marker}",
                    proposal_record_id,
                    self.case.policy.accounting_policy_version,
                    self.case.policy.posting_rule_version,
                ),
            )
            connection.execute(
                """
                INSERT INTO accounting_core.journal_entry_line (
                    tenant_account_id,
                    general_journal_id,
                    line_number,
                    chart_account_id,
                    account_role_code,
                    debit_amount,
                    credit_amount,
                    line_description
                ) VALUES (%s, %s, 1, %s, 'accounts_receivable', 1, 0, %s)
                """,
                (
                    self.case.tenant_id,
                    general_journal_id,
                    chart_account_id,
                    f"readiness balance semantic proof {marker}",
                ),
            )
            connection.execute("SET CONSTRAINTS ALL IMMEDIATE")
        finally:
            connection.rollback()
            connection.close()


if __name__ == "__main__":
    unittest.main()
