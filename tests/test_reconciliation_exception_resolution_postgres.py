"""Real PostgreSQL acceptance for maker-checker reconciliation exception resolution."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from accounting_information_platform import (
    AccountingValidationError,
    IdempotencyConflictError,
    accept_reconciliation_run,
    resolve_reconciliation_exception,
)
from tests import test_postgres_posting as posting
from tests.test_reconciliation_run_api import ReconciliationRunApiTests

_ROOT = Path(__file__).resolve().parents[1]
_RESOLUTION_MIGRATION = (
    _ROOT / "database/migrations/0020_reconciliation_exception_resolution_command.sql"
)
_EVIDENCE_HASH = "sha256:" + "a" * 64


class ReconciliationExceptionResolutionPostgresTests(unittest.TestCase):
    """Prove mutable exception status cannot substitute for named command evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install migrations 0001 through 0020 in real PostgreSQL."""
        posting.PostgresPostingTests.setUpClass()
        with psycopg.connect(
            posting.DATABASE_URL,
            autocommit=True,
            cursor_factory=psycopg.ClientCursor,
        ) as connection:
            connection.execute(_RESOLUTION_MIGRATION.read_text(encoding="utf-8"))

    def setUp(self) -> None:
        """Open one evaluating run and persist one review exception."""
        self.fixture = ReconciliationRunApiTests(
            "test_open_run_binds_statement_scope_and_replays"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)
        _statement, command = self.fixture._statement_and_command()
        self.opened = accept_reconciliation_run(
            command,
            posting.DATABASE_URL,
            self.fixture.case.policy.tenant_reference,
        )
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = self._tenant_id(connection)
            self.exception_id = connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_exception (
                    tenant_account_id,
                    reconciliation_run_id,
                    exception_code,
                    owner_reference,
                    next_action,
                    effective_at,
                    resolution_status_code
                )
                VALUES (
                    %s, %s, 'missing_book_candidate',
                    'urn:cwl:principal:controller_owner',
                    'Attach reviewed evidence and resolve through the named command.',
                    %s, 'open'
                )
                RETURNING reconciliation_exception_id
                """,
                (
                    tenant_id,
                    self.opened["reconciliation_run_id"],
                    datetime(2026, 9, 2, 0, 10, tzinfo=timezone.utc),
                ),
            ).fetchone()[0]
            connection.commit()

    def _tenant_id(self, connection: psycopg.Connection) -> object:
        """Resolve the database tenant identity for the opened aggregate."""
        return connection.execute(
            """
            SELECT tenant_account_id
            FROM accounting_core.reconciliation_run
            WHERE reconciliation_run_id = %s
            """,
            (self.opened["reconciliation_run_id"],),
        ).fetchone()[0]

    def _command(self, **overrides: object) -> dict[str, object]:
        """Return one reviewed exception-resolution command."""
        command: dict[str, object] = {
            "tenant_reference": self.fixture.case.policy.tenant_reference,
            "reconciliation_action_code": "resolve_exception",
            "reconciliation_run_id": self.opened["reconciliation_run_id"],
            "reconciliation_exception_id": str(self.exception_id),
            "reconciliation_idempotency_key": f"resolve-{self.exception_id}",
            "resolution_status_code": "resolved",
            "actor_reference": "urn:cwl:principal:independent_reviewer",
            "purpose_code": "bank_reconciliation_exception_review",
            "resolution_evidence_reference": (
                f"urn:cwl:evidence:reconciliation_exception:{self.exception_id}:review"
            ),
            "resolution_evidence_hash": _EVIDENCE_HASH,
            "effective_at": "2026-09-02T00:20:00Z",
        }
        command.update(overrides)
        return command

    def test_raw_terminal_status_without_resolution_command_fails(self) -> None:
        """Privileged SQL cannot manufacture maker-checker resolution authority."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaisesRegex(
                psycopg.Error,
                "reconciliation_exception_resolution_command_required",
            ):
                connection.execute(
                    """
                    UPDATE accounting_core.reconciliation_exception
                    SET resolution_status_code = 'resolved'
                    WHERE tenant_account_id = %s
                      AND reconciliation_exception_id = %s
                    """,
                    (self._tenant_id(connection), self.exception_id),
                )
            connection.rollback()

    def test_named_command_resolves_exception_and_emits_atomic_outbox(self) -> None:
        """One reviewed command persists terminal status, immutable evidence, and outbox."""
        result = resolve_reconciliation_exception(
            self._command(),
            posting.DATABASE_URL,
            self.fixture.case.policy.tenant_reference,
        )

        self.assertEqual(result["resolution_status_code"], "resolved")
        self.assertFalse(result["replayed"])
        self.assertRegex(
            str(result["reconciliation_exception_resolution_command_hash"]),
            r"^sha256:[0-9a-f]{64}$",
        )
        with psycopg.connect(posting.DATABASE_URL) as connection:
            status = connection.execute(
                """
                SELECT resolution_status_code
                FROM accounting_core.reconciliation_exception
                WHERE tenant_account_id = %s
                  AND reconciliation_exception_id = %s
                """,
                (self._tenant_id(connection), self.exception_id),
            ).fetchone()[0]
            outbox = connection.execute(
                """
                SELECT event_type_code, payload_hash
                FROM accounting_integration.outbox_event
                WHERE tenant_account_id = %s
                  AND aggregate_reference = %s
                ORDER BY created_at DESC, outbox_event_id DESC
                LIMIT 1
                """,
                (
                    self._tenant_id(connection),
                    f"urn:cwl:accounting:reconciliation_exception:{self.exception_id}",
                ),
            ).fetchone()
        self.assertEqual(status, "resolved")
        self.assertEqual(outbox[0], "reconciliation_exception_resolved")
        self.assertEqual(
            outbox[1], result["reconciliation_exception_resolution_command_hash"]
        )

    def test_exact_replay_reuses_immutable_resolution_receipt(self) -> None:
        """Exact retries replay while changed evidence under one key conflicts."""
        command = self._command()
        first = resolve_reconciliation_exception(
            command,
            posting.DATABASE_URL,
            self.fixture.case.policy.tenant_reference,
        )
        replay = resolve_reconciliation_exception(
            command,
            posting.DATABASE_URL,
            self.fixture.case.policy.tenant_reference,
        )
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(
            replay["reconciliation_exception_resolution_command_hash"],
            first["reconciliation_exception_resolution_command_hash"],
        )
        with self.assertRaises(IdempotencyConflictError):
            resolve_reconciliation_exception(
                self._command(resolution_evidence_hash="sha256:" + "b" * 64),
                posting.DATABASE_URL,
                self.fixture.case.policy.tenant_reference,
            )

    def test_exception_owner_cannot_resolve_own_exception(self) -> None:
        """Maker-checker separation rejects the exception owner as resolution actor."""
        with self.assertRaisesRegex(AccountingValidationError, "cannot approve"):
            resolve_reconciliation_exception(
                self._command(actor_reference="urn:cwl:principal:controller_owner"),
                posting.DATABASE_URL,
                self.fixture.case.policy.tenant_reference,
            )

    def test_resolved_exception_evidence_is_immutable(self) -> None:
        """The terminal exception fact cannot be rewritten after its command commits."""
        resolve_reconciliation_exception(
            self._command(),
            posting.DATABASE_URL,
            self.fixture.case.policy.tenant_reference,
        )
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaisesRegex(
                psycopg.Error,
                "reconciliation_exception_resolution_immutable",
            ):
                connection.execute(
                    """
                    UPDATE accounting_core.reconciliation_exception
                    SET next_action = 'rewrite forbidden'
                    WHERE tenant_account_id = %s
                      AND reconciliation_exception_id = %s
                    """,
                    (self._tenant_id(connection), self.exception_id),
                )
            connection.rollback()


if __name__ == "__main__":
    unittest.main()
