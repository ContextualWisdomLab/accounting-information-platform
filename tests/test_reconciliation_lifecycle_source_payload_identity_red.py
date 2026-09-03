"""RED contract for reconciliation lifecycle source-payload idempotency."""

from __future__ import annotations

import unittest
from unittest import mock

from accounting_information_platform import IdempotencyConflictError
from accounting_information_platform import reconciliation_lifecycle as lifecycle
from tests.test_reconciliation_lifecycle import (
    _EFFECTIVE_AT,
    _Ledger,
    _RUN_ID,
    _TENANT,
    _command,
)


class ReconciliationLifecycleSourcePayloadIdentityRedTests(unittest.TestCase):
    """Require one idempotency key to identify the complete received lifecycle command."""

    def test_changed_ignored_payload_member_cannot_replay_original_transition(self) -> None:
        """A materially changed JSON command must conflict even when core fields match."""
        _Ledger.connection = type(_Ledger.connection)()
        _Ledger.locks = []
        _Ledger.connection.prior_transition = (
            _RUN_ID,
            "urn:cwl:principal:controller",
            "month_end_reconciliation",
            _EFFECTIVE_AT,
        )
        changed_command = _command(request_context={"review_batch": "changed"})

        with mock.patch.object(lifecycle, "PostgresPostingLedger", _Ledger):
            with self.assertRaises(IdempotencyConflictError):
                lifecycle.reconcile_reconciliation_run(
                    changed_command,
                    "postgresql://example",
                    _TENANT,
                )


if __name__ == "__main__":
    unittest.main()
