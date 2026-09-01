"""Unit regressions for public reconciliation database-error normalization."""

from __future__ import annotations

import unittest
import unittest.mock as mock

import psycopg

import accounting_information_platform as accounting


class ReconciliationCommandIdentityPublicErrorTests(unittest.TestCase):
    """Keep unrelated PostgreSQL integrity errors outside idempotency translation."""

    def test_unrelated_unique_violation_is_not_masked(self) -> None:
        """Only the database-owned reconciliation identity marker becomes a domain conflict."""
        public_commands = (
            ("_accept_reconciliation_run", accounting.accept_reconciliation_run),
            ("_reconcile_reconciliation_run", accounting.reconcile_reconciliation_run),
        )
        for private_name, public_command in public_commands:
            with self.subTest(command=private_name):
                unrelated = psycopg.errors.UniqueViolation(
                    "unrelated accounting uniqueness invariant"
                )
                with mock.patch.object(
                    accounting,
                    private_name,
                    side_effect=unrelated,
                ):
                    with self.assertRaises(psycopg.errors.UniqueViolation) as raised:
                        public_command(object(), "postgresql://unused", "tenant-unused")
                self.assertIs(raised.exception, unrelated)


if __name__ == "__main__":
    unittest.main()
