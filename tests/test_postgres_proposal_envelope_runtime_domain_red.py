"""Real PostgreSQL RED for proposal-envelope admission before posting replay."""

from __future__ import annotations

import unittest

from accounting_information_platform import AccountingValidationError

import tests.test_postgres_posting as postgres_posting


class PostgresProposalEnvelopeRuntimeDomainTests(unittest.TestCase):
    """Keep PostgreSQL posting behind current proposal-envelope admission."""

    @classmethod
    def setUpClass(cls) -> None:
        """Apply the same real PostgreSQL foundation used by posting integration tests."""
        postgres_posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Seed one isolated tenant, legal entity, book, period, and chart catalog."""
        self.case = postgres_posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def _reject_command_lock(self) -> list[str]:
        """Fail if an invalid current envelope reaches PostgreSQL serialization."""
        lock_calls: list[str] = []

        def unexpected_lock(_connection: object, command_scope: str) -> None:
            lock_calls.append(command_scope)
            raise AssertionError("command lock acquired before proposal-envelope admission")

        self.case.ledger._acquire_command_lock = unexpected_lock
        return lock_calls

    def test_first_post_revalidates_current_contract_version_before_lock(self) -> None:
        """A mutated contract version fails before command lock or durable writes."""
        proposal = self.case._two_line_proposal()
        object.__setattr__(proposal, "proposal_contract_version", True)
        lock_calls = self._reject_command_lock()

        with self.assertRaisesRegex(
            AccountingValidationError,
            "proposal_contract_version must be a positive integer",
        ):
            self.case.ledger.post(proposal, self.case.policy)

        self.assertEqual(lock_calls, [])
        self.assertEqual(self.case.ledger.journal_count, 0)
        self.assertEqual(
            self.case._count_table("accounting_integration.journal_proposal_record"),
            0,
        )
        self.assertEqual(self.case._count_table("accounting_core.general_journal"), 0)
        self.assertEqual(self.case._count_table("accounting_core.journal_entry_line"), 0)
        self.assertEqual(
            self.case._count_table("accounting_integration.posting_receipt"), 0
        )
        self.assertEqual(self.case._count_table("accounting_integration.outbox_event"), 0)

    def test_replay_revalidates_current_contract_version_before_cached_receipt(self) -> None:
        """A mutated replay fails before command lock or authoritative receipt lookup."""
        proposal = self.case._two_line_proposal()
        first = self.case.ledger.post(proposal, self.case.policy)
        self.assertEqual(self.case.ledger.post(proposal, self.case.policy), first)

        object.__setattr__(proposal, "proposal_contract_version", True)
        lock_calls = self._reject_command_lock()

        with self.assertRaisesRegex(
            AccountingValidationError,
            "proposal_contract_version must be a positive integer",
        ):
            self.case.ledger.post(proposal, self.case.policy)

        self.assertEqual(lock_calls, [])
        self.assertEqual(self.case.ledger.journal_count, 1)
        self.assertEqual(
            self.case._count_table("accounting_integration.journal_proposal_record"),
            1,
        )
        self.assertEqual(self.case._count_table("accounting_core.general_journal"), 1)
        self.assertEqual(self.case._count_table("accounting_core.journal_entry_line"), 2)
        self.assertEqual(
            self.case._count_table("accounting_integration.posting_receipt"), 1
        )
        self.assertEqual(self.case._count_table("accounting_integration.outbox_event"), 1)

    def test_first_post_revalidates_current_idempotency_key_before_lock(self) -> None:
        """Envelope admission is not satisfied by checking contract version alone."""
        proposal = self.case._two_line_proposal()
        object.__setattr__(proposal, "idempotency_key", "")
        lock_calls = self._reject_command_lock()

        with self.assertRaisesRegex(
            AccountingValidationError,
            "idempotency_key must be a non-empty string",
        ):
            self.case.ledger.post(proposal, self.case.policy)

        self.assertEqual(lock_calls, [])
        self.assertEqual(self.case.ledger.journal_count, 0)
        self.assertEqual(
            self.case._count_table("accounting_integration.journal_proposal_record"),
            0,
        )
        self.assertEqual(self.case._count_table("accounting_core.general_journal"), 0)
        self.assertEqual(self.case._count_table("accounting_core.journal_entry_line"), 0)
        self.assertEqual(
            self.case._count_table("accounting_integration.posting_receipt"), 0
        )
        self.assertEqual(self.case._count_table("accounting_integration.outbox_event"), 0)

    def test_replay_revalidates_current_idempotency_key_before_cached_receipt(self) -> None:
        """A changed envelope key cannot bypass current admission through replay."""
        proposal = self.case._two_line_proposal()
        first = self.case.ledger.post(proposal, self.case.policy)
        self.assertEqual(self.case.ledger.post(proposal, self.case.policy), first)

        object.__setattr__(proposal, "idempotency_key", "")
        lock_calls = self._reject_command_lock()

        with self.assertRaisesRegex(
            AccountingValidationError,
            "idempotency_key must be a non-empty string",
        ):
            self.case.ledger.post(proposal, self.case.policy)

        self.assertEqual(lock_calls, [])
        self.assertEqual(self.case.ledger.journal_count, 1)
        self.assertEqual(
            self.case._count_table("accounting_integration.journal_proposal_record"),
            1,
        )
        self.assertEqual(self.case._count_table("accounting_core.general_journal"), 1)
        self.assertEqual(self.case._count_table("accounting_core.journal_entry_line"), 2)
        self.assertEqual(
            self.case._count_table("accounting_integration.posting_receipt"), 1
        )
        self.assertEqual(self.case._count_table("accounting_integration.outbox_event"), 1)

    def test_first_post_revalidates_current_source_payload_hash_before_lock(self) -> None:
        """Current provenance must be admitted before PostgreSQL serialization."""
        proposal = self.case._two_line_proposal()
        object.__setattr__(proposal, "source_payload_hash", "sha256:not-canonical")
        lock_calls = self._reject_command_lock()

        with self.assertRaisesRegex(
            AccountingValidationError,
            "source_payload_hash must be canonical sha256",
        ):
            self.case.ledger.post(proposal, self.case.policy)

        self.assertEqual(lock_calls, [])
        self.assertEqual(self.case.ledger.journal_count, 0)
        self.assertEqual(
            self.case._count_table("accounting_integration.journal_proposal_record"),
            0,
        )
        self.assertEqual(self.case._count_table("accounting_core.general_journal"), 0)
        self.assertEqual(self.case._count_table("accounting_core.journal_entry_line"), 0)
        self.assertEqual(
            self.case._count_table("accounting_integration.posting_receipt"), 0
        )
        self.assertEqual(self.case._count_table("accounting_integration.outbox_event"), 0)

    def test_replay_revalidates_current_source_payload_hash_before_cached_receipt(self) -> None:
        """Invalid current provenance cannot be hidden by retained-receipt replay."""
        proposal = self.case._two_line_proposal()
        first = self.case.ledger.post(proposal, self.case.policy)
        self.assertEqual(self.case.ledger.post(proposal, self.case.policy), first)

        object.__setattr__(proposal, "source_payload_hash", "sha256:not-canonical")
        lock_calls = self._reject_command_lock()

        with self.assertRaisesRegex(
            AccountingValidationError,
            "source_payload_hash must be canonical sha256",
        ):
            self.case.ledger.post(proposal, self.case.policy)

        self.assertEqual(lock_calls, [])
        self.assertEqual(self.case.ledger.journal_count, 1)
        self.assertEqual(
            self.case._count_table("accounting_integration.journal_proposal_record"),
            1,
        )
        self.assertEqual(self.case._count_table("accounting_core.general_journal"), 1)
        self.assertEqual(self.case._count_table("accounting_core.journal_entry_line"), 2)
        self.assertEqual(
            self.case._count_table("accounting_integration.posting_receipt"), 1
        )
        self.assertEqual(self.case._count_table("accounting_integration.outbox_event"), 1)

    def test_first_post_revalidates_current_source_event_population_before_lock(self) -> None:
        """Current source-event population must be admitted before command serialization."""
        proposal = self.case._two_line_proposal()
        object.__setattr__(proposal, "source_event_references", ())
        lock_calls = self._reject_command_lock()

        with self.assertRaisesRegex(
            AccountingValidationError,
            "at least one source event reference is required",
        ):
            self.case.ledger.post(proposal, self.case.policy)

        self.assertEqual(lock_calls, [])
        self.assertEqual(self.case.ledger.journal_count, 0)
        self.assertEqual(
            self.case._count_table("accounting_integration.journal_proposal_record"),
            0,
        )
        self.assertEqual(self.case._count_table("accounting_core.general_journal"), 0)
        self.assertEqual(self.case._count_table("accounting_core.journal_entry_line"), 0)
        self.assertEqual(
            self.case._count_table("accounting_integration.posting_receipt"), 0
        )
        self.assertEqual(self.case._count_table("accounting_integration.outbox_event"), 0)

    def test_replay_revalidates_current_source_event_population_before_cached_receipt(self) -> None:
        """Invalid current source events cannot be hidden by retained-receipt replay."""
        proposal = self.case._two_line_proposal()
        first = self.case.ledger.post(proposal, self.case.policy)
        self.assertEqual(self.case.ledger.post(proposal, self.case.policy), first)

        object.__setattr__(proposal, "source_event_references", ())
        lock_calls = self._reject_command_lock()

        with self.assertRaisesRegex(
            AccountingValidationError,
            "at least one source event reference is required",
        ):
            self.case.ledger.post(proposal, self.case.policy)

        self.assertEqual(lock_calls, [])
        self.assertEqual(self.case.ledger.journal_count, 1)
        self.assertEqual(
            self.case._count_table("accounting_integration.journal_proposal_record"),
            1,
        )
        self.assertEqual(self.case._count_table("accounting_core.general_journal"), 1)
        self.assertEqual(self.case._count_table("accounting_core.journal_entry_line"), 2)
        self.assertEqual(
            self.case._count_table("accounting_integration.posting_receipt"), 1
        )
        self.assertEqual(self.case._count_table("accounting_integration.outbox_event"), 1)

    def test_unchanged_exact_contract_version_replays_normally(self) -> None:
        """Exact built-in positive contract versions preserve idempotent replay."""
        proposal = self.case._two_line_proposal()

        first = self.case.ledger.post(proposal, self.case.policy)
        replay = self.case.ledger.post(proposal, self.case.policy)

        self.assertEqual(replay, first)
        self.assertEqual(self.case.ledger.journal_count, 1)
        self.assertEqual(
            self.case._count_table("accounting_integration.journal_proposal_record"),
            1,
        )
        self.assertEqual(self.case._count_table("accounting_core.general_journal"), 1)
        self.assertEqual(self.case._count_table("accounting_core.journal_entry_line"), 2)
        self.assertEqual(
            self.case._count_table("accounting_integration.posting_receipt"), 1
        )
        self.assertEqual(self.case._count_table("accounting_integration.outbox_event"), 1)


if __name__ == "__main__":
    unittest.main()
