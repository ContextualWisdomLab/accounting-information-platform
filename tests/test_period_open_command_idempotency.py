"""Regression tests for fiscal-period-open command identity and durable replay."""

from __future__ import annotations

import unittest
from unittest import mock

from accounting_information_platform import AccountingValidationError, IdempotencyConflictError
from accounting_information_platform.persistence import PostgresPostingLedger
from accounting_information_platform.accept import accept_period_open
from tests import test_postgres_posting as posting


class PeriodOpenCommandBoundaryTests(unittest.TestCase):
    """Require explicit immutable command evidence before period-open database work."""

    def test_period_open_requires_idempotency_key_before_database_work(self) -> None:
        """A period-open write without a command key fails before the adapter is created."""
        tenant_reference = "urn:cwl:tenant_period_open_command"
        payload = {
            "tenant_reference": tenant_reference,
            "legal_entity_reference": "urn:cwl:legal_entity:period_open_command",
            "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-09",
            "period_start_date": "2026-09-01",
            "period_end_date": "2026-09-30",
            "source_payload_hash": "sha256:" + "a" * 64,
        }
        with mock.patch(
            "accounting_information_platform.accept.PostgresPostingLedger"
        ) as ledger_type:
            with self.assertRaisesRegex(AccountingValidationError, "idempotency_key"):
                accept_period_open(
                    payload,
                    "postgresql://unused.example.invalid/accounting",
                    tenant_reference,
                )
            ledger_type.assert_not_called()

    def test_period_open_requires_source_hash_before_database_work(self) -> None:
        """A period-open write without immutable payload identity fails before DB access."""
        tenant_reference = "urn:cwl:tenant_period_open_command"
        payload = {
            "tenant_reference": tenant_reference,
            "legal_entity_reference": "urn:cwl:legal_entity:period_open_command",
            "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-09",
            "period_start_date": "2026-09-01",
            "period_end_date": "2026-09-30",
            "idempotency_key": "period-open-command-v1",
        }
        with mock.patch(
            "accounting_information_platform.accept.PostgresPostingLedger"
        ) as ledger_type:
            with self.assertRaisesRegex(AccountingValidationError, "source_payload_hash"):
                accept_period_open(
                    payload,
                    "postgresql://unused.example.invalid/accounting",
                    tenant_reference,
                )
            ledger_type.assert_not_called()


class PeriodOpenPersistenceBoundaryTests(unittest.TestCase):
    """Keep direct adapter calls on the same immutable command boundary."""

    def test_direct_adapter_rejects_empty_command_key_before_database_work(self) -> None:
        """The durable adapter rejects an empty period-open command key before sessions."""
        ledger = PostgresPostingLedger(
            "postgresql://unused.example.invalid/accounting",
            "urn:cwl:tenant_period_open_command",
        )
        with mock.patch.object(ledger, "_session") as session:
            with self.assertRaisesRegex(AccountingValidationError, "idempotency_key"):
                ledger.open_fiscal_period(
                    "urn:cwl:legal_entity:period_open_command",
                    "2026-09",
                    idempotency_key=" ",
                    source_payload_hash="sha256:" + "a" * 64,
                )
            session.assert_not_called()

    def test_direct_adapter_rejects_invalid_source_hash_before_database_work(self) -> None:
        """The durable adapter rejects malformed source evidence before sessions."""
        ledger = PostgresPostingLedger(
            "postgresql://unused.example.invalid/accounting",
            "urn:cwl:tenant_period_open_command",
        )
        with mock.patch.object(ledger, "_session") as session:
            with self.assertRaisesRegex(AccountingValidationError, "source_payload_hash"):
                ledger.open_fiscal_period(
                    "urn:cwl:legal_entity:period_open_command",
                    "2026-09",
                    idempotency_key="period-open-command-v1",
                    source_payload_hash="sha256:not-a-digest",
                )
            session.assert_not_called()


class PostgresPeriodOpenCommandIdempotencyTests(unittest.TestCase):
    """Prove period-open replay and conflict semantics on real PostgreSQL state."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def test_same_period_open_command_replays_and_changed_hash_conflicts(self) -> None:
        """Only the same tenant/key/hash may replay an already-open fiscal period."""
        payload = self.case._period_open_payload(
            idempotency_key="period-open-command-v1",
            source_payload_hash="sha256:" + "b" * 64,
        )

        first = accept_period_open(
            payload,
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        replay = accept_period_open(
            payload,
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )

        self.assertFalse(bool(first["replayed"]))
        self.assertTrue(bool(replay["replayed"]))
        self.assertEqual(
            {key: value for key, value in first.items() if key != "replayed"},
            {key: value for key, value in replay.items() if key != "replayed"},
        )

        with self.assertRaisesRegex(IdempotencyConflictError, "different payload"):
            accept_period_open(
                {**payload, "source_payload_hash": "sha256:" + "c" * 64},
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
            )

        with self.assertRaisesRegex(IdempotencyConflictError, "different payload"):
            accept_period_open(
                {**payload, "period_end_date": "2026-10-01"},
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
            )

        second_key = accept_period_open(
            {**payload, "idempotency_key": "period-open-command-v2"},
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.assertTrue(bool(second_key["replayed"]))

        with self.assertRaisesRegex(AccountingValidationError, "dates do not match"):
            accept_period_open(
                {
                    **payload,
                    "idempotency_key": "period-open-command-v3",
                    "period_end_date": "2026-10-01",
                    "source_payload_hash": "sha256:" + "d" * 64,
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
            )


if __name__ == "__main__":
    unittest.main()
