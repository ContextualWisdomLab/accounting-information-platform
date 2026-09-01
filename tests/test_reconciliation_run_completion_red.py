"""RED/GREEN contracts for evidence-backed reconciliation-run completion.

A run may become ``reconciled`` only through a tenant-scoped, idempotent command
whose outcome is derived from persisted reconciliation evidence. Direct status
updates are not an accounting owner-control path.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import psycopg

from accounting_information_platform import accept_reconciliation_run_completion
from tests import test_postgres_posting as posting
from tests import test_reconciliation_candidate_allocation_persistence_red as allocation


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0020_reconciliation_run_completion_evidence.sql"


class ReconciliationRunCompletionContractTests(unittest.TestCase):
    """Require a dedicated completion command instead of a generic status setter."""

    def test_completion_migration_and_public_command_exist(self) -> None:
        """The lifecycle repair must be explicit in schema and application code."""
        self.assertTrue(MIGRATION.exists())
        self.assertTrue(callable(accept_reconciliation_run_completion))

    def test_completion_accepts_exact_bridge_with_timing_differences(self) -> None:
        """Known outstanding items may remain when the database-owned bridge ties exactly."""
        tenant_id = uuid.uuid4()
        run_id = uuid.uuid4()
        completion_id = uuid.uuid4()
        approved_match_id = uuid.uuid4()

        class _Result:
            def __init__(
                self,
                *,
                one: tuple[object, ...] | None = None,
                many: list[tuple[object, ...]] | None = None,
            ) -> None:
                self._one = one
                self._many = many or []

            def fetchone(self) -> tuple[object, ...] | None:
                return self._one

            def fetchall(self) -> list[tuple[object, ...]]:
                return self._many

        class _Connection:
            def execute(self, statement: str, parameters: object = None) -> _Result:
                normalized = " ".join(statement.split())
                if normalized.startswith(
                    "SELECT accounting_core.lock_reconciliation_run_lifecycle"
                ):
                    return _Result()
                if "FROM accounting_core.reconciliation_run_completion_command" in normalized:
                    return _Result()
                if normalized.startswith("SELECT run_status_code"):
                    return _Result(one=("evaluating",))
                if "FROM accounting_core.reconciliation_match AS match" in normalized:
                    return _Result(
                        many=[
                            (
                                str(approved_match_id),
                                "approved",
                                "approved",
                                "sha256:" + "1" * 64,
                            )
                        ]
                    )
                if "FROM accounting_core.reconciliation_exception" in normalized:
                    return _Result(many=[])
                if normalized.startswith(
                    "INSERT INTO accounting_core.reconciliation_run_completion_command"
                ):
                    return _Result(
                        one=(
                            completion_id,
                            run_id,
                            "completion-known-timing",
                            "evaluating",
                            "sha256:" + "2" * 64,
                            "sha256:" + "3" * 64,
                            "sha256:" + "4" * 64,
                            datetime(2026, 1, 1, tzinfo=timezone.utc),
                        )
                    )
                if normalized.startswith("UPDATE accounting_core.reconciliation_run"):
                    return _Result()
                raise AssertionError(f"unexpected SQL in completion contract: {normalized}")

        class _Session:
            def __init__(self, connection: _Connection) -> None:
                self.connection = connection

            def __enter__(self) -> _Connection:
                return self.connection

            def __exit__(
                self,
                exc_type: object,
                exc_value: object,
                traceback: object,
            ) -> None:
                return None

        class _Ledger:
            def __init__(self, database_url: str, tenant_reference: str) -> None:
                self.connection = _Connection()

            def _session(self) -> _Session:
                raise AssertionError(
                    "completion authority must not use a READ COMMITTED session"
                )

            def _consistent_read_session(self) -> _Session:
                return _Session(self.connection)

            def _require_tenant(self, connection: _Connection) -> uuid.UUID:
                return tenant_id

            def _acquire_command_lock(self, connection: _Connection, lock_key: str) -> None:
                return None

        bridge = SimpleNamespace(
            statement_population_reference="sha256:" + "3" * 64,
            book_population_reference="sha256:" + "4" * 64,
            statement_closing_balance=Decimal("100.00"),
            book_closing_balance=Decimal("90.00"),
            outstanding_book_items=Decimal("15.00"),
            outstanding_bank_items=Decimal("5.00"),
            unexplained_difference=Decimal("0"),
        )
        with (
            patch(
                "accounting_information_platform.reconciliation_completion.PostgresPostingLedger",
                _Ledger,
            ),
            patch(
                "accounting_information_platform.reconciliation_completion._database_owned_close_projection_evidence",
                return_value=bridge,
            ),
            patch(
                "accounting_information_platform.reconciliation_completion._completion_document",
                return_value={"run_status_code": "reconciled"},
            ),
        ):
            result = accept_reconciliation_run_completion(
                {
                    "tenant_reference": "tenant-fixture",
                    "reconciliation_run_id": str(run_id),
                    "completion_idempotency_key": "completion-known-timing",
                },
                "postgresql://example.invalid/accounting",
                "tenant-fixture",
            )

        self.assertEqual(result["run_status_code"], "reconciled")


@unittest.skipUnless(
    MIGRATION.exists(),
    "RED until reconciliation-run completion evidence is installed",
)
class PostgresReconciliationRunCompletionRedTests(unittest.TestCase):
    """Prove PostgreSQL rejects unaudited or incomplete completion attempts."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            exists = connection.execute(
                "SELECT to_regclass('accounting_core.reconciliation_run_completion_command')"
            ).fetchone()[0]
            if exists is None:
                connection.execute(MIGRATION.read_text(encoding="utf-8"))

    def setUp(self) -> None:
        self.fixture = allocation.PostgresReconciliationAllocationRedTests("setUp")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)

    def test_direct_status_update_cannot_claim_reconciled(self) -> None:
        """A caller cannot manufacture reconciliation truth with direct SQL."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(self.fixture.scope["tenant_account_id"]),),
            )
            with self.assertRaisesRegex(psycopg.Error, "completion command"):
                connection.execute(
                    """
                    UPDATE accounting_core.reconciliation_run
                    SET run_status_code = 'reconciled'
                    WHERE tenant_account_id = %s
                      AND reconciliation_run_id = %s
                    """,
                    (self.fixture.scope["tenant_account_id"], self.fixture.run_reference),
                )

    def test_completion_rejects_run_without_approved_review_evidence(self) -> None:
        """Opening a run is not enough to manufacture reconciled accounting evidence."""
        with self.assertRaisesRegex(Exception, "approved reconciliation match"):
            accept_reconciliation_run_completion(
                {
                    "tenant_reference": self.fixture.case.policy.tenant_reference,
                    "reconciliation_run_id": str(self.fixture.run_reference),
                    "completion_idempotency_key": "completion-missing-review",
                },
                posting.DATABASE_URL,
                self.fixture.case.policy.tenant_reference,
            )


if __name__ == "__main__":
    unittest.main()
