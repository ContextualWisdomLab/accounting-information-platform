"""Real PostgreSQL regression for tenant-owned transactional outbox evidence."""

from __future__ import annotations

import unittest
import uuid

import psycopg

from tests import test_postgres_posting as posting


class OutboxTenantInvariantTests(unittest.TestCase):
    """Require every authoritative outbox row to belong to one tenant."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def test_superuser_cannot_insert_outbox_event_without_tenant(self) -> None:
        """The database rejects orphan outbox evidence even when RLS can be bypassed."""
        aggregate_reference = f"urn:cwl:accounting:test-outbox:{uuid.uuid4()}"
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaises(psycopg.errors.NotNullViolation):
                connection.execute(
                    """
                    INSERT INTO accounting_integration.outbox_event (
                        event_type_code,
                        aggregate_reference,
                        payload_reference,
                        payload_hash
                    )
                    VALUES ('posting_receipt', %s, %s, %s)
                    """,
                    (
                        aggregate_reference,
                        f"{aggregate_reference}:payload",
                        "sha256:" + "a" * 64,
                    ),
                )
                connection.commit()
            connection.rollback()


if __name__ == "__main__":
    unittest.main()
