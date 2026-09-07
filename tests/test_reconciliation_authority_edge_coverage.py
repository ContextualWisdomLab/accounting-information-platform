"""Fail-closed edge coverage for reconciliation authority and migration installation."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import unittest.mock as mock

from accounting_information_platform import AccountingValidationError
from accounting_information_platform import migration_install as migration_install
from accounting_information_platform import reconciliation_close_package as close_package
from accounting_information_platform import reconciliation_lifecycle as lifecycle
from tests.test_reconciliation_lifecycle import (
    _Connection as _LifecycleConnection,
    _Ledger as _LifecycleLedger,
    _Rows as _LifecycleRows,
    _bridge as _lifecycle_bridge,
    _command as _lifecycle_command,
)


class _Rows:
    """Minimal query result for deterministic close-projection guard tests."""

    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[object, ...]]:
        """Return the configured rows without adding database behavior."""
        return list(self._rows)


class _ScriptedConnection:
    """Return one configured row set for each production query in order."""

    def __init__(self, *responses: list[tuple[object, ...]]) -> None:
        self._responses = iter(responses)

    def execute(self, _query: str, _parameters: tuple[object, ...] = ()) -> _Rows:
        """Return the next exact result set expected by the authority loader."""
        try:
            rows = next(self._responses)
        except StopIteration as error:  # pragma: no cover - fixture defect guard
            raise AssertionError("authority loader executed beyond the scripted boundary") from error
        return _Rows(rows)


def _scope_row(
    *,
    opening_hash: object = "open-hash",
    closing_hash: object = "close-hash",
    period_start: object = datetime(2026, 9, 1, tzinfo=timezone.utc),
    currency_code: object = "KRW",
) -> tuple[object, ...]:
    """Return one complete database-owned reconciliation scope row."""
    return (
        "statement-id",
        opening_hash,
        closing_hash,
        period_start,
        datetime(2026, 9, 30, tzinfo=timezone.utc),
        date(2026, 9, 30),
        datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc),
        "cash-account-id",
        currency_code,
    )


def _balance_rows(*, currency_code: str = "KRW") -> list[tuple[object, ...]]:
    """Return tied opening and closing bank-statement balances."""
    return [
        ("open-hash", "100000", currency_code, "CRDT"),
        ("close-hash", "115000", currency_code, "CRDT"),
    ]


def _entry_rows(
    *,
    amount: str = "15000",
    currency_code: str = "KRW",
    identity: str = "entry-1",
) -> list[tuple[object, ...]]:
    """Return one immutable statement movement."""
    return [
        (
            identity,
            1,
            amount,
            currency_code,
            "CRDT",
            False,
            "sha256:" + "e" * 64,
        )
    ]


class ReconciliationCloseAuthorityEdgeTests(unittest.TestCase):
    """Cover fail-closed source, amount, currency, and population boundaries."""

    def test_signed_bank_amount_handles_debit_and_rejects_unknown_direction(self) -> None:
        """Debit evidence is negative and an unknown direction never becomes a signed fact."""
        self.assertEqual(
            close_package._signed_bank_amount("12.50", "DBIT"),
            Decimal("-12.50"),
        )
        with self.assertRaisesRegex(ValueError, "CRDT or DBIT"):
            close_package._signed_bank_amount("12.50", "UNKNOWN")

    def test_allocations_reject_unknown_nonpositive_and_overcapacity_sources(self) -> None:
        """Approved allocation evidence cannot invent, negate, or overconsume a source."""
        capacities = {"entry-1": Decimal("10")}
        with self.assertRaisesRegex(ValueError, "unknown source"):
            close_package._allocated_by_source(
                [("missing", "1")], capacities=capacities, label="statement"
            )
        with self.assertRaisesRegex(ValueError, "positive exact amount"):
            close_package._allocated_by_source(
                [("entry-1", "0")], capacities=capacities, label="statement"
            )
        with self.assertRaisesRegex(ValueError, "exceeds source capacity"):
            close_package._allocated_by_source(
                [("entry-1", "11")], capacities=capacities, label="statement"
            )

    def test_close_projection_requires_exactly_one_scope(self) -> None:
        """A run without one statement/cash-account scope cannot create close authority."""
        connection = _ScriptedConnection([])
        with self.assertRaisesRegex(ValueError, "resolve exactly one statement"):
            close_package._database_owned_close_projection_evidence(
                connection,
                "tenant-id",
                reconciliation_run_reference="run-id",
            )

    def test_close_projection_requires_statement_scope_balance_identities(self) -> None:
        """Missing opening, closing, or period identity fails before balance population reads."""
        connection = _ScriptedConnection([_scope_row(opening_hash=None)])
        with self.assertRaisesRegex(ValueError, "requires exact statement opening/closing balances"):
            close_package._database_owned_close_projection_evidence(
                connection,
                "tenant-id",
                reconciliation_run_reference="run-id",
            )

    def test_close_projection_requires_both_retained_balances(self) -> None:
        """Both retained balance hashes must resolve inside the knowledge cutoff."""
        connection = _ScriptedConnection(
            [_scope_row()],
            [("open-hash", "100000", "KRW", "CRDT")],
        )
        with self.assertRaisesRegex(ValueError, "must both be present"):
            close_package._database_owned_close_projection_evidence(
                connection,
                "tenant-id",
                reconciliation_run_reference="run-id",
            )

    def test_close_projection_rejects_cross_currency_balances(self) -> None:
        """Statement opening and closing evidence must stay in the run currency."""
        connection = _ScriptedConnection(
            [_scope_row()],
            _balance_rows(currency_code="USD"),
        )
        with self.assertRaisesRegex(ValueError, "balances must use the reconciliation currency"):
            close_package._database_owned_close_projection_evidence(
                connection,
                "tenant-id",
                reconciliation_run_reference="run-id",
            )

    def test_close_projection_requires_statement_entries(self) -> None:
        """An empty retained statement population cannot become reconciliation evidence."""
        connection = _ScriptedConnection(
            [_scope_row()],
            _balance_rows(),
            [],
        )
        with self.assertRaisesRegex(ValueError, "must contain at least one immutable entry"):
            close_package._database_owned_close_projection_evidence(
                connection,
                "tenant-id",
                reconciliation_run_reference="run-id",
            )

    def test_close_projection_rejects_cross_currency_statement_entries(self) -> None:
        """Statement movements from another currency cannot enter the run population."""
        connection = _ScriptedConnection(
            [_scope_row()],
            _balance_rows(),
            _entry_rows(currency_code="USD"),
        )
        with self.assertRaisesRegex(ValueError, "entries must use the reconciliation currency"):
            close_package._database_owned_close_projection_evidence(
                connection,
                "tenant-id",
                reconciliation_run_reference="run-id",
            )

    def test_close_projection_rejects_duplicate_statement_population_identity(self) -> None:
        """Two retained rows cannot collapse to the same source-entry identity."""
        duplicate_rows = _entry_rows(identity="duplicate") + [
            (
                "duplicate",
                2,
                "0",
                "KRW",
                "CRDT",
                False,
                "sha256:" + "f" * 64,
            )
        ]
        connection = _ScriptedConnection(
            [_scope_row()],
            _balance_rows(),
            duplicate_rows,
        )
        with self.assertRaisesRegex(ValueError, "identities must be unique"):
            close_package._database_owned_close_projection_evidence(
                connection,
                "tenant-id",
                reconciliation_run_reference="run-id",
            )

    def test_close_projection_rejects_statement_arithmetic_that_does_not_close(self) -> None:
        """Opening plus retained movements must reproduce the retained closing balance exactly."""
        connection = _ScriptedConnection(
            [_scope_row()],
            _balance_rows(),
            _entry_rows(amount="14000"),
        )
        with self.assertRaisesRegex(ValueError, "opening plus movements must equal closing"):
            close_package._database_owned_close_projection_evidence(
                connection,
                "tenant-id",
                reconciliation_run_reference="run-id",
            )

    def test_close_projection_rejects_cross_currency_cash_journals(self) -> None:
        """Assigned cash-account journals must use the reconciliation currency."""
        journal_rows = [
            (
                "opening-journal",
                date(2026, 8, 22),
                datetime(2026, 8, 22, 12, tzinfo=timezone.utc),
                1,
                "100000",
                "0",
                "USD",
            )
        ]
        connection = _ScriptedConnection(
            [_scope_row()],
            _balance_rows(),
            _entry_rows(),
            journal_rows,
        )
        with self.assertRaisesRegex(ValueError, "cash journal population must use"):
            close_package._database_owned_close_projection_evidence(
                connection,
                "tenant-id",
                reconciliation_run_reference="run-id",
            )


class FoundationMigrationEdgeTests(unittest.TestCase):
    """Cover the complete-chain installer's preflight and recovery fail-closed paths."""

    @staticmethod
    def _write_chain(root: Path, *, include_forward: bool) -> Path:
        """Create the exact file manifest needed to reach the selected installer boundary."""
        migration_path = root / "0001_accounting_foundation.sql"
        migration_path.write_text("-- base\n", encoding="utf-8")
        for filename in migration_install._BASE_FOUNDATION_PREREQUISITES:
            (root / filename).write_text(f"-- {filename}\n", encoding="utf-8")
        if include_forward:
            for filename in migration_install._FORWARD_MIGRATIONS:
                (root / filename).write_text(f"-- {filename}\n", encoding="utf-8")
        return migration_path

    def test_incomplete_base_chain_cannot_succeed_if_base_loader_returns(self) -> None:
        """A base preflight gap still fails even if a mocked legacy loader returns silently."""
        with TemporaryDirectory() as directory:
            migration_path = Path(directory) / "0001_accounting_foundation.sql"
            with mock.patch.object(migration_install, "_apply_base_foundation_migration"):
                with self.assertRaisesRegex(AccountingValidationError, "complete checked-in chain"):
                    migration_install.apply_foundation_migration(
                        "postgresql://unused", migration_path
                    )

    def test_missing_forward_migration_fails_before_base_database_write(self) -> None:
        """Missing lifecycle successors abort before the canonical base loader can write."""
        with TemporaryDirectory() as directory:
            migration_path = self._write_chain(Path(directory), include_forward=False)
            with mock.patch.object(
                migration_install, "_apply_base_foundation_migration"
            ) as base_loader:
                with self.assertRaisesRegex(AccountingValidationError, "lifecycle migration is missing"):
                    migration_install.apply_foundation_migration(
                        "postgresql://unused", migration_path
                    )
            base_loader.assert_not_called()

    def test_forward_migration_execution_error_is_normalized_for_recovery(self) -> None:
        """A PostgreSQL successor failure becomes the stable recovery-facing validation error."""

        class _FailingConnection:
            def __enter__(self) -> "_FailingConnection":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def execute(self, _sql: str) -> None:
                raise RuntimeError("simulated successor failure")

        class _Psycopg:
            ClientCursor = object()

            @staticmethod
            def connect(*_args: object, **_kwargs: object) -> _FailingConnection:
                return _FailingConnection()

        with TemporaryDirectory() as directory:
            migration_path = self._write_chain(Path(directory), include_forward=True)
            with mock.patch.object(migration_install, "_apply_base_foundation_migration"), mock.patch.object(
                migration_install._persistence, "_import_psycopg", return_value=_Psycopg
            ):
                with self.assertRaisesRegex(AccountingValidationError, "migration failed"):
                    migration_install.apply_foundation_migration(
                        "postgresql://unused", migration_path
                    )


class ReconciliationLifecycleAuthorityEdgeTests(unittest.TestCase):
    """Cover fail-closed lifecycle scope evidence not reachable from a valid run."""

    def test_missing_authoritative_run_currency_fails_before_transition_write(self) -> None:
        """A persisted run without currency evidence cannot transition to reconciled."""

        class _MissingCurrencyConnection(_LifecycleConnection):
            def execute(
                self, query: str, parameters: tuple[object, ...] = ()
            ) -> _LifecycleRows:
                normalized = " ".join(query.split())
                if (
                    "SELECT run_status_code" in normalized
                    and "FROM accounting_core.reconciliation_run" in normalized
                    and "FOR UPDATE" in normalized
                ):
                    self.executed.append((normalized, parameters))
                    return _LifecycleRows([(self.run_status, None)])
                return super().execute(query, parameters)

        _LifecycleLedger.connection = _MissingCurrencyConnection()
        _LifecycleLedger.locks = []
        with mock.patch.object(lifecycle, "PostgresPostingLedger", _LifecycleLedger), mock.patch.object(
            close_package,
            "_database_owned_close_projection_evidence",
            return_value=_lifecycle_bridge(),
        ):
            with self.assertRaisesRegex(AccountingValidationError, "currency evidence is missing"):
                lifecycle.reconcile_reconciliation_run(
                    _lifecycle_command(),
                    "postgresql://unused",
                    "urn:cwl:tenant:test",
                )


if __name__ == "__main__":
    unittest.main()
