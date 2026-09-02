"""Public diagnostic contract for reconciliation recording-time authority."""

from __future__ import annotations

import unittest
from unittest import mock

import psycopg

from accounting_information_platform import AccountingValidationError
from accounting_information_platform import reconciliation_exception_resolution as resolution
from tests.test_reconciliation_exception_resolution import _TENANT, _command


class ReconciliationRecordingTimePublicDiagnosticTests(unittest.TestCase):
    """Keep database provenance failures inside the accounting domain boundary."""

    def test_recording_time_authority_check_violation_becomes_actionable_domain_error(self) -> None:
        """The public command must not leak the provider-specific PostgreSQL error."""
        database_error = psycopg.errors.CheckViolation(
            "exception resolution requires database-owned chronology "
            "(reconciliation_resolution_recording_time_authority_required)"
        )
        with mock.patch.object(
            resolution,
            "_resolve_reconciliation_exception_once",
            side_effect=database_error,
        ):
            with self.assertRaisesRegex(
                AccountingValidationError,
                "database-owned system-time",
            ) as raised:
                resolution.resolve_reconciliation_exception(
                    _command(),
                    "postgresql://example",
                    _TENANT,
                )

        self.assertIs(raised.exception.__cause__, database_error)


if __name__ == "__main__":
    unittest.main()
