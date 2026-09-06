"""Meaningful defensive-path coverage for reconciliation authority boundaries."""

from __future__ import annotations

import unittest
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from accounting_information_platform.core import (
    AccountingValidationError,
    IdempotencyConflictError,
)
import accounting_information_platform.migration_install as migration_install
import accounting_information_platform.reconciliation_close_package as close_package
import accounting_information_platform.reconciliation_completion as completion


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


class _Session:
    def __init__(self, connection: object) -> None:
        self.connection = connection

    def __enter__(self) -> object:
        return self.connection

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        return None


class MigrationInstallDefensiveCoverageTests(unittest.TestCase):
    """Exercise migration preflight and PostgreSQL-error wrapping without weakening fail-closed behavior."""

    def test_missing_completion_migration_fails_before_base_install(self) -> None:
        with TemporaryDirectory() as directory:
            migration_path = Path(directory) / "0019_reconciliation_run_command_evidence.sql"
            with patch.object(migration_install, "_apply_foundation_migration") as base_install:
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    "completion-evidence migration is missing",
                ):
                    migration_install.apply_foundation_migration(
                        "postgresql://example.invalid/accounting",
                        migration_path,
                    )
            base_install.assert_not_called()

    def test_completion_migration_executes_after_base_chain(self) -> None:
        statements: list[str] = []

        class _Connection:
            def execute(self, statement: str) -> None:
                statements.append(statement)

        class _Psycopg:
            ClientCursor = object()

            @staticmethod
            def connect(*args: object, **kwargs: object) -> _Session:
                return _Session(_Connection())

        with TemporaryDirectory() as directory:
            migration_path = Path(directory) / "0019_reconciliation_run_command_evidence.sql"
            completion_path = Path(directory) / "0020_reconciliation_run_completion_evidence.sql"
            completion_path.write_text("SELECT 1;\n", encoding="utf-8")
            with (
                patch.object(migration_install, "_apply_foundation_migration") as base_install,
                patch.object(migration_install, "_import_psycopg", return_value=_Psycopg),
            ):
                migration_install.apply_foundation_migration(
                    "postgresql://example.invalid/accounting",
                    migration_path,
                )

        base_install.assert_called_once_with(
            "postgresql://example.invalid/accounting",
            migration_path,
        )
        self.assertEqual(statements, ["SELECT 1;\n"])

    def test_completion_migration_wraps_driver_failure(self) -> None:
        class _Psycopg:
            ClientCursor = object()

            @staticmethod
            def connect(*args: object, **kwargs: object) -> _Session:
                raise RuntimeError("driver failure")

        with TemporaryDirectory() as directory:
            migration_path = Path(directory) / "0019_reconciliation_run_command_evidence.sql"
            completion_path = Path(directory) / "0020_reconciliation_run_completion_evidence.sql"
            completion_path.write_text("SELECT 1;\n", encoding="utf-8")
            with (
                patch.object(migration_install, "_apply_foundation_migration"),
                patch.object(migration_install, "_import_psycopg", return_value=_Psycopg),
                self.assertRaisesRegex(
                    AccountingValidationError,
                    "Reconciliation completion migration failed",
                ),
            ):
                migration_install.apply_foundation_migration(
                    "postgresql://example.invalid/accounting",
                    migration_path,
                )


class _CompletionConnection:
    def __init__(
        self,
        *,
        prior_by_key: tuple[object, ...] | None = None,
        prior_by_run: tuple[object, ...] | None = None,
        run_row: tuple[object, ...] | None = ("evaluating",),
        match_rows: list[tuple[object, ...]] | None = None,
        exception_rows: list[tuple[object, ...]] | None = None,
        completion_row: tuple[object, ...] | None = None,
    ) -> None:
        self.prior_by_key = prior_by_key
        self.prior_by_run = prior_by_run
        self.run_row = run_row
        self.match_rows = match_rows if match_rows is not None else []
        self.exception_rows = exception_rows if exception_rows is not None else []
        self.completion_row = completion_row

    def execute(self, statement: str, parameters: object = None) -> _Result:
        normalized = " ".join(statement.split())
        if normalized.startswith("SELECT accounting_core.lock_reconciliation_run_lifecycle"):
            return _Result()
        if "FROM accounting_core.reconciliation_run_completion_command" in normalized:
            if "completion_idempotency_key = %s" in normalized:
                return _Result(one=self.prior_by_key)
            return _Result(one=self.prior_by_run)
        if normalized.startswith("SELECT run_status_code"):
            return _Result(one=self.run_row)
        if "FROM accounting_core.reconciliation_match AS match" in normalized:
            return _Result(many=self.match_rows)
        if "FROM accounting_core.reconciliation_exception" in normalized:
            return _Result(many=self.exception_rows)
        if normalized.startswith(
            "INSERT INTO accounting_core.reconciliation_run_completion_command"
        ):
            return _Result(one=self.completion_row)
        if normalized.startswith("UPDATE accounting_core.reconciliation_run"):
            return _Result()
        raise AssertionError(f"unexpected completion SQL: {normalized}")


class ReconciliationCompletionDefensiveCoverageTests(unittest.TestCase):
    """Cover fail-closed completion branches with command and evidence semantics intact."""

    TENANT_REFERENCE = "tenant-defensive"

    def setUp(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.run_id = uuid.uuid4()
        self.match_id = uuid.uuid4()
        self.completion_id = uuid.uuid4()
        self.valid_match_rows = [
            (
                str(self.match_id),
                "approved",
                "approved",
                "sha256:" + "1" * 64,
            )
        ]
        self.bridge = SimpleNamespace(
            statement_population_reference="sha256:" + "2" * 64,
            book_population_reference="sha256:" + "3" * 64,
            statement_closing_balance=Decimal("100.00"),
            book_closing_balance=Decimal("100.00"),
        )
        self.completion_row = (
            self.completion_id,
            self.run_id,
            "completion-defensive",
            "evaluating",
            "sha256:" + "4" * 64,
            self.bridge.statement_population_reference,
            self.bridge.book_population_reference,
            datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    def _payload(self, *, key: object = "completion-defensive") -> dict[str, object]:
        return {
            "tenant_reference": self.TENANT_REFERENCE,
            "reconciliation_run_id": str(self.run_id),
            "completion_idempotency_key": key,
        }

    def _invoke(
        self,
        connection: _CompletionConnection,
        *,
        bridge: object | None = None,
        bridge_error: Exception | None = None,
    ) -> dict[str, object]:
        tenant_id = self.tenant_id

        class _Ledger:
            def __init__(self, database_url: str, tenant_reference: str) -> None:
                self.connection = connection

            def _consistent_read_session(self) -> _Session:
                return _Session(self.connection)

            def _require_tenant(self, current: object) -> uuid.UUID:
                return tenant_id

            def _acquire_command_lock(self, current: object, lock_key: str) -> None:
                return None

        bridge_patch = (
            patch.object(
                completion,
                "_database_owned_close_projection_evidence",
                side_effect=bridge_error,
            )
            if bridge_error is not None
            else patch.object(
                completion,
                "_database_owned_close_projection_evidence",
                return_value=bridge if bridge is not None else self.bridge,
            )
        )
        with (
            patch.object(completion, "PostgresPostingLedger", _Ledger),
            patch.object(
                completion,
                "_load_reconciliation_run_document",
                return_value={"run_status_code": "reconciled"},
            ),
            bridge_patch,
        ):
            return completion.accept_reconciliation_run_completion(
                self._payload(),
                "postgresql://example.invalid/accounting",
                self.TENANT_REFERENCE,
            )

    def test_payload_must_be_mapping(self) -> None:
        with self.assertRaisesRegex(AccountingValidationError, "payload must be a JSON object"):
            completion.accept_reconciliation_run_completion(
                [],
                "postgresql://example.invalid/accounting",
                self.TENANT_REFERENCE,
            )

    def test_payload_tenant_must_match_bound_tenant(self) -> None:
        with self.assertRaisesRegex(AccountingValidationError, "tenant_reference does not match"):
            completion.accept_reconciliation_run_completion(
                {
                    "tenant_reference": "tenant-other",
                    "reconciliation_run_id": str(self.run_id),
                    "completion_idempotency_key": "completion-defensive",
                },
                "postgresql://example.invalid/accounting",
                self.TENANT_REFERENCE,
            )

    def test_completion_key_must_be_canonical_non_empty_text(self) -> None:
        with self.assertRaisesRegex(AccountingValidationError, "completion_idempotency_key is required"):
            completion.accept_reconciliation_run_completion(
                self._payload(key=" completion-defensive "),
                "postgresql://example.invalid/accounting",
                self.TENANT_REFERENCE,
            )

    def test_replayed_key_cannot_point_to_different_run(self) -> None:
        prior = list(self.completion_row)
        prior[1] = uuid.uuid4()
        connection = _CompletionConnection(prior_by_key=tuple(prior))
        with self.assertRaisesRegex(IdempotencyConflictError, "different reconciliation run"):
            self._invoke(connection)

    def test_replayed_key_returns_immutable_completion_receipt(self) -> None:
        connection = _CompletionConnection(prior_by_key=self.completion_row)
        result = self._invoke(connection)
        self.assertTrue(result["replayed"])
        self.assertEqual(
            result["reconciliation_run_completion_command_id"],
            str(self.completion_id),
        )

    def test_run_cannot_be_completed_with_different_command_key(self) -> None:
        connection = _CompletionConnection(prior_by_run=self.completion_row)
        with self.assertRaisesRegex(IdempotencyConflictError, "different command key"):
            self._invoke(connection)

    def test_missing_run_fails_closed(self) -> None:
        connection = _CompletionConnection(run_row=None)
        with self.assertRaisesRegex(AccountingValidationError, "run is not recorded"):
            self._invoke(connection)

    def test_terminal_run_cannot_be_reopened_by_completion(self) -> None:
        connection = _CompletionConnection(run_row=("reconciled",))
        with self.assertRaisesRegex(AccountingValidationError, "must be evaluating or review_required"):
            self._invoke(connection)

    def test_proposed_match_blocks_completion(self) -> None:
        connection = _CompletionConnection(
            match_rows=[(str(self.match_id), "proposed", None, None)]
        )
        with self.assertRaisesRegex(AccountingValidationError, "proposed reconciliation matches remain"):
            self._invoke(connection)

    def test_approved_match_requires_immutable_approval_snapshot(self) -> None:
        connection = _CompletionConnection(
            match_rows=[(str(self.match_id), "approved", "approved", "not-a-sha256")]
        )
        with self.assertRaisesRegex(AccountingValidationError, "immutable database approval snapshot"):
            self._invoke(connection)

    def test_open_exception_blocks_completion(self) -> None:
        connection = _CompletionConnection(
            match_rows=self.valid_match_rows,
            exception_rows=[(str(uuid.uuid4()), "open")],
        )
        with self.assertRaisesRegex(AccountingValidationError, "open reconciliation exceptions remain"):
            self._invoke(connection)

    def test_incomplete_database_bridge_is_domain_validation_error(self) -> None:
        connection = _CompletionConnection(match_rows=self.valid_match_rows)
        with self.assertRaisesRegex(AccountingValidationError, "bridge evidence is incomplete"):
            self._invoke(connection, bridge_error=ValueError("source population torn"))

    def test_success_builds_receipt_through_authoritative_read_model(self) -> None:
        connection = _CompletionConnection(
            match_rows=self.valid_match_rows,
            completion_row=self.completion_row,
        )
        result = self._invoke(connection)
        self.assertFalse(result["replayed"])
        self.assertEqual(result["run_status_code"], "reconciled")
        self.assertEqual(result["completion_idempotency_key"], "completion-defensive")


class _SequencedConnection:
    def __init__(self, responses: list[list[tuple[object, ...]]]) -> None:
        self.responses = list(responses)

    def execute(self, statement: str, parameters: object = None) -> _Result:
        if not self.responses:
            raise AssertionError("unexpected database-owned close-projection query")
        return _Result(many=self.responses.pop(0))


class ReconciliationCloseProjectionDefensiveCoverageTests(unittest.TestCase):
    """Exercise rejected database-owned population states instead of excluding them from coverage."""

    def setUp(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.run_reference = str(uuid.uuid4())
        self.opening_hash = "opening-balance"
        self.closing_hash = "closing-balance"
        self.statement_record_id = uuid.uuid4()
        self.chart_account_id = uuid.uuid4()
        self.period_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.period_end = datetime(2026, 1, 31, 23, 59, 59, tzinfo=timezone.utc)
        self.cutoff = datetime(2026, 2, 1, tzinfo=timezone.utc)
        self.scope = [
            (
                self.statement_record_id,
                self.opening_hash,
                self.closing_hash,
                self.period_start,
                self.period_end,
                date(2026, 1, 31),
                self.cutoff,
                self.chart_account_id,
                "USD",
            )
        ]
        self.balances = [
            (self.opening_hash, Decimal("100"), "USD", "CRDT"),
            (self.closing_hash, Decimal("110"), "USD", "CRDT"),
        ]
        self.entries = [
            (
                "entry-1",
                1,
                Decimal("10"),
                "USD",
                "CRDT",
                False,
                "sha256:" + "a" * 64,
            )
        ]

    def _load(self, responses: list[list[tuple[object, ...]]]) -> object:
        return close_package._database_owned_close_projection_evidence(
            _SequencedConnection(responses),
            self.tenant_id,
            reconciliation_run_reference=self.run_reference,
        )

    def test_bank_direction_supports_debit_and_rejects_unknown_codes(self) -> None:
        self.assertEqual(
            close_package._signed_bank_amount(Decimal("10"), "DBIT"),
            Decimal("-10"),
        )
        with self.assertRaisesRegex(ValueError, "CRDT or DBIT"):
            close_package._signed_bank_amount(Decimal("10"), "UNKNOWN")

    def test_allocations_reject_unknown_sources_invalid_amounts_and_overallocation(self) -> None:
        capacities = {"source-1": Decimal("10")}
        with self.assertRaisesRegex(ValueError, "unknown source"):
            close_package._allocated_by_source(
                [("missing", Decimal("1"))],
                capacities=capacities,
                label="statement",
            )
        with self.assertRaisesRegex(ValueError, "positive exact amount"):
            close_package._allocated_by_source(
                [("source-1", Decimal("0"))],
                capacities=capacities,
                label="statement",
            )
        with self.assertRaisesRegex(ValueError, "exceeds source capacity"):
            close_package._allocated_by_source(
                [("source-1", Decimal("11"))],
                capacities=capacities,
                label="statement",
            )

    def test_source_scope_must_resolve_exactly_one_row(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one statement and cash account"):
            self._load([[]])

    def test_source_scope_requires_balance_hashes_and_period_start(self) -> None:
        incomplete_scope = [
            (
                self.statement_record_id,
                None,
                self.closing_hash,
                self.period_start,
                self.period_end,
                date(2026, 1, 31),
                self.cutoff,
                self.chart_account_id,
                "USD",
            )
        ]
        with self.assertRaisesRegex(ValueError, "requires exact statement opening/closing balances"):
            self._load([incomplete_scope])

    def test_statement_balances_must_include_both_required_hashes(self) -> None:
        with self.assertRaisesRegex(ValueError, "opening and closing balance evidence"):
            self._load([self.scope, [self.balances[0]]])

    def test_statement_balances_must_use_reconciliation_currency(self) -> None:
        wrong_currency = [
            self.balances[0],
            (self.closing_hash, Decimal("110"), "EUR", "CRDT"),
        ]
        with self.assertRaisesRegex(ValueError, "balances must use the reconciliation currency"):
            self._load([self.scope, wrong_currency])

    def test_statement_population_must_not_be_empty(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one immutable entry"):
            self._load([self.scope, self.balances, []])

    def test_statement_entries_must_use_reconciliation_currency(self) -> None:
        wrong_currency = [
            (
                "entry-1",
                1,
                Decimal("10"),
                "EUR",
                "CRDT",
                False,
                "sha256:" + "a" * 64,
            )
        ]
        with self.assertRaisesRegex(ValueError, "entries must use the reconciliation currency"):
            self._load([self.scope, self.balances, wrong_currency])

    def test_statement_population_identity_must_be_unique(self) -> None:
        duplicates = [
            self.entries[0],
            (
                "entry-1",
                2,
                Decimal("0.01"),
                "USD",
                "CRDT",
                False,
                "sha256:" + "b" * 64,
            ),
        ]
        with self.assertRaisesRegex(ValueError, "identities must be unique"):
            self._load([self.scope, self.balances, duplicates])

    def test_statement_opening_plus_movements_must_equal_closing(self) -> None:
        mismatched = [
            (
                "entry-1",
                1,
                Decimal("9"),
                "USD",
                "CRDT",
                False,
                "sha256:" + "a" * 64,
            )
        ]
        with self.assertRaisesRegex(ValueError, "opening plus movements must equal closing"):
            self._load([self.scope, self.balances, mismatched])

    def test_cash_journal_population_must_use_reconciliation_currency(self) -> None:
        journals = [
            (
                "journal-1",
                date(2026, 1, 2),
                datetime(2026, 1, 2, tzinfo=timezone.utc),
                1,
                Decimal("10"),
                Decimal("0"),
                "EUR",
            )
        ]
        with self.assertRaisesRegex(ValueError, "cash journal population must use"):
            self._load([self.scope, self.balances, self.entries, journals])

    def test_evidence_population_rejects_duplicate_reconciliation_run_authority(self) -> None:
        digest = "sha256:" + "c" * 64
        cutoff = "2026-01-01T00:00:00Z"
        evidence = (
            close_package.ReconciliationEvidenceReference(
                "reconciliation_run", "run-1", digest, cutoff
            ),
            close_package.ReconciliationEvidenceReference(
                "reconciliation_run", "run-2", digest, cutoff
            ),
            close_package.ReconciliationEvidenceReference(
                "statement_artifact", "artifact-1", digest
            ),
            close_package.ReconciliationEvidenceReference(
                "statement_population", "statement-population-1", digest
            ),
            close_package.ReconciliationEvidenceReference(
                "book_population", "book-population-1", digest
            ),
        )
        projection = SimpleNamespace(
            reconciliation_run_reference="run-1",
            statement_population_reference="statement-population-1",
            book_population_reference="book-population-1",
        )
        with self.assertRaisesRegex(ValueError, "exactly one reconciliation_run evidence"):
            close_package._validate_and_order_evidence(evidence, projection=projection)


if __name__ == "__main__":
    unittest.main()
