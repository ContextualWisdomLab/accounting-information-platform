"""PostgreSQL retention invariants for reconciliation authority outbox evidence."""

from __future__ import annotations

import unittest
from unittest import mock

import psycopg

from accounting_information_platform import (
    reconcile_reconciliation_run,
    resolve_reconciliation_exception,
)
from accounting_information_platform import reconciliation_close_package as close_package
from tests import test_postgres_posting as posting
from tests.test_reconciliation_exception_resolution_postgres import (
    ReconciliationExceptionResolutionPostgresTests,
)
from tests.test_reconciliation_lifecycle_postgres import (
    ReconciliationLifecyclePostgresTests,
    _bridge,
)


class ReconciliationOutboxRetentionPostgresTests(unittest.TestCase):
    """Prove committed reconciliation authority keeps exactly one bound outbox event."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete checked-in PostgreSQL migration chain."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create one reviewed exception that can be resolved through the public command."""
        self.resolution_fixture = ReconciliationExceptionResolutionPostgresTests(
            "test_named_command_resolves_exception_and_emits_atomic_outbox"
        )
        self.resolution_fixture.setUp()
        self.addCleanup(self.resolution_fixture.doCleanups)
        self.addCleanup(self.resolution_fixture.tearDown)

    def _commit_resolution(self) -> tuple[object, object]:
        """Commit one valid resolution and return its tenant and outbox identities."""
        resolve_reconciliation_exception(
            self.resolution_fixture._command(),
            posting.DATABASE_URL,
            self.resolution_fixture.fixture.case.policy.tenant_reference,
        )
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = self.resolution_fixture._tenant_id(connection)
            outbox_event_id = connection.execute(
                """
                SELECT outbox_event_id
                FROM accounting_integration.outbox_event
                WHERE tenant_account_id = %s
                  AND aggregate_reference = %s
                ORDER BY created_at DESC, outbox_event_id DESC
                LIMIT 1
                """,
                (
                    tenant_id,
                    "urn:cwl:accounting:reconciliation_exception:"
                    f"{self.resolution_fixture.exception_id}",
                ),
            ).fetchone()[0]
        return tenant_id, outbox_event_id

    def _commit_lifecycle_transition(self) -> tuple[object, object]:
        """Commit one valid lifecycle transition and return its tenant and outbox identities."""
        lifecycle_fixture = ReconciliationLifecyclePostgresTests(
            "test_supported_command_persists_transition_outbox_and_freezes_review_state"
        )
        lifecycle_fixture.setUp()
        self.addCleanup(lifecycle_fixture.doCleanups)
        self.addCleanup(lifecycle_fixture.tearDown)
        bridge = _bridge(str(lifecycle_fixture.opened["reconciliation_run_id"]))
        with mock.patch.object(
            close_package,
            "_database_owned_close_projection_evidence",
            return_value=bridge,
        ):
            reconcile_reconciliation_run(
                lifecycle_fixture._command(),
                posting.DATABASE_URL,
                lifecycle_fixture.fixture.case.policy.tenant_reference,
            )
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = lifecycle_fixture._tenant_id(connection)
            outbox_event_id = connection.execute(
                """
                SELECT outbox_event_id
                FROM accounting_integration.outbox_event
                WHERE tenant_account_id = %s
                  AND aggregate_reference = %s
                  AND event_type_code = 'reconciliation_run_reconciled'
                ORDER BY created_at DESC, outbox_event_id DESC
                LIMIT 1
                """,
                (
                    tenant_id,
                    "urn:cwl:accounting:reconciliation_run:"
                    + str(lifecycle_fixture.opened["reconciliation_run_id"]),
                ),
            ).fetchone()[0]
        return tenant_id, outbox_event_id

    @staticmethod
    def _outbox_identity(
        connection: psycopg.Connection[object],
        tenant_id: object,
        outbox_event_id: object,
    ) -> tuple[str, str, str, str]:
        """Return the four linkage fields that bind one tenant-scoped outbox event."""
        row = connection.execute(
            """
            SELECT event_type_code,
                   aggregate_reference,
                   payload_reference,
                   payload_hash
            FROM accounting_integration.outbox_event
            WHERE tenant_account_id = %s
              AND outbox_event_id = %s
            """,
            (tenant_id, outbox_event_id),
        ).fetchone()
        assert row is not None
        return row

    def _assert_duplicate_insert_rejected(
        self,
        tenant_id: object,
        outbox_event_id: object,
    ) -> None:
        """Require a second exact authority event to fail at COMMIT."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            event_type, aggregate_reference, payload_reference, payload_hash = (
                self._outbox_identity(connection, tenant_id, outbox_event_id)
            )
            with self.assertRaisesRegex(
                psycopg.Error,
                "reconciliation_authority_outbox_retention",
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
                        event_type,
                        aggregate_reference,
                        payload_reference,
                        payload_hash,
                    ),
                )
                connection.commit()
            connection.rollback()

    def test_committed_resolution_outbox_evidence_cannot_be_deleted(self) -> None:
        """Deleting the event after a valid commit must fail at the database boundary."""
        tenant_id, outbox_event_id = self._commit_resolution()
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaisesRegex(
                psycopg.Error,
                "reconciliation_authority_outbox_retention",
            ):
                connection.execute(
                    """
                    DELETE FROM accounting_integration.outbox_event
                    WHERE tenant_account_id = %s
                      AND outbox_event_id = %s
                    """,
                    (tenant_id, outbox_event_id),
                )
                connection.commit()
            connection.rollback()

    def test_committed_resolution_outbox_identity_cannot_be_rekeyed(self) -> None:
        """Changing linkage fields after commit must not detach immutable authority evidence."""
        tenant_id, outbox_event_id = self._commit_resolution()
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaisesRegex(
                psycopg.Error,
                "reconciliation_authority_outbox_retention",
            ):
                connection.execute(
                    """
                    UPDATE accounting_integration.outbox_event
                    SET payload_reference = payload_reference || ':detached'
                    WHERE tenant_account_id = %s
                      AND outbox_event_id = %s
                    """,
                    (tenant_id, outbox_event_id),
                )
                connection.commit()
            connection.rollback()

    def test_committed_resolution_outbox_duplicate_cannot_be_inserted(self) -> None:
        """A second exact event must not make one resolution command ambiguous."""
        tenant_id, outbox_event_id = self._commit_resolution()
        self._assert_duplicate_insert_rejected(tenant_id, outbox_event_id)

    def test_unrelated_outbox_cannot_be_rekeyed_into_resolution_authority(self) -> None:
        """An unrelated event cannot be updated into a duplicate resolution identity."""
        tenant_id, outbox_event_id = self._commit_resolution()
        with psycopg.connect(posting.DATABASE_URL) as connection:
            event_type, aggregate_reference, payload_reference, payload_hash = (
                self._outbox_identity(connection, tenant_id, outbox_event_id)
            )
            unrelated_event_id = connection.execute(
                """
                INSERT INTO accounting_integration.outbox_event (
                    tenant_account_id,
                    event_type_code,
                    aggregate_reference,
                    payload_reference,
                    payload_hash
                )
                VALUES (
                    %s,
                    'reconciliation_retention_probe',
                    'urn:cwl:accounting:retention_probe:aggregate',
                    'urn:cwl:accounting:retention_probe:payload',
                    %s
                )
                RETURNING outbox_event_id
                """,
                (tenant_id, "sha256:" + "0" * 64),
            ).fetchone()[0]
            with self.assertRaisesRegex(
                psycopg.Error,
                "reconciliation_authority_outbox_retention",
            ):
                connection.execute(
                    """
                    UPDATE accounting_integration.outbox_event
                    SET event_type_code = %s,
                        aggregate_reference = %s,
                        payload_reference = %s,
                        payload_hash = %s
                    WHERE tenant_account_id = %s
                      AND outbox_event_id = %s
                    """,
                    (
                        event_type,
                        aggregate_reference,
                        payload_reference,
                        payload_hash,
                        tenant_id,
                        unrelated_event_id,
                    ),
                )
                connection.commit()
            connection.rollback()

    def test_committed_lifecycle_outbox_evidence_cannot_be_deleted(self) -> None:
        """Lifecycle authority retains the matching event after its successful commit."""
        tenant_id, outbox_event_id = self._commit_lifecycle_transition()
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaisesRegex(
                psycopg.Error,
                "reconciliation_authority_outbox_retention",
            ):
                connection.execute(
                    """
                    DELETE FROM accounting_integration.outbox_event
                    WHERE tenant_account_id = %s
                      AND outbox_event_id = %s
                    """,
                    (tenant_id, outbox_event_id),
                )
                connection.commit()
            connection.rollback()

    def test_committed_lifecycle_outbox_duplicate_cannot_be_inserted(self) -> None:
        """A second exact event must not make one lifecycle command ambiguous."""
        tenant_id, outbox_event_id = self._commit_lifecycle_transition()
        self._assert_duplicate_insert_rejected(tenant_id, outbox_event_id)

    def test_published_at_update_preserves_resolution_authority(self) -> None:
        """Publication metadata may advance without changing authority linkage."""
        tenant_id, outbox_event_id = self._commit_resolution()
        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute(
                """
                UPDATE accounting_integration.outbox_event
                SET published_at = clock_timestamp()
                WHERE tenant_account_id = %s
                  AND outbox_event_id = %s
                """,
                (tenant_id, outbox_event_id),
            )
            connection.commit()
            published_at = connection.execute(
                """
                SELECT published_at
                FROM accounting_integration.outbox_event
                WHERE tenant_account_id = %s
                  AND outbox_event_id = %s
                """,
                (tenant_id, outbox_event_id),
            ).fetchone()[0]
        self.assertIsNotNone(published_at)


if __name__ == "__main__":
    unittest.main()
