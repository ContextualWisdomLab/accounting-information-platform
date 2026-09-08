"""Unit regressions for reconciliation database-error normalization."""

from __future__ import annotations

import unittest

import psycopg

from accounting_information_platform.reconciliation_run import (
    _normalize_reconciliation_command_identity_conflicts,
)


class ReconciliationCommandIdentityPublicErrorTests(unittest.TestCase):
    """Keep unrelated PostgreSQL integrity errors outside idempotency translation."""

    def test_unrelated_unique_violation_is_not_masked(self) -> None:
        """Only the database-owned reconciliation identity marker becomes a domain conflict."""

        class _MarkerlessUniqueViolation(psycopg.errors.UniqueViolation):
            @property
            def sqlstate(self) -> str:
                """Exercise the marker-free SQLSTATE 23505 normalization branch."""
                return "23505"

        unrelated = _MarkerlessUniqueViolation(
            "unrelated accounting uniqueness invariant"
        )
        self.assertEqual(unrelated.sqlstate, "23505")

        @_normalize_reconciliation_command_identity_conflicts
        def command() -> dict[str, object]:
            """Raise an unrelated PostgreSQL uniqueness error through the shared boundary."""
            raise unrelated

        with self.assertRaises(psycopg.errors.UniqueViolation) as raised:
            command()
        self.assertIs(raised.exception, unrelated)


if __name__ == "__main__":
    unittest.main()
