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
        unrelated = psycopg.errors.UniqueViolation(
            "unrelated accounting uniqueness invariant"
        )

        @_normalize_reconciliation_command_identity_conflicts
        def command() -> dict[str, object]:
            """Raise an unrelated PostgreSQL uniqueness error through the shared boundary."""
            raise unrelated

        with self.assertRaises(psycopg.errors.UniqueViolation) as raised:
            command()
        self.assertIs(raised.exception, unrelated)


if __name__ == "__main__":
    unittest.main()
