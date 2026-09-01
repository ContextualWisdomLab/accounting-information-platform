"""Fail-closed regression for database-assigned reconciliation transition hashes."""

from __future__ import annotations

import contextlib
import unittest
import unittest.mock as mock
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

from accounting_information_platform import AccountingValidationError
from accounting_information_platform import reconciliation_close_package as close_package
from accounting_information_platform import reconciliation_lifecycle as lifecycle

_TENANT_REFERENCE = "urn:cwl:tenant:test"
_TENANT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_RUN_ID = UUID("11111111-1111-1111-1111-111111111111")
_TRANSITION_ID = UUID("22222222-2222-2222-2222-222222222222")
_EFFECTIVE_AT = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
_RECORDED_AT = datetime(2026, 9, 1, 12, 1, tzinfo=timezone.utc)
_PLACEHOLDER_HASH = "sha256:" + "0" * 64


class _Rows:
    """Return configured rows through the psycopg result interface used by production."""

    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self._rows = rows or []

    def fetchone(self) -> tuple[object, ...] | None:
        """Return the first configured row, if any."""
        return self._rows[0] if self._rows else None


class _PlaceholderHashConnection:
    """Model a database where the transition-hash trigger failed to replace the sentinel."""

    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, query: str, parameters: tuple[object, ...] = ()) -> _Rows:
        """Return stable authority rows while retaining the sentinel transition hash."""
        del parameters
        normalized = " ".join(query.split())
        self.executed.append(normalized)
        if normalized.startswith("SET TRANSACTION ISOLATION LEVEL"):
            return _Rows()
        if (
            "SELECT reconciliation_run_id, actor_reference" in normalized
            and "FROM accounting_core.reconciliation_run_transition_command" in normalized
        ):
            return _Rows()
        if "SELECT 1" in normalized and "FROM accounting_core.reconciliation_run_command" in normalized:
            return _Rows()
        if "SELECT run_status_code, currency_code" in normalized:
            return _Rows([("evaluating", "KRW")])
        if "SELECT reconciliation_command_hash" in normalized:
            return _Rows([("sha256:" + "a" * 64,)])
        if normalized.startswith("INSERT INTO accounting_core.reconciliation_run_transition_command"):
            return _Rows([(_TRANSITION_ID, _PLACEHOLDER_HASH, _RECORDED_AT)])
        if normalized.startswith("UPDATE accounting_core.reconciliation_run"):
            return _Rows()
        if normalized.startswith("INSERT INTO accounting_integration.outbox_event"):
            return _Rows()
        if "FROM accounting_core.reconciliation_run_transition_command AS transition" in normalized:
            return _Rows(
                [
                    (
                        _TRANSITION_ID,
                        "sha256:" + "b" * 64,
                        _PLACEHOLDER_HASH,
                        "urn:cwl:principal:controller",
                        "month_end_reconciliation",
                        _EFFECTIVE_AT,
                        _RECORDED_AT,
                        "reconciled",
                        "sha256:" + "1" * 64,
                        "sha256:" + "2" * 64,
                    )
                ]
            )
        raise AssertionError(f"unexpected lifecycle query: {normalized}")


class _Ledger:
    """Expose the exact transaction and tenant hooks needed by the lifecycle command."""

    connection = _PlaceholderHashConnection()

    def __init__(self, database_url: str, tenant_reference: str) -> None:
        self.database_url = database_url
        self.tenant_reference = tenant_reference

    @contextlib.contextmanager
    def _session(self):
        """Yield the configured transaction connection."""
        yield type(self).connection

    def _acquire_command_lock(self, _connection: object, _scope: str) -> None:
        """Model successful advisory-lock acquisition."""

    def _require_tenant(self, _connection: object) -> UUID:
        """Return the tenant identity bound to the request."""
        return _TENANT_ID


def _bridge() -> SimpleNamespace:
    """Return one exact database-owned bridge suitable for lifecycle completion."""
    return SimpleNamespace(
        statement_population_reference="sha256:" + "1" * 64,
        book_population_reference="sha256:" + "2" * 64,
        statement_opening_balance=Decimal("100.00"),
        statement_period_movements=Decimal("25.00"),
        statement_closing_balance=Decimal("125.00"),
        book_opening_balance=Decimal("100.00"),
        posted_cash_book_movements=Decimal("25.00"),
        book_closing_balance=Decimal("125.00"),
        reconciled_book_balance=Decimal("125.00"),
        outstanding_bank_items=Decimal("0.00"),
        outstanding_book_items=Decimal("0.00"),
        unexplained_difference=Decimal("0.00"),
    )


class ReconciliationLifecycleTransitionHashGuardTests(unittest.TestCase):
    """Prevent unassigned transition hashes from becoming durable accounting evidence."""

    def test_placeholder_transition_hash_fails_before_status_or_outbox_writes(self) -> None:
        """A missing database hash trigger must fail closed before authoritative side effects."""
        _Ledger.connection = _PlaceholderHashConnection()
        payload = {
            "tenant_reference": _TENANT_REFERENCE,
            "reconciliation_action_code": "reconcile",
            "reconciliation_run_id": str(_RUN_ID),
            "reconciliation_idempotency_key": "reconcile-run-hash-guard",
            "actor_reference": "urn:cwl:principal:controller",
            "purpose_code": "month_end_reconciliation",
            "effective_at": "2026-09-01T12:00:00Z",
        }

        with (
            mock.patch.object(lifecycle, "PostgresPostingLedger", _Ledger),
            mock.patch.object(lifecycle, "_load_review_control_state", return_value=((), ())),
            mock.patch.object(close_package, "_database_owned_close_projection_evidence", return_value=_bridge()),
        ):
            with self.assertRaisesRegex(AccountingValidationError, "transition command hash"):
                lifecycle.reconcile_reconciliation_run(
                    payload,
                    "postgresql://example",
                    _TENANT_REFERENCE,
                )

        sql = "\n".join(_Ledger.connection.executed)
        self.assertNotIn("UPDATE accounting_core.reconciliation_run", sql)
        self.assertNotIn("INSERT INTO accounting_integration.outbox_event", sql)


if __name__ == "__main__":  # pragma: no cover - direct test execution convenience
    unittest.main()
