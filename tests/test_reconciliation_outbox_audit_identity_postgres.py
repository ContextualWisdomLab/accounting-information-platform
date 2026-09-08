"""PostgreSQL immutability regressions for reconciliation outbox audit identity."""

from __future__ import annotations

import unittest
from uuid import uuid4

import psycopg

from tests import test_postgres_posting as posting
from tests.test_reconciliation_outbox_retention_postgres import (
    ReconciliationOutboxRetentionPostgresTests,
)


class ReconciliationOutboxAuditIdentityPostgresTests(unittest.TestCase):
    """Prove committed reconciliation outbox audit identity cannot be rewritten."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete checked-in PostgreSQL migration chain."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one committed reconciliation exception-resolution outbox event."""
        self.fixture = ReconciliationOutboxRetentionPostgresTests(
            "test_published_at_update_preserves_resolution_authority"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)

    def test_committed_resolution_outbox_created_at_cannot_be_rewritten(self) -> None:
        """A privileged writer cannot rewrite the authority event audit timestamp."""
        tenant_id, outbox_event_id = self.fixture._commit_resolution()
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaisesRegex(
                psycopg.Error,
                "reconciliation_authority_outbox_audit_identity",
            ):
                connection.execute(
                    """
                    UPDATE accounting_integration.outbox_event
                    SET created_at = created_at + interval '1 second'
                    WHERE tenant_account_id = %s
                      AND outbox_event_id = %s
                    """,
                    (tenant_id, outbox_event_id),
                )
                connection.commit()
            connection.rollback()

    def test_committed_resolution_outbox_event_id_cannot_be_rewritten(self) -> None:
        """A privileged writer cannot replace the retained event surrogate identity."""
        tenant_id, outbox_event_id = self.fixture._commit_resolution()
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaisesRegex(
                psycopg.Error,
                "reconciliation_authority_outbox_audit_identity",
            ):
                connection.execute(
                    """
                    UPDATE accounting_integration.outbox_event
                    SET outbox_event_id = %s
                    WHERE tenant_account_id = %s
                      AND outbox_event_id = %s
                    """,
                    (uuid4(), tenant_id, outbox_event_id),
                )
                connection.commit()
            connection.rollback()


if __name__ == "__main__":
    unittest.main()
