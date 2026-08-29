"""Real PostgreSQL readiness regressions for behavior-defining trigger registrations."""

from __future__ import annotations

import unittest
import uuid

import psycopg
from psycopg import sql

from accounting_information_platform import AccountingValidationError, PostgresPostingLedger
from tests import test_postgres_posting as posting
from tests import test_postgres_runtime_rls as runtime_rls


_REQUIRED_CONTROL_TRIGGERS = (
    ("accounting_core", "journal_entry_line", "journal_line_book_scope_guard"),
    ("accounting_core", "general_journal", "closed_period_guard"),
    ("accounting_core", "journal_reversal", "journal_reversal_first_temporal_guard"),
    ("accounting_core", "journal_reversal", "journal_reversal_second_finalization_guard"),
    ("accounting_core", "general_journal", "general_journal_immutable_guard"),
    ("accounting_core", "journal_entry_line", "journal_entry_immutable_guard"),
    ("accounting_core", "journal_source_reference", "journal_source_immutable_guard"),
    ("accounting_core", "journal_reversal", "journal_reversal_immutable_guard"),
    ("accounting_integration", "posting_receipt", "posting_receipt_immutable_guard"),
    ("accounting_integration", "journal_proposal_record", "journal_proposal_immutable_guard"),
    ("accounting_core", "journal_entry_line", "journal_entry_finalized_guard"),
    ("accounting_core", "journal_source_reference", "journal_source_finalized_guard"),
    ("accounting_integration", "fiscal_period_open_command", "fiscal_period_open_command_immutable"),
    ("accounting_core", "accounting_book_period_control", "soft_close_evidence_immutable_guard"),
    ("accounting_integration", "bank_statement_artifact", "bank_statement_artifact_immutable_guard"),
    ("accounting_integration", "bank_statement_record", "bank_statement_record_immutable_guard"),
    ("accounting_integration", "bank_statement_entry", "bank_statement_entry_immutable_guard"),
    ("accounting_integration", "bank_statement_entry_detail", "bank_statement_entry_detail_immutable_guard"),
    ("accounting_core", "reconciliation_run", "reconciliation_run_scope_guard"),
)


class PostgresReadinessTriggerContractTests(unittest.TestCase):
    """Prove readiness fails closed when a required database control is detached."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the checked-in PostgreSQL foundation once for this regression module."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Seed one tenant and provision a restricted runtime identity for readiness."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        role_name = f"accounting_runtime_{uuid.uuid4().hex[:10]}"
        password = f"AisRuntime{uuid.uuid4().hex}"
        runtime_rls.PostgresRuntimeRlsTests._create_runtime_role(
            role_name, password, self.case.tenant_id
        )
        self.addCleanup(
            runtime_rls.PostgresRuntimeRlsTests._drop_runtime_role,
            role_name,
        )
        self.runtime_ledger = PostgresPostingLedger(
            runtime_rls.PostgresRuntimeRlsTests._runtime_database_url(
                role_name, password
            ),
            self.case.policy.tenant_reference,
        )

    def test_every_required_behavior_trigger_must_remain_enabled(self) -> None:
        """Disabling any checked-in behavior trigger makes readiness fail closed."""
        self.runtime_ledger.check_readiness()
        for schema_name, table_name, trigger_name in _REQUIRED_CONTROL_TRIGGERS:
            with self.subTest(trigger_name=trigger_name):
                with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
                    admin.execute(
                        sql.SQL("ALTER TABLE {}.{} DISABLE TRIGGER {}").format(
                            sql.Identifier(schema_name),
                            sql.Identifier(table_name),
                            sql.Identifier(trigger_name),
                        )
                    )
                try:
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        "accounting database schema is incomplete",
                    ):
                        self.runtime_ledger.check_readiness()
                finally:
                    with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
                        admin.execute(
                            sql.SQL("ALTER TABLE {}.{} ENABLE TRIGGER {}").format(
                                sql.Identifier(schema_name),
                                sql.Identifier(table_name),
                                sql.Identifier(trigger_name),
                            )
                        )
                self.runtime_ledger.check_readiness()

    def test_update_of_column_contract_drift_is_not_accepted(self) -> None:
        """A broader soft-close trigger is drift, even when its function remains attached."""
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
            admin.execute(
                "DROP TRIGGER soft_close_evidence_immutable_guard "
                "ON accounting_core.accounting_book_period_control"
            )
            admin.execute(
                """
                CREATE TRIGGER soft_close_evidence_immutable_guard
                    BEFORE UPDATE
                    ON accounting_core.accounting_book_period_control
                    FOR EACH ROW
                    EXECUTE FUNCTION accounting_core.guard_soft_close_evidence_update()
                """
            )
        try:
            with self.assertRaisesRegex(
                AccountingValidationError,
                "accounting database schema is incomplete",
            ):
                self.runtime_ledger.check_readiness()
        finally:
            with psycopg.connect(posting.DATABASE_URL, autocommit=True) as admin:
                admin.execute(
                    "DROP TRIGGER soft_close_evidence_immutable_guard "
                    "ON accounting_core.accounting_book_period_control"
                )
                admin.execute(
                    """
                    CREATE TRIGGER soft_close_evidence_immutable_guard
                        BEFORE UPDATE OF soft_close_idempotency_key,
                                         soft_close_source_payload_hash,
                                         soft_close_source_journal_count
                        ON accounting_core.accounting_book_period_control
                        FOR EACH ROW
                        EXECUTE FUNCTION accounting_core.guard_soft_close_evidence_update()
                    """
                )
        self.runtime_ledger.check_readiness()


if __name__ == "__main__":
    unittest.main()
