"""PostgreSQL admission invariants for reconciliation authority outbox events."""

from __future__ import annotations

import unittest

import psycopg

from tests import test_postgres_posting as posting
from tests.test_reconciliation_exception_resolution_postgres import (
    ReconciliationExceptionResolutionPostgresTests,
)


class ReconciliationOutboxOrphanAuthorityPostgresTests(unittest.TestCase):
    """Reject authority-shaped events that have no matching immutable command."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete checked-in PostgreSQL migration chain."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one tenant/run/exception scope without terminal authority commands."""
        self.fixture = ReconciliationExceptionResolutionPostgresTests(
            "test_named_command_resolves_exception_and_emits_atomic_outbox"
        )
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)

    def _tenant_id(self, connection: psycopg.Connection[object]) -> object:
        """Resolve the tenant identity for the open reconciliation aggregate."""
        return self.fixture._tenant_id(connection)

    def _assert_orphan_event_rejected(
        self,
        *,
        event_type_code: str,
        aggregate_reference: str,
        payload_reference: str,
    ) -> None:
        """Require a reserved reconciliation authority event to have command authority."""
        payload_hash = "sha256:" + "f" * 64
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = self._tenant_id(connection)
            with self.assertRaisesRegex(
                psycopg.Error,
                "reconciliation_authority_outbox_orphan",
            ):
                connection.execute(
                    """
                    INSERT INTO accounting_integration.outbox_event (
                        tenant_account_id,
                        event_type_code,
                        aggregate_reference,
                        payload_reference,
                        payload_hash
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        tenant_id,
                        event_type_code,
                        aggregate_reference,
                        payload_reference,
                        payload_hash,
                    ),
                )
                connection.commit()
            connection.rollback()

            retained = connection.execute(
                """
                SELECT count(*)
                FROM accounting_integration.outbox_event
                WHERE tenant_account_id = %s
                  AND event_type_code = %s
                  AND aggregate_reference = %s
                  AND payload_reference = %s
                  AND payload_hash = %s
                """,
                (
                    tenant_id,
                    event_type_code,
                    aggregate_reference,
                    payload_reference,
                    payload_hash,
                ),
            ).fetchone()[0]
        self.assertEqual(retained, 0)

    def test_resolution_event_without_resolution_command_cannot_commit(self) -> None:
        """A fabricated resolution event cannot become accounting authority by itself."""
        self._assert_orphan_event_rejected(
            event_type_code="reconciliation_exception_resolved",
            aggregate_reference=(
                "urn:cwl:accounting:reconciliation_exception:"
                f"{self.fixture.exception_id}"
            ),
            payload_reference=(
                "urn:cwl:accounting:reconciliation_exception_resolution:"
                "00000000-0000-0000-0000-000000000047"
            ),
        )

    def test_lifecycle_event_without_transition_command_cannot_commit(self) -> None:
        """A fabricated reconciled-run event cannot become accounting authority by itself."""
        self._assert_orphan_event_rejected(
            event_type_code="reconciliation_run_reconciled",
            aggregate_reference=(
                "urn:cwl:accounting:reconciliation_run:"
                f"{self.fixture.opened['reconciliation_run_id']}"
            ),
            payload_reference=(
                "urn:cwl:accounting:reconciliation_run_transition:"
                "00000000-0000-0000-0000-000000000047"
            ),
        )


if __name__ == "__main__":
    unittest.main()
