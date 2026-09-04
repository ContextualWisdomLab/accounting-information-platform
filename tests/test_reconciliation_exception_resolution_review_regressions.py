"""Regression contracts for reviewed exception-resolution authority defects."""

from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from unittest import mock

from accounting_information_platform import IdempotencyConflictError
from accounting_information_platform import persistence
from accounting_information_platform import reconciliation_exception_resolution as resolution
from tests.test_reconciliation_exception_resolution import (
    _EFFECTIVE_AT,
    _EVIDENCE_HASH,
    _EVIDENCE_REFERENCE,
    _EXCEPTION_ID,
    _Ledger,
    _RUN_ID,
    _command,
    _source_hash,
)

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION = _ROOT / "database/migrations/0020_reconciliation_exception_resolution_command.sql"


class ReconciliationExceptionResolutionReviewRegressionTests(unittest.TestCase):
    """Keep reviewed authority and installation defects from regressing."""

    def test_replay_binds_full_incoming_command_payload(self) -> None:
        """A changed formerly ignored payload member cannot replay under the original key."""
        connection = _Ledger.connection = type(_Ledger.connection)()
        _Ledger.locks = []
        connection.prior = (
            _RUN_ID,
            _EXCEPTION_ID,
            "resolved",
            _EVIDENCE_REFERENCE,
            _EVIDENCE_HASH,
            _source_hash(),
            "urn:cwl:principal:independent_reviewer",
            "bank_reconciliation_exception_review",
            _EFFECTIVE_AT,
        )
        changed = _command(request_context={"review_batch": "changed"})
        with mock.patch.object(resolution, "PostgresPostingLedger", _Ledger):
            with self.assertRaises(IdempotencyConflictError):
                resolution.resolve_reconciliation_exception(
                    changed,
                    "postgresql://example",
                    "urn:cwl:tenant:test",
                )

    def test_upgrade_preflight_rejects_legacy_terminal_exceptions(self) -> None:
        """Migration 0020 stops before minting authority over legacy terminal rows."""
        migration = _MIGRATION.read_text(encoding="utf-8")
        marker = "reconciliation_exception_resolution_legacy_terminal_preflight"
        self.assertIn(marker, migration)
        self.assertIn("resolution_status_code <> 'open'", migration)
        self.assertLess(migration.index(marker), migration.index("CREATE TABLE"))

    def test_upgrade_preflight_can_see_forced_rls_history(self) -> None:
        """Migration-owner preflight visibility cannot be filtered by tenant forced RLS."""
        migration = _MIGRATION.read_text(encoding="utf-8")
        policy = "CREATE POLICY reconciliation_exception_resolution_upgrade_visibility"
        marker = "reconciliation_exception_resolution_legacy_terminal_preflight"
        drop_policy = "DROP POLICY reconciliation_exception_resolution_upgrade_visibility"
        self.assertIn(policy, migration)
        self.assertIn("ON accounting_core.reconciliation_exception", migration)
        self.assertIn("FOR SELECT\n    TO current_user\n    USING (true);", migration)
        self.assertIn(drop_policy, migration)
        self.assertLess(migration.index(policy), migration.index(marker))
        self.assertLess(migration.index(marker), migration.index(drop_policy))
        self.assertLess(migration.index(drop_policy), migration.index("ALTER TABLE"))

    def test_open_exception_control_evidence_is_frozen_from_creation(self) -> None:
        """Maker evidence cannot be rewritten before the checker command is recorded."""
        migration = _MIGRATION.read_text(encoding="utf-8")
        immutable_marker = "reconciliation_exception_evidence_immutable"
        self.assertIn(immutable_marker, migration)
        guard_start = migration.index(
            "CREATE OR REPLACE FUNCTION accounting_core.enforce_reconciliation_exception_resolution_authority"
        )
        guard_end = migration.index(
            "CREATE TRIGGER accounting_reconciliation_exception_resolution_authority_guard"
        )
        guard = migration[guard_start:guard_end]
        self.assertNotIn("IF command_exists\n       AND (", guard)
        for field in (
            "owner_reference",
            "next_action",
            "effective_at",
            "recorded_at",
        ):
            self.assertIn(f"NEW.{field} IS DISTINCT FROM OLD.{field}", guard)

    def test_retained_review_evidence_is_database_bound_and_immutable(self) -> None:
        """A resolution command cannot promote caller-shaped provenance into authority."""
        migration = _MIGRATION.read_text(encoding="utf-8")
        source = inspect.getsource(resolution._resolve_reconciliation_exception_once)
        for marker in (
            "reconciliation_evidence_id uuid NOT NULL",
            "REFERENCES accounting_core.reconciliation_evidence",
            "exception_resolution_review",
            "reconciliation_exception_resolution_evidence_required",
            "reconciliation_exception_resolution_evidence_time",
            "reconciliation_evidence_immutable",
        ):
            self.assertIn(marker, migration)
        self.assertIn("FROM accounting_core.reconciliation_evidence", source)
        self.assertIn("evidence_payload_hash = %s", source)
        self.assertIn("effective_at <= %s", source)
        self.assertIn("recorded_at <= clock_timestamp()", source)

    def test_canonical_foundation_loader_installs_exception_resolution_migration(self) -> None:
        """Any shared PostgreSQL fixture using the canonical loader reaches migration 0020."""
        loader_source = inspect.getsource(persistence.apply_foundation_migration)
        self.assertIn("0020_reconciliation_exception_resolution_command.sql", loader_source)

    def test_resolution_command_schema_retains_source_payload_hash(self) -> None:
        """Idempotency persists command payload identity apart from reviewed evidence."""
        migration = _MIGRATION.read_text(encoding="utf-8")
        table_start = migration.index(
            "CREATE TABLE accounting_core.reconciliation_exception_resolution_command"
        )
        table_end = migration.index(
            "CREATE INDEX reconciliation_exception_resolution_recorded_index"
        )
        table_definition = migration[table_start:table_end]
        self.assertIn("source_payload_hash", table_definition)
        self.assertIn("^sha256:[0-9a-f]{64}$", table_definition)


if __name__ == "__main__":
    unittest.main()
