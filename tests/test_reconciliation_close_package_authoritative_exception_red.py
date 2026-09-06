"""RED contracts binding close-package eligibility to authoritative database state."""

from __future__ import annotations

import unittest
import uuid
from dataclasses import replace

import psycopg

from accounting_information_platform import reconciliation_close_package as close_package
from accounting_information_platform.persistence import PostgresPostingLedger
from tests import test_postgres_posting as posting
from tests import test_reconciliation_candidate_allocation_persistence_red as allocation
from tests.test_reconciliation_close_package_red import ReconciliationClosePackageTests


class _RowsResult:
    """Minimal database result carrying a complete exception population."""

    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    def fetchall(self) -> list[tuple[object, ...]]:
        """Return all configured rows."""
        return self.rows


class _RowsConnection:
    """Capture the authoritative exception query for defensive contract tests."""

    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.query: str | None = None
        self.parameters: tuple[object, ...] | None = None

    def execute(self, query: str, parameters: tuple[object, ...]) -> _RowsResult:
        """Record one query and return configured exception rows."""
        self.query = query
        self.parameters = parameters
        return _RowsResult(self.rows)


class ReconciliationClosePackageAuthoritativeExceptionTests(unittest.TestCase):
    """Reject close-package projections that omit database-owned open exceptions."""

    tenant_id = "tenant-id"
    run_reference = "run-001"

    def setUp(self) -> None:
        fixture = ReconciliationClosePackageTests(
            "test_package_is_order_independent_and_preserves_exact_values"
        )
        self.projection = fixture._projection()

    def _validate(
        self,
        rows: list[tuple[object, ...]],
        projection=None,
    ) -> _RowsConnection:
        connection = _RowsConnection(rows)
        close_package._validate_database_owned_exception_state(
            connection,
            self.tenant_id,
            reconciliation_run_reference=self.run_reference,
            projection=self.projection if projection is None else projection,
        )
        return connection

    def test_zero_open_exceptions_matches_clean_projection_and_locks_population(self) -> None:
        connection = self._validate(
            [
                ("exception-resolved", "resolved"),
                ("exception-superseded", "superseded"),
            ]
        )
        self.assertEqual(connection.parameters, (self.tenant_id, self.run_reference))
        self.assertIn("reconciliation_exception", connection.query or "")
        self.assertIn("FOR SHARE", connection.query or "")

    def test_open_exception_missing_from_projection_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "database-owned unresolved exception population",
        ):
            self._validate([("exception-open", "open")])

    def test_projection_exception_count_must_equal_open_database_population(self) -> None:
        projection = replace(
            self.projection,
            exception_count=2,
            exception_statement_entry_references=("stmt-a", "stmt-b"),
            suitable_for_period_close_review=False,
        )
        self._validate(
            [
                ("exception-open-a", "open"),
                ("exception-resolved", "resolved"),
                ("exception-open-b", "open"),
            ],
            projection,
        )


class PostgresReconciliationClosePackageAuthoritativeStateTests(unittest.TestCase):
    """Exercise run scope and unresolved-exception eligibility in real PostgreSQL."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.fixture = allocation.PostgresReconciliationAllocationRedTests("setUp")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        base = ReconciliationClosePackageTests(
            "test_package_is_order_independent_and_preserves_exact_values"
        )._projection()
        self.projection = replace(
            base,
            tenant_account_reference=self.fixture.case.policy.tenant_reference,
            legal_entity_reference=self.fixture.case.policy.legal_entity_reference,
            accounting_book_reference=self.fixture.case.policy.accounting_book_reference,
            bank_account_assignment_reference=str(
                self.fixture.scope["bank_account_assignment_id"]
            ),
            reconciliation_run_reference=str(self.fixture.run_reference),
            currency_code="KRW",
            exception_count=0,
            exception_statement_entry_references=(),
        )

    def _ledger(self) -> PostgresPostingLedger:
        return PostgresPostingLedger(
            posting.DATABASE_URL,
            self.fixture.case.policy.tenant_reference,
        )

    def _validate(self) -> None:
        with self._ledger()._session() as connection:
            tenant_account_id = self._ledger()._require_tenant(connection)
            close_package._validate_database_owned_exception_state(
                connection,
                tenant_account_id,
                reconciliation_run_reference=str(self.fixture.run_reference),
                projection=self.projection,
            )

    def test_run_scope_loader_rejects_non_reconciled_run(self) -> None:
        """An evaluating fixture cannot be promoted to close-package authority."""
        ledger = self._ledger()
        with ledger._session() as connection:
            tenant_account_id = ledger._require_tenant(connection)
            with self.assertRaisesRegex(
                ValueError,
                "must be reconciled before close-package construction",
            ):
                close_package._database_owned_run_source_evidence(
                    connection,
                    tenant_account_id,
                    tenant_reference=self.fixture.case.policy.tenant_reference,
                    reconciliation_run_reference=str(self.fixture.run_reference),
                )

    def test_open_database_exception_blocks_clean_close_projection(self) -> None:
        self._validate()
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_exception (
                    reconciliation_exception_id, tenant_account_id,
                    reconciliation_run_id, exception_code, owner_reference,
                    next_action, effective_at, resolution_status_code
                )
                VALUES (%s, %s, %s, 'unmatched_statement', 'controller-test',
                        'Resolve the unmatched statement evidence before close review.',
                        %s, 'open')
                """,
                (
                    uuid.uuid4(),
                    self.fixture.scope["tenant_account_id"],
                    self.fixture.run_reference,
                    allocation.VALID_FROM,
                ),
            )

        with self.assertRaisesRegex(
            ValueError,
            "database-owned unresolved exception population",
        ):
            self._validate()


if __name__ == "__main__":
    unittest.main()