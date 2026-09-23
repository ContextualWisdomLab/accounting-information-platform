"""Real PostgreSQL RED for current Accounting Policy reference admission."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from accounting_information_platform import AccountingPolicy, AccountingValidationError

import tests.test_postgres_posting as postgres_posting


class _HostileReference(str):
    """Expose caller-owned behavior if a mutated policy reference escapes admission."""

    def __hash__(self) -> int:
        """Fail if a caller-owned reference reaches identity or cache hashing."""
        raise AssertionError("caller-defined policy-reference hashing executed")

    def __eq__(self, other: object) -> bool:
        """Fail if a caller-owned reference reaches retained/control equality."""
        raise AssertionError("caller-defined policy-reference equality executed")

    def __ne__(self, other: object) -> bool:
        """Fail if a caller-owned reference reaches retained/control inequality."""
        raise AssertionError("caller-defined policy-reference inequality executed")


class PostgresPolicyReferenceRuntimeDomainTests(unittest.TestCase):
    """Keep PostgreSQL posting behind current policy-reference admission."""

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

    def _policy_copy(self) -> AccountingPolicy:
        """Return a fresh canonical policy so mutation scenarios remain isolated."""
        policy = self.case.policy
        return AccountingPolicy(
            tenant_reference=policy.tenant_reference,
            legal_entity_reference=policy.legal_entity_reference,
            accounting_book_reference=policy.accounting_book_reference,
            intended_book_role_code=policy.intended_book_role_code,
            transaction_currency=policy.transaction_currency,
            functional_currency=policy.functional_currency,
            open_period_start=policy.open_period_start,
            open_period_end=policy.open_period_end,
            chart_account_mapping=dict(policy.chart_account_mapping),
            accounting_policy_version=policy.accounting_policy_version,
            posting_rule_version=policy.posting_rule_version,
        )

    def _assert_no_posting_rows(self) -> None:
        """Require an invalid policy to retain no posting or integration authority."""
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

    def _assert_one_posting_population(self) -> None:
        """Require replay rejection to leave the original durable population unchanged."""
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

    def test_first_post_revalidates_current_policy_references_before_session(self) -> None:
        """Mutated policy identity fails before PostgreSQL session or durable writes."""
        fields = (
            ("tenant_reference", "tenant reference"),
            ("legal_entity_reference", "legal entity reference"),
            ("accounting_book_reference", "accounting book reference"),
        )
        proposal = self.case._two_line_proposal()

        for field_name, label in fields:
            with self.subTest(field_name=field_name):
                policy = self._policy_copy()
                original = getattr(policy, field_name)
                object.__setattr__(policy, field_name, _HostileReference(original))

                with patch.object(
                    self.case.ledger,
                    "_session",
                    side_effect=AssertionError(
                        "PostgreSQL session opened before current policy-reference admission"
                    ),
                ) as session:
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        f"{label} must be a CWL URN",
                    ):
                        self.case.ledger.post(proposal, policy)

                session.assert_not_called()
                self._assert_no_posting_rows()

    def test_replay_revalidates_current_policy_references_before_session(self) -> None:
        """A retained receipt must not hide mutation of current policy scope identity."""
        proposal = self.case._two_line_proposal()
        first = self.case.ledger.post(proposal, self.case.policy)
        self.assertEqual(self.case.ledger.post(proposal, self.case.policy), first)
        self._assert_one_posting_population()

        fields = (
            ("tenant_reference", "tenant reference"),
            ("legal_entity_reference", "legal entity reference"),
            ("accounting_book_reference", "accounting book reference"),
        )
        for field_name, label in fields:
            with self.subTest(field_name=field_name):
                policy = self._policy_copy()
                original = getattr(policy, field_name)
                object.__setattr__(policy, field_name, _HostileReference(original))

                with patch.object(
                    self.case.ledger,
                    "_session",
                    side_effect=AssertionError(
                        "PostgreSQL session opened before replay policy-reference admission"
                    ),
                ) as session:
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        f"{label} must be a CWL URN",
                    ):
                        self.case.ledger.post(proposal, policy)

                session.assert_not_called()
                self._assert_one_posting_population()

    def test_exact_builtin_policy_references_preserve_post_and_replay(self) -> None:
        """Canonical built-in policy references retain normal PostgreSQL idempotency."""
        proposal = self.case._two_line_proposal()

        first = self.case.ledger.post(proposal, self.case.policy)
        replay = self.case.ledger.post(proposal, self.case.policy)

        self.assertEqual(replay, first)
        self._assert_one_posting_population()


if __name__ == "__main__":
    unittest.main()
