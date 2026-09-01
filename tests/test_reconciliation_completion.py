"""Behavior tests for the owner-controlled reconciliation completion command."""

from __future__ import annotations

import unittest
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock
from uuid import UUID, uuid4

from accounting_information_platform.core import (
    AccountingValidationError,
    IdempotencyConflictError,
)
from accounting_information_platform import reconciliation_completion as completion


_TENANT = "urn:cwl:tenant:completion-test"
_RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
_TENANT_ID = UUID("22222222-2222-4222-8222-222222222222")
_COMMAND_ID = UUID("33333333-3333-4333-8333-333333333333")
_RUN_HASH = "sha256:" + "1" * 64
_STATEMENT_HASH = "sha256:" + "2" * 64
_BOOK_HASH = "sha256:" + "3" * 64


class _Result:
    """Minimal psycopg-result stand-in used by focused command tests."""

    def __init__(self, *, row: tuple[object, ...] | None = None, rows: list[tuple[object, ...]] | None = None) -> None:
        self._row = row
        self._rows = [] if rows is None else rows

    def fetchone(self) -> tuple[object, ...] | None:
        """Return the configured single row."""
        return self._row

    def fetchall(self) -> list[tuple[object, ...]]:
        """Return the configured row population."""
        return list(self._rows)


class _Connection:
    """Deterministic connection double covering each command query branch."""

    def __init__(
        self,
        *,
        run_row: tuple[object, ...] | None = ("evaluating", _RUN_HASH),
        open_exception: bool = False,
        proposed_match: bool = False,
        prior_row: tuple[object, ...] | None = None,
    ) -> None:
        self.run_row = run_row
        self.open_exception = open_exception
        self.proposed_match = proposed_match
        self.prior_row = prior_row
        self.inserted = False
        self.queries: list[str] = []

    def execute(self, query: str, _params: object = None) -> _Result:
        """Return deterministic rows based on the SQL contract being exercised."""
        self.queries.append(query)
        if "FROM accounting_core.reconciliation_completion_command" in query:
            if self.prior_row is not None:
                return _Result(row=self.prior_row)
            if self.inserted:
                return _Result(row=_completion_row())
            return _Result(row=None)
        if "JOIN accounting_core.reconciliation_run_command" in query:
            return _Result(row=self.run_row)
        if "FROM accounting_core.reconciliation_exception" in query:
            return _Result(row=(self.open_exception,))
        if "FROM accounting_core.reconciliation_match AS match_record" in query and "SELECT EXISTS" in query:
            return _Result(row=(self.proposed_match,))
        if "FROM accounting_core.reconciliation_match AS match_record" in query and "reconciliation_approval" in query:
            return _Result(
                rows=[
                    (
                        "44444444-4444-4444-8444-444444444444",
                        "sha256:" + "4" * 64,
                        "sha256:" + "5" * 64,
                        "memory:approval-1",
                    )
                ]
            )
        if "INSERT INTO accounting_core.reconciliation_completion_command" in query:
            self.inserted = True
            return _Result(row=(_COMMAND_ID,))
        if "UPDATE accounting_core.reconciliation_run" in query:
            return _Result()
        if "INSERT INTO accounting_integration.outbox_event" in query:
            return _Result()
        raise AssertionError(f"unexpected SQL in completion test: {query}")


def _completion_row(
    *,
    run_id: UUID = _RUN_ID,
    actor_reference: str = "urn:cwl:principal:controller",
    purpose_code: str = "reconciliation_close_review",
) -> tuple[object, ...]:
    """Return one immutable stored command row matching the loader projection."""
    return (
        str(_COMMAND_ID),
        str(run_id),
        "completion-key-1",
        "sha256:" + "6" * 64,
        _STATEMENT_HASH,
        _BOOK_HASH,
        "sha256:" + "7" * 64,
        "sha256:" + "8" * 64,
        actor_reference,
        purpose_code,
        datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
    )


def _payload(**changes: object) -> dict[str, object]:
    """Return one canonical completion request with optional changes."""
    payload: dict[str, object] = {
        "tenant_reference": _TENANT,
        "reconciliation_run_id": str(_RUN_ID),
        "reconciliation_completion_key": "completion-key-1",
        "actor_reference": "urn:cwl:principal:controller",
        "completion_purpose_code": "reconciliation_close_review",
    }
    payload.update(changes)
    return payload


def _bridge() -> SimpleNamespace:
    """Return exact database-owned bridge evidence used by the success path."""
    from decimal import Decimal

    return SimpleNamespace(
        statement_population_reference=_STATEMENT_HASH,
        book_population_reference=_BOOK_HASH,
        statement_opening_balance=Decimal("1000.00"),
        statement_period_movements=Decimal("100.00"),
        statement_closing_balance=Decimal("1100.00"),
        book_opening_balance=Decimal("1000.00"),
        posted_cash_book_movements=Decimal("100.00"),
        book_closing_balance=Decimal("1100.00"),
        reconciled_book_balance=Decimal("1100.00"),
        outstanding_bank_items=Decimal("0"),
        outstanding_book_items=Decimal("0"),
        unexplained_difference=Decimal("0"),
    )


class ReconciliationCompletionTests(unittest.TestCase):
    """Exercise validation, replay, fail-closed state, and successful completion."""

    def _patched_ledger(self, connection: _Connection) -> tuple[mock.MagicMock, mock._patch]:
        """Return a ledger double and patcher bound to *connection*."""
        ledger = mock.MagicMock()
        ledger._consistent_read_session.return_value = nullcontext(connection)
        ledger._require_tenant.return_value = _TENANT_ID
        patcher = mock.patch.object(completion, "PostgresPostingLedger", return_value=ledger)
        return ledger, patcher

    def test_canonical_hash_is_deterministic(self) -> None:
        """Equivalent mapping order produces the same content identity."""
        self.assertEqual(
            completion._canonical_sha256({"b": 2, "a": 1}),
            completion._canonical_sha256({"a": 1, "b": 2}),
        )

    def test_command_validation_fails_closed_before_database_access(self) -> None:
        """Malformed scope, identity, actor, purpose, and key values are rejected."""
        invalid_cases: tuple[object, ...] = (
            [],
            _payload(tenant_reference="urn:cwl:tenant:other"),
            _payload(reconciliation_run_id="not-a-uuid"),
            _payload(reconciliation_run_id=1),
            _payload(reconciliation_completion_key=""),
            _payload(reconciliation_completion_key=" bad"),
            _payload(actor_reference=1),
            _payload(actor_reference=""),
            _payload(completion_purpose_code="generic_status_change"),
        )
        for invalid in invalid_cases:
            with self.subTest(invalid=invalid):
                with self.assertRaises(AccountingValidationError):
                    completion._require_completion_command(invalid, _TENANT)

    def test_exact_retry_replays_without_revalidating_mutable_state(self) -> None:
        """An exact stored command replays even after the run has already transitioned."""
        connection = _Connection(prior_row=_completion_row())
        ledger, patcher = self._patched_ledger(connection)
        with patcher, mock.patch.object(
            completion, "_database_owned_close_projection_evidence"
        ) as bridge_loader:
            document = completion.accept_reconciliation_completion(
                _payload(), "postgresql://example", _TENANT
            )
        self.assertTrue(document["replayed"])
        bridge_loader.assert_not_called()
        ledger._acquire_command_lock.assert_called_once()

    def test_same_key_changed_command_conflicts(self) -> None:
        """A stored idempotency key cannot be rebound to another run or actor."""
        connection = _Connection(prior_row=_completion_row(actor_reference="urn:cwl:principal:other"))
        _ledger, patcher = self._patched_ledger(connection)
        with patcher, self.assertRaises(IdempotencyConflictError):
            completion.accept_reconciliation_completion(
                _payload(), "postgresql://example", _TENANT
            )

    def test_missing_or_ineligible_run_fails_closed(self) -> None:
        """Unknown and terminal runs cannot acquire a second completion authority path."""
        for run_row in (None, ("reconciled", _RUN_HASH), ("not_reconciled", _RUN_HASH)):
            with self.subTest(run_row=run_row):
                connection = _Connection(run_row=run_row)
                _ledger, patcher = self._patched_ledger(connection)
                with patcher, self.assertRaises(AccountingValidationError):
                    completion.accept_reconciliation_completion(
                        _payload(), "postgresql://example", _TENANT
                    )

    def test_open_exception_and_pending_match_block_completion(self) -> None:
        """Unresolved exception or proposal populations prevent reconciliation."""
        for connection in (
            _Connection(open_exception=True),
            _Connection(proposed_match=True),
        ):
            with self.subTest(open_exception=connection.open_exception, proposed_match=connection.proposed_match):
                _ledger, patcher = self._patched_ledger(connection)
                with patcher, self.assertRaises(AccountingValidationError):
                    completion.accept_reconciliation_completion(
                        _payload(), "postgresql://example", _TENANT
                    )

    def test_success_binds_bridge_approval_command_transition_and_outbox_atomically(self) -> None:
        """One consistent transaction records immutable evidence, transition, and outbox."""
        connection = _Connection()
        ledger, patcher = self._patched_ledger(connection)
        with patcher, mock.patch.object(
            completion,
            "_database_owned_close_projection_evidence",
            return_value=_bridge(),
        ) as bridge_loader:
            document = completion.accept_reconciliation_completion(
                _payload(), "postgresql://example", _TENANT
            )
        self.assertFalse(document["replayed"])
        self.assertEqual(document["run_status_code"], "reconciled")
        self.assertEqual(document["statement_population_hash"], _STATEMENT_HASH)
        self.assertEqual(document["book_population_hash"], _BOOK_HASH)
        self.assertEqual(document["recorded_at"], "2026-09-01T12:00:00Z")
        bridge_loader.assert_called_once_with(
            connection,
            _TENANT_ID,
            reconciliation_run_reference=str(_RUN_ID),
        )
        sql = "\n".join(connection.queries)
        self.assertIn("INSERT INTO accounting_core.reconciliation_completion_command", sql)
        self.assertIn("UPDATE accounting_core.reconciliation_run", sql)
        self.assertIn("INSERT INTO accounting_integration.outbox_event", sql)
        ledger._consistent_read_session.assert_called_once_with()


if __name__ == "__main__":  # pragma: no cover - direct invocation convenience
    unittest.main()
