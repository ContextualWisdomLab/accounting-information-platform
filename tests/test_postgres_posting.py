"""Realistic PostgreSQL posting tests against the foundation migration."""

from __future__ import annotations

import json
import os
import unittest
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

from accounting_information_platform import (
    AccountingPolicy,
    AccountingValidationError,
    IdempotencyConflictError,
    JournalLineProposal,
    JournalProposal,
    PeriodCloseReceipt,
    PostgresPostingLedger,
    ingest_journal_proposal,
)
import psycopg

from accounting_information_platform.persistence import apply_foundation_migration


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "database/migrations/0001_accounting_foundation.sql"
DATABASE_URL = os.environ.get(
    "ACCOUNTING_DATABASE_URL",
    "postgresql://postgres:postgres@127.0.0.1:5432/accounting_test",
)
VALID_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)


class PostgresPostingTests(unittest.TestCase):
    """Post, replay, reverse, and reject against a real PostgreSQL 18 catalog."""

    @classmethod
    def setUpClass(cls) -> None:
        with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
            connection.execute("DROP SCHEMA IF EXISTS accounting_reporting CASCADE")
            connection.execute("DROP SCHEMA IF EXISTS accounting_integration CASCADE")
            connection.execute("DROP SCHEMA IF EXISTS accounting_core CASCADE")
        apply_foundation_migration(DATABASE_URL, MIGRATION_PATH)

    def setUp(self) -> None:
        suffix = uuid.uuid4().hex[:8]
        self.policy = AccountingPolicy(
            tenant_reference=f"urn:cwl:tenant_{suffix}",
            legal_entity_reference=f"urn:cwl:legal_entity:entity_{suffix}",
            accounting_book_reference=f"urn:cwl:accounting_book:primary_statutory_{suffix}",
            intended_book_role_code="primary_statutory",
            transaction_currency="KRW",
            functional_currency="KRW",
            open_period_start=date(2026, 8, 1),
            open_period_end=date(2026, 8, 31),
            chart_account_mapping={
                "accounts_receivable": "110100",
                "usage_revenue": "410100",
            },
            accounting_policy_version="ifrs-v1",
            posting_rule_version="billing-issued-v1",
        )
        self.tenant_id = self._seed_master_data(period_status_code="open")
        self.ledger = PostgresPostingLedger(
            DATABASE_URL, tenant_reference=self.policy.tenant_reference
        )

    def test_posts_balanced_two_line_journal_and_ties_trial_balance(self) -> None:
        """A posted two-line journal is durable and ties to the journal population."""
        proposal = self._two_line_proposal()

        receipt = self.ledger.post(proposal, self.policy)
        balances = self.ledger.trial_balance(
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            date(2026, 8, 31),
        )
        population = self._journal_population_totals()

        self.assertEqual(receipt.posting_status_code, "posted")
        self.assertEqual(receipt.line_count, 2)
        self.assertEqual(self.ledger.journal_count, 1)
        self.assertEqual(self._count_table("accounting_core.general_journal"), 1)
        self.assertEqual(self._count_table("accounting_core.journal_entry_line"), 2)
        self.assertEqual(self._count_table("accounting_integration.posting_receipt"), 1)
        self.assertEqual(self._count_table("accounting_integration.outbox_event"), 1)
        self.assertEqual(balances["110100"].debit_total, Decimal("25000"))
        self.assertEqual(balances["410100"].credit_total, Decimal("25000"))
        self.assertEqual(balances["110100"].debit_total, population["110100"][0])
        self.assertEqual(balances["410100"].credit_total, population["410100"][1])
        self.assertEqual(
            sum(balance.debit_total for balance in balances.values()),
            sum(balance.credit_total for balance in balances.values()),
        )

    def test_replay_of_same_idempotency_key_does_not_duplicate_rows(self) -> None:
        """Exact replay returns the original receipt and writes no second journal."""
        proposal = self._two_line_proposal()

        first = self.ledger.post(proposal, self.policy)
        second = self.ledger.post(proposal, self.policy)

        self.assertEqual(first, second)
        self.assertEqual(self.ledger.journal_count, 1)
        self.assertEqual(self._count_table("accounting_core.general_journal"), 1)
        self.assertEqual(self._count_table("accounting_core.journal_entry_line"), 2)

    def test_reverse_preserves_original_and_zeroes_trial_balance(self) -> None:
        """Reversal is append-only and the selected population nets to zero."""
        receipt = self.ledger.post(self._two_line_proposal(), self.policy)

        reversal = self.ledger.reverse(
            receipt.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
        )
        replayed = self.ledger.reverse(
            receipt.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
        )
        balances = self.ledger.trial_balance(
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            date(2026, 8, 31),
        )
        population = self._journal_population_totals()

        self.assertEqual(reversal, replayed)
        self.assertEqual(reversal.reversal_of_journal_reference, receipt.journal_reference)
        self.assertEqual(self.ledger.journal_count, 2)
        self.assertEqual(self._count_table("accounting_core.journal_reversal"), 1)
        self.assertEqual(self._count_table("accounting_core.general_journal"), 2)
        self.assertEqual(self._original_journal_status(receipt.journal_reference), "posted")
        for account_code, balance in balances.items():
            self.assertEqual(balance.net_balance, Decimal("0"))
            self.assertEqual(balance.debit_total, population[account_code][0])
            self.assertEqual(balance.credit_total, population[account_code][1])

    def test_closed_period_posts_zero_rows(self) -> None:
        """A hard-closed fiscal period rejects posting before any durable write."""
        self._set_period_status("hard_closed")
        proposal = self._two_line_proposal()

        with self.assertRaisesRegex(
            AccountingValidationError,
            "Open that period or post into an open period",
        ):
            self.ledger.post(proposal, self.policy)

        self.assertEqual(self._count_table("accounting_integration.journal_proposal_record"), 0)
        self.assertEqual(self._count_table("accounting_core.general_journal"), 0)
        self.assertEqual(self._count_table("accounting_core.journal_entry_line"), 0)
        self.assertEqual(self._count_table("accounting_integration.posting_receipt"), 0)
        self.assertEqual(self.ledger.journal_count, 0)

    def test_conflicting_idempotency_and_missing_master_data_fail_closed(self) -> None:
        """Payload conflicts and missing catalog rows tell the operator the next action."""
        proposal = self._two_line_proposal()
        self.ledger.post(proposal, self.policy)
        changed = self._two_line_proposal(
            proposal_id=str(uuid.uuid4()),
            source_payload_hash="sha256:" + "b" * 64,
        )
        with self.assertRaises(IdempotencyConflictError):
            self.ledger.post(changed, self.policy)

        missing_tenant = PostgresPostingLedger(
            DATABASE_URL, tenant_reference="urn:cwl:tenant_missing_row"
        )
        with self.assertRaisesRegex(AccountingValidationError, "Create the tenant_account row"):
            missing_tenant.post(self._two_line_proposal(), self.policy)

        with self.assertRaisesRegex(AccountingValidationError, "proposal_id must be a UUID"):
            self.ledger.post(self._two_line_proposal(proposal_id="not-a-uuid"), self.policy)

        with self.assertRaisesRegex(AccountingValidationError, "Create the legal_entity_record"):
            self.ledger.post(
                self._two_line_proposal(
                    proposal_id=str(uuid.uuid4()),
                    idempotency_key="missing-legal-entity",
                    legal_entity_reference="urn:cwl:legal_entity:missing",
                    source_payload_hash="sha256:" + "c" * 64,
                ),
                self._policy_with(
                    legal_entity_reference="urn:cwl:legal_entity:missing"
                ),
            )
        with self.assertRaisesRegex(AccountingValidationError, "Create the accounting_book row"):
            self.ledger.post(
                self._two_line_proposal(
                    proposal_id=str(uuid.uuid4()),
                    idempotency_key="missing-book",
                    intended_book_role_code="management_book",
                    source_payload_hash="sha256:" + "d" * 64,
                ),
                self._policy_with(
                    intended_book_role_code="management_book",
                    accounting_book_reference="urn:cwl:accounting_book:management",
                ),
            )
        with self.assertRaisesRegex(AccountingValidationError, "Create the chart_account row"):
            self.ledger.post(
                self._two_line_proposal(
                    proposal_id=str(uuid.uuid4()),
                    idempotency_key="missing-chart-account",
                    source_payload_hash="sha256:" + "e" * 64,
                ),
                self._policy_with(
                    chart_account_mapping={
                        "accounts_receivable": "999999",
                        "usage_revenue": "410100",
                    }
                ),
            )
        with self.assertRaisesRegex(AccountingValidationError, "Create an open fiscal period"):
            self.ledger.post(
                self._two_line_proposal(
                    proposal_id=str(uuid.uuid4()),
                    idempotency_key="missing-period",
                    accounting_date=date(2026, 9, 1),
                    source_payload_hash="sha256:" + "f" * 64,
                ),
                self._policy_with(open_period_end=date(2026, 9, 30)),
            )

    def test_reverse_and_lookup_failures_tell_the_next_action(self) -> None:
        """Reversal and catalog misses fail closed without rewriting posted journals."""
        with self.assertRaisesRegex(AccountingValidationError, "journal does not exist"):
            self.ledger.reverse(
                "urn:cwl:accounting:general_journal:missing",
                date(2026, 8, 31),
                "billing_correction",
                self.policy,
            )

        receipt = self.ledger.post(self._two_line_proposal(), self.policy)
        with self.assertRaisesRegex(AccountingValidationError, "closed fiscal period"):
            self.ledger.reverse(
                receipt.journal_reference,
                date(2026, 9, 1),
                "billing_correction",
                self.policy,
            )
        wrong_scope = self._policy_with(tenant_reference="urn:cwl:tenant_other_scope")
        with self.assertRaisesRegex(AccountingValidationError, "scope"):
            self.ledger.reverse(
                receipt.journal_reference,
                date(2026, 8, 31),
                "billing_correction",
                wrong_scope,
            )
        reversal = self.ledger.reverse(
            receipt.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
        )
        with self.assertRaisesRegex(AccountingValidationError, "reversal journal"):
            self.ledger.reverse(
                reversal.journal_reference,
                date(2026, 8, 31),
                "billing_correction",
                self.policy,
            )

        empty = self.ledger.trial_balance(
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            date(2026, 8, 30),
        )
        self.assertEqual(empty, {})
        self.assertEqual(
            self.ledger.trial_balance(
                "urn:cwl:tenant_other",
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                date(2026, 8, 31),
            ),
            {},
        )
        self.assertEqual(
            self.ledger.trial_balance(
                self.policy.tenant_reference,
                "urn:cwl:legal_entity:missing",
                "urn:cwl:accounting_book:missing",
                date(2026, 8, 31),
            ),
            {},
        )

    def test_operator_setup_failures_name_the_next_action(self) -> None:
        """Missing driver, URL, server, or migration file fail closed with a retry action."""
        with self.assertRaisesRegex(AccountingValidationError, "Set a PostgreSQL 18 URL"):
            PostgresPostingLedger("", tenant_reference=self.policy.tenant_reference)
        with mock.patch(
            "accounting_information_platform.persistence.importlib.import_module",
            side_effect=ImportError("missing"),
        ):
            with self.assertRaisesRegex(AccountingValidationError, "requirements-quality.txt"):
                PostgresPostingLedger(
                    DATABASE_URL, tenant_reference=self.policy.tenant_reference
                ).journal_count
        unreachable = PostgresPostingLedger(
            "postgresql://postgres:postgres@127.0.0.1:1/accounting_test",
            tenant_reference=self.policy.tenant_reference,
        )
        with self.assertRaisesRegex(AccountingValidationError, "Start PostgreSQL 18"):
            unreachable.journal_count
        with self.assertRaisesRegex(
            AccountingValidationError, "Restore database/migrations"
        ):
            apply_foundation_migration(DATABASE_URL, ROOT / "absent.sql")
        with self.assertRaisesRegex(AccountingValidationError, "restore a clean database"):
            apply_foundation_migration(DATABASE_URL, MIGRATION_PATH)

    def test_close_persists_snapshot_tied_to_posted_journal(self) -> None:
        """Closing an open period snapshots the journal population in one transaction."""
        self.ledger.post(self._two_line_proposal(), self.policy)

        receipt = self._close_period()
        snapshot_lines = self._snapshot_line_totals()
        population = self._journal_population_totals()

        self.assertIsInstance(receipt, PeriodCloseReceipt)
        self.assertFalse(receipt.replayed)
        self.assertEqual(receipt.period_code, "2026-08")
        self.assertEqual(receipt.period_status_code, "hard_closed")
        self.assertEqual(receipt.source_journal_count, 1)
        self.assertEqual(self._period_status("2026-08"), "hard_closed")
        self.assertIsNotNone(self._period_closed_at("2026-08"))
        self.assertEqual(self._count_table("accounting_reporting.trial_balance_snapshot"), 1)
        self.assertEqual(self._count_table("accounting_reporting.trial_balance_line"), 2)
        self.assertEqual(self._count_outbox("period_closed"), 1)
        self.assertEqual(snapshot_lines["110100"][0], Decimal("25000"))
        self.assertEqual(snapshot_lines["410100"][1], Decimal("25000"))
        self.assertEqual(snapshot_lines["110100"][0], population["110100"][0])
        self.assertEqual(snapshot_lines["410100"][1], population["410100"][1])
        self.assertEqual(snapshot_lines["110100"][2], Decimal("25000"))
        self.assertEqual(snapshot_lines["410100"][2], Decimal("-25000"))

    def test_close_then_ordinary_post_writes_zero_rows(self) -> None:
        """A later ordinary post into a closed period writes no durable rows."""
        self.ledger.post(self._two_line_proposal(), self.policy)
        self._close_period()

        with self.assertRaisesRegex(
            AccountingValidationError,
            "Open that period or post into an open period",
        ):
            self.ledger.post(
                self._two_line_proposal(
                    proposal_id=str(uuid.uuid4()),
                    idempotency_key="after-close-rejected",
                    source_payload_hash="sha256:" + "c" * 64,
                ),
                self.policy,
            )

        self.assertEqual(self._count_table("accounting_integration.journal_proposal_record"), 1)
        self.assertEqual(self._count_table("accounting_core.general_journal"), 1)
        self.assertEqual(self._count_table("accounting_core.journal_entry_line"), 2)
        self.assertEqual(self._count_table("accounting_integration.posting_receipt"), 1)
        self.assertEqual(self.ledger.journal_count, 1)

    def test_reclose_is_idempotent(self) -> None:
        """Re-closing a hard-closed period replays the same snapshot and event."""
        self.ledger.post(self._two_line_proposal(), self.policy)
        first = self._close_period()
        second = self._close_period()

        self.assertTrue(second.replayed)
        self.assertEqual(second.snapshot_record_id, first.snapshot_record_id)
        self.assertEqual(second.source_payload_hash, first.source_payload_hash)
        self.assertEqual(second.snapshot_generated_at, first.snapshot_generated_at)
        self.assertEqual(second.source_journal_count, 1)
        self.assertEqual(self._count_table("accounting_reporting.trial_balance_snapshot"), 1)
        self.assertEqual(self._count_table("accounting_reporting.trial_balance_line"), 2)
        self.assertEqual(self._count_outbox("period_closed"), 1)

    def test_open_period_still_accepts_posts(self) -> None:
        """Closing one period does not block ordinary posting into a later open period."""
        self.ledger.post(self._two_line_proposal(), self.policy)
        self._close_period()
        self._seed_additional_period("2026-09", date(2026, 9, 1), date(2026, 9, 30))

        later = self.ledger.post(
            self._two_line_proposal(
                proposal_id=str(uuid.uuid4()),
                idempotency_key="september-open-period",
                accounting_date=date(2026, 9, 15),
                transaction_date=date(2026, 9, 15),
                source_payload_hash="sha256:" + "d" * 64,
            ),
            self._policy_with(
                open_period_start=date(2026, 9, 1),
                open_period_end=date(2026, 9, 30),
            ),
        )

        self.assertEqual(later.posting_status_code, "posted")
        self.assertEqual(self.ledger.journal_count, 2)
        self.assertEqual(self._period_status("2026-08"), "hard_closed")
        self.assertEqual(self._period_status("2026-09"), "open")

    def test_soft_close_rejects_posts_and_hard_close_reuses_snapshot(self) -> None:
        """soft_closed rejects ordinary posting; hard close upgrades without a second snapshot."""
        self.ledger.post(self._two_line_proposal(), self.policy)
        soft = self._close_period(period_status_code="soft_closed")
        replayed_soft = self._close_period(period_status_code="soft_closed")
        self.assertEqual(soft.period_status_code, "soft_closed")
        self.assertTrue(replayed_soft.replayed)
        self.assertEqual(replayed_soft.snapshot_record_id, soft.snapshot_record_id)
        self.assertEqual(self._count_outbox("period_closed"), 1)

        with self.assertRaisesRegex(
            AccountingValidationError,
            "Open that period or post into an open period",
        ):
            self.ledger.post(
                self._two_line_proposal(
                    proposal_id=str(uuid.uuid4()),
                    idempotency_key="after-soft-close",
                    source_payload_hash="sha256:" + "e" * 64,
                ),
                self.policy,
            )

        hard = self._close_period(period_status_code="hard_closed")
        ignored_soft = self._close_period(period_status_code="soft_closed")

        self.assertFalse(hard.replayed)
        self.assertEqual(hard.period_status_code, "hard_closed")
        self.assertEqual(hard.snapshot_record_id, soft.snapshot_record_id)
        self.assertEqual(self._count_table("accounting_reporting.trial_balance_snapshot"), 1)
        self.assertEqual(self._count_outbox("period_closed"), 2)
        self.assertTrue(ignored_soft.replayed)
        self.assertEqual(ignored_soft.period_status_code, "hard_closed")
        self.assertEqual(self._period_status("2026-08"), "hard_closed")
        self.assertEqual(self._count_table("accounting_integration.journal_proposal_record"), 1)

    def test_close_empty_period_and_catalog_failures_name_the_next_action(self) -> None:
        """Empty-period close is durable; catalog and status errors name the retry action."""
        empty = self._close_period()
        self.assertEqual(empty.source_journal_count, 0)
        self.assertEqual(self._count_table("accounting_reporting.trial_balance_snapshot"), 1)
        self.assertEqual(self._count_table("accounting_reporting.trial_balance_line"), 0)
        self.assertEqual(self._period_status("2026-08"), "hard_closed")

        with self.assertRaisesRegex(AccountingValidationError, "Create the fiscal_period row"):
            self._close_period(period_code="2026-10")
        with self.assertRaisesRegex(AccountingValidationError, "Create the legal_entity_record"):
            self._close_period(legal_entity_reference="urn:cwl:legal_entity:missing")
        with self.assertRaisesRegex(AccountingValidationError, "Create the accounting_book row"):
            self._close_period(accounting_book_reference="urn:cwl:accounting_book:missing")
        with self.assertRaisesRegex(AccountingValidationError, "soft_closed or hard_closed"):
            self._close_period(period_status_code="open")
        with self.assertRaisesRegex(AccountingValidationError, "book reporting currency"):
            self._close_period(snapshot_currency_code="USD")
        with self.assertRaisesRegex(AccountingValidationError, "three-letter ISO currency"):
            self._close_period(snapshot_currency_code="usd")
        with self.assertRaisesRegex(AccountingValidationError, "Supply the fiscal period code"):
            self._close_period(period_code="   ")
        self._set_period_status("hard_closed")
        other_ledger = PostgresPostingLedger(
            DATABASE_URL, tenant_reference=self.policy.tenant_reference
        )
        self._delete_snapshots()
        with self.assertRaisesRegex(
            AccountingValidationError, "Restore the trial_balance_snapshot"
        ):
            other_ledger.close_fiscal_period(
                legal_entity_reference=self.policy.legal_entity_reference,
                accounting_book_reference=self.policy.accounting_book_reference,
                period_code="2026-08",
                snapshot_currency_code="KRW",
            )

    def test_post_proposal_resolves_catalog_policy_from_billing_ingest(self) -> None:
        """A Billing validated proposal posts with AIS catalog mapping and versions."""
        payload = self._billing_validated_payload()
        proposal = ingest_journal_proposal(payload)

        policy = self.ledger.resolve_accounting_policy(proposal)
        receipt = self.ledger.post_proposal(proposal)
        replayed = self.ledger.post_proposal(proposal)
        line_accounts = self._posted_chart_accounts()

        self.assertEqual(policy.chart_account_mapping["accounts_receivable"], "110100")
        self.assertEqual(policy.chart_account_mapping["usage_revenue"], "410100")
        self.assertEqual(policy.accounting_policy_version, "ifrs-v1")
        self.assertEqual(policy.posting_rule_version, "billing-issued-v1")
        self.assertEqual(policy.accounting_book_reference, self.policy.accounting_book_reference)
        self.assertNotIn("110100", json.dumps(payload["lines"]))
        self.assertEqual(receipt.accounting_policy_version, "ifrs-v1")
        self.assertEqual(receipt.posting_rule_version, "billing-issued-v1")
        self.assertEqual(receipt.accounting_book_reference, self.policy.accounting_book_reference)
        self.assertEqual(receipt, replayed)
        self.assertEqual(self.ledger.journal_count, 1)
        self.assertEqual(self._count_table("accounting_core.general_journal"), 1)
        self.assertEqual(line_accounts, {"110100", "410100"})

    def test_post_proposal_catalog_misses_write_zero_rows(self) -> None:
        """Unmapped roles, missing books, and closed periods write no durable rows."""
        with self.assertRaisesRegex(AccountingValidationError, "Create the account_role_mapping row"):
            self.ledger.post_proposal(
                ingest_journal_proposal(
                    self._billing_validated_payload(
                        lines=[
                            {
                                "line_number": 1,
                                "account_role_code": "accounts_receivable",
                                "debit_amount": "25000",
                                "credit_amount": "0",
                            },
                            {
                                "line_number": 2,
                                "account_role_code": "tax_payable",
                                "debit_amount": "0",
                                "credit_amount": "25000",
                            },
                        ]
                    )
                )
            )
        with self.assertRaisesRegex(AccountingValidationError, "Create the accounting_book row"):
            self.ledger.post_proposal(
                ingest_journal_proposal(
                    self._billing_validated_payload(intended_book_role_code="management_book")
                )
            )
        self.ledger.close_fiscal_period(
            legal_entity_reference=self.policy.legal_entity_reference,
            accounting_book_reference=self.policy.accounting_book_reference,
            period_code="2026-08",
            snapshot_currency_code="KRW",
        )
        with self.assertRaisesRegex(
            AccountingValidationError,
            "Open that period or post into an open period",
        ):
            self.ledger.post_proposal(ingest_journal_proposal(self._billing_validated_payload()))

        self.assertEqual(self._count_table("accounting_integration.journal_proposal_record"), 0)
        self.assertEqual(self._count_table("accounting_core.general_journal"), 0)
        self.assertEqual(self._count_table("accounting_core.journal_entry_line"), 0)
        self.assertEqual(self._count_table("accounting_integration.posting_receipt"), 0)

    def test_resolve_accounting_policy_catalog_failures_name_the_next_action(self) -> None:
        """Policy resolution fails closed without inventing chart codes or versions."""
        with self.assertRaisesRegex(AccountingValidationError, "Open a PostgresPostingLedger"):
            self.ledger.resolve_accounting_policy(
                ingest_journal_proposal(
                    self._billing_validated_payload(tenant_reference="urn:cwl:tenant_other")
                )
            )
        with self.assertRaisesRegex(AccountingValidationError, "Create the legal_entity_record"):
            self.ledger.resolve_accounting_policy(
                ingest_journal_proposal(
                    self._billing_validated_payload(
                        legal_entity_reference="urn:cwl:legal_entity:missing"
                    )
                )
            )
        self._delete_role_mappings()
        with self.assertRaisesRegex(
            AccountingValidationError, "Create the account_role_mapping rows"
        ):
            self.ledger.resolve_accounting_policy(
                ingest_journal_proposal(self._billing_validated_payload())
            )
        self._seed_role_mapping("accounts_receivable", "110100")
        self._seed_role_mapping(
            "usage_revenue",
            "410100",
            accounting_policy_version="ifrs-v2",
        )
        with self.assertRaisesRegex(AccountingValidationError, "single effective mapping set"):
            self.ledger.resolve_accounting_policy(
                ingest_journal_proposal(self._billing_validated_payload())
            )
        self._delete_role_mappings()
        self._seed_role_mapping("accounts_receivable", "110100")
        self._seed_role_mapping("usage_revenue", "410100")
        self._seed_role_mapping(
            "accounts_receivable",
            "110100",
            valid_from=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        with self.assertRaisesRegex(AccountingValidationError, "Close the superseded mapping"):
            self.ledger.resolve_accounting_policy(
                ingest_journal_proposal(self._billing_validated_payload())
            )

    def _close_period(self, **overrides: object) -> PeriodCloseReceipt:
        values: dict[str, object] = {
            "legal_entity_reference": self.policy.legal_entity_reference,
            "accounting_book_reference": self.policy.accounting_book_reference,
            "period_code": "2026-08",
            "snapshot_currency_code": "KRW",
        }
        values.update(overrides)
        return self.ledger.close_fiscal_period(**values)

    def _policy_with(self, **overrides: object) -> AccountingPolicy:
        values: dict[str, object] = {
            "tenant_reference": self.policy.tenant_reference,
            "legal_entity_reference": self.policy.legal_entity_reference,
            "accounting_book_reference": self.policy.accounting_book_reference,
            "intended_book_role_code": self.policy.intended_book_role_code,
            "transaction_currency": "KRW",
            "functional_currency": "KRW",
            "open_period_start": date(2026, 8, 1),
            "open_period_end": date(2026, 8, 31),
            "chart_account_mapping": self.policy.chart_account_mapping,
            "accounting_policy_version": "ifrs-v1",
            "posting_rule_version": "billing-issued-v1",
        }
        values.update(overrides)
        return AccountingPolicy(**values)

    def _two_line_proposal(self, **overrides: object) -> JournalProposal:
        values: dict[str, object] = {
            "proposal_id": str(uuid.uuid4()),
            "proposal_contract_version": 1,
            "idempotency_key": "invoice-two-line-v1",
            "tenant_reference": self.policy.tenant_reference,
            "legal_entity_reference": self.policy.legal_entity_reference,
            "intended_book_role_code": self.policy.intended_book_role_code,
            "transaction_currency": "KRW",
            "transaction_date": date(2026, 8, 31),
            "accounting_date": date(2026, 8, 31),
            "source_payload_hash": "sha256:" + "a" * 64,
            "source_event_references": ("urn:cwl:billing:invoice:two_line",),
            "lines": (
                JournalLineProposal(1, "accounts_receivable", "25000", "0"),
                JournalLineProposal(2, "usage_revenue", "0", "25000"),
            ),
        }
        values.update(overrides)
        return JournalProposal(**values)

    def _seed_master_data(self, *, period_status_code: str) -> str:
        with psycopg.connect(DATABASE_URL) as connection:
            tenant_id = connection.execute(
                """
                INSERT INTO accounting_core.tenant_account (tenant_account_code)
                VALUES (%s)
                RETURNING tenant_account_id
                """,
                (self.policy.tenant_reference,),
            ).fetchone()[0]
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(tenant_id),),
            )
            legal_entity_id = connection.execute(
                """
                INSERT INTO accounting_core.legal_entity_record (
                    tenant_account_id, legal_entity_code, entity_name,
                    functional_currency_code, valid_from
                )
                VALUES (%s, %s, %s, 'KRW', %s)
                RETURNING legal_entity_id
                """,
                (
                    tenant_id,
                    self.policy.legal_entity_reference,
                    "Statutory entity",
                    VALID_FROM,
                ),
            ).fetchone()[0]
            book_id = connection.execute(
                """
                INSERT INTO accounting_core.accounting_book (
                    tenant_account_id, legal_entity_id, book_role_code, book_name,
                    reporting_currency_code, valid_from
                )
                VALUES (%s, %s, %s, %s, 'KRW', %s)
                RETURNING accounting_book_id
                """,
                (
                    tenant_id,
                    legal_entity_id,
                    self.policy.intended_book_role_code,
                    self.policy.accounting_book_reference,
                    VALID_FROM,
                ),
            ).fetchone()[0]
            for account_code, account_name, normal_balance_code in (
                ("110100", "Accounts receivable", "debit"),
                ("410100", "Usage revenue", "credit"),
            ):
                chart_account_id = connection.execute(
                    """
                    INSERT INTO accounting_core.chart_account (
                        tenant_account_id, accounting_book_id, chart_account_code,
                        account_name, normal_balance_code, valid_from
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING chart_account_id
                    """,
                    (
                        tenant_id,
                        book_id,
                        account_code,
                        account_name,
                        normal_balance_code,
                        VALID_FROM,
                    ),
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO accounting_core.account_role_mapping (
                        tenant_account_id, accounting_book_id, account_role_code,
                        chart_account_id, accounting_policy_version, posting_rule_version,
                        valid_from
                    )
                    VALUES (%s, %s, %s, %s, 'ifrs-v1', 'billing-issued-v1', %s)
                    """,
                    (
                        tenant_id,
                        book_id,
                        "accounts_receivable" if account_code == "110100" else "usage_revenue",
                        chart_account_id,
                        VALID_FROM,
                    ),
                )
            calendar_id = connection.execute(
                """
                INSERT INTO accounting_core.fiscal_calendar (
                    tenant_account_id, calendar_code, calendar_name
                )
                VALUES (%s, 'statutory_calendar', 'Statutory calendar')
                RETURNING fiscal_calendar_id
                """,
                (tenant_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO accounting_core.fiscal_period (
                    tenant_account_id, fiscal_calendar_id, period_code,
                    period_start_date, period_end_date, period_status_code
                )
                VALUES (%s, %s, '2026-08', %s, %s, %s)
                """,
                (
                    tenant_id,
                    calendar_id,
                    date(2026, 8, 1),
                    date(2026, 8, 31),
                    period_status_code,
                ),
            )
            connection.commit()
        return str(tenant_id)

    def _set_period_status(self, period_status_code: str) -> None:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (self.tenant_id,),
            )
            connection.execute(
                """
                UPDATE accounting_core.fiscal_period
                SET period_status_code = %s
                WHERE tenant_account_id = %s
                """,
                (period_status_code, self.tenant_id),
            )
            connection.commit()

    def _count_table(self, table_name: str) -> int:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (self.tenant_id,),
            )
            return connection.execute(
                f"SELECT COUNT(*) FROM {table_name} WHERE tenant_account_id = %s",
                (self.tenant_id,),
            ).fetchone()[0]

    def _journal_population_totals(self) -> dict[str, tuple[Decimal, Decimal]]:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (self.tenant_id,),
            )
            rows = connection.execute(
                """
                SELECT chart_account.chart_account_code,
                       SUM(journal_entry_line.debit_amount),
                       SUM(journal_entry_line.credit_amount)
                FROM accounting_core.journal_entry_line
                JOIN accounting_core.chart_account
                  ON chart_account.tenant_account_id = journal_entry_line.tenant_account_id
                 AND chart_account.chart_account_id = journal_entry_line.chart_account_id
                WHERE journal_entry_line.tenant_account_id = %s
                GROUP BY chart_account.chart_account_code
                """,
                (self.tenant_id,),
            ).fetchall()
        return {row[0]: (row[1], row[2]) for row in rows}

    def _seed_additional_period(
        self, period_code: str, period_start: date, period_end: date
    ) -> None:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (self.tenant_id,),
            )
            calendar_id = connection.execute(
                """
                SELECT fiscal_calendar_id
                FROM accounting_core.fiscal_calendar
                WHERE tenant_account_id = %s
                """,
                (self.tenant_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO accounting_core.fiscal_period (
                    tenant_account_id, fiscal_calendar_id, period_code,
                    period_start_date, period_end_date, period_status_code
                )
                VALUES (%s, %s, %s, %s, %s, 'open')
                """,
                (self.tenant_id, calendar_id, period_code, period_start, period_end),
            )
            connection.commit()

    def _period_status(self, period_code: str) -> str:
        return self._period_row(period_code)[0]

    def _period_closed_at(self, period_code: str):
        return self._period_row(period_code)[1]

    def _period_row(self, period_code: str) -> tuple[str, object]:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (self.tenant_id,),
            )
            return connection.execute(
                """
                SELECT period_status_code, period_closed_at
                FROM accounting_core.fiscal_period
                WHERE tenant_account_id = %s AND period_code = %s
                """,
                (self.tenant_id, period_code),
            ).fetchone()

    def _count_outbox(self, event_type_code: str) -> int:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (self.tenant_id,),
            )
            return connection.execute(
                """
                SELECT COUNT(*)
                FROM accounting_integration.outbox_event
                WHERE tenant_account_id = %s AND event_type_code = %s
                """,
                (self.tenant_id, event_type_code),
            ).fetchone()[0]

    def _snapshot_line_totals(self) -> dict[str, tuple[Decimal, Decimal, Decimal]]:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (self.tenant_id,),
            )
            rows = connection.execute(
                """
                SELECT chart_account.chart_account_code,
                       trial_balance_line.debit_total_amount,
                       trial_balance_line.credit_total_amount,
                       trial_balance_line.net_balance_amount
                FROM accounting_reporting.trial_balance_line
                JOIN accounting_core.chart_account
                  ON chart_account.tenant_account_id = trial_balance_line.tenant_account_id
                 AND chart_account.chart_account_id = trial_balance_line.chart_account_id
                WHERE trial_balance_line.tenant_account_id = %s
                """,
                (self.tenant_id,),
            ).fetchall()
        return {row[0]: (row[1], row[2], row[3]) for row in rows}

    def _billing_validated_payload(self, **overrides: object) -> dict[str, object]:
        source_payload_hash = "sha256:" + "a" * 64
        invoice_draft_id = "019d7b92-1aa0-7a7f-b61c-962c0f4bf612"
        values: dict[str, object] = {
            "proposal_id": invoice_draft_id,
            "proposal_contract_version": 1,
            "idempotency_key": (
                f"{self.policy.tenant_reference}:invoice_draft:{invoice_draft_id}"
                f":{source_payload_hash}:v1"
            ),
            "tenant_reference": self.policy.tenant_reference,
            "legal_entity_reference": self.policy.legal_entity_reference,
            "intended_book_role_code": "primary_statutory",
            "transaction_currency": "KRW",
            "transaction_date": "2026-08-31",
            "accounting_date": "2026-08-31",
            "source_payload_hash": source_payload_hash,
            "proposed_at": "2026-08-31T00:00:00Z",
            "proposal_status": "validated",
            "source_event_references": (
                f"{self.policy.tenant_reference}:invoice_draft:{invoice_draft_id}",
            ),
            "lines": [
                {
                    "line_number": 1,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "25000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "0",
                    "credit_amount": "25000",
                },
            ],
        }
        values.update(overrides)
        return values

    def _posted_chart_accounts(self) -> set[str]:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (self.tenant_id,),
            )
            rows = connection.execute(
                """
                SELECT chart_account.chart_account_code
                FROM accounting_core.journal_entry_line
                JOIN accounting_core.chart_account
                  ON chart_account.tenant_account_id = journal_entry_line.tenant_account_id
                 AND chart_account.chart_account_id = journal_entry_line.chart_account_id
                WHERE journal_entry_line.tenant_account_id = %s
                """,
                (self.tenant_id,),
            ).fetchall()
        return {row[0] for row in rows}

    def _delete_role_mappings(self) -> None:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (self.tenant_id,),
            )
            connection.execute(
                """
                DELETE FROM accounting_core.account_role_mapping
                WHERE tenant_account_id = %s
                """,
                (self.tenant_id,),
            )
            connection.commit()

    def _seed_role_mapping(
        self,
        account_role_code: str,
        chart_account_code: str,
        *,
        accounting_policy_version: str = "ifrs-v1",
        posting_rule_version: str = "billing-issued-v1",
        valid_from: datetime = VALID_FROM,
    ) -> None:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (self.tenant_id,),
            )
            chart_account_id, book_id = connection.execute(
                """
                SELECT chart_account.chart_account_id, chart_account.accounting_book_id
                FROM accounting_core.chart_account
                WHERE chart_account.tenant_account_id = %s
                  AND chart_account.chart_account_code = %s
                """,
                (self.tenant_id, chart_account_code),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO accounting_core.account_role_mapping (
                    tenant_account_id, accounting_book_id, account_role_code,
                    chart_account_id, accounting_policy_version, posting_rule_version,
                    valid_from
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    self.tenant_id,
                    book_id,
                    account_role_code,
                    chart_account_id,
                    accounting_policy_version,
                    posting_rule_version,
                    valid_from,
                ),
            )
            connection.commit()

    def _delete_snapshots(self) -> None:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (self.tenant_id,),
            )
            connection.execute(
                """
                DELETE FROM accounting_reporting.trial_balance_line
                WHERE tenant_account_id = %s
                """,
                (self.tenant_id,),
            )
            connection.execute(
                """
                DELETE FROM accounting_reporting.trial_balance_snapshot
                WHERE tenant_account_id = %s
                """,
                (self.tenant_id,),
            )
            connection.commit()

    def _original_journal_status(self, journal_reference: str) -> str:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (self.tenant_id,),
            )
            return connection.execute(
                """
                SELECT journal_status_code
                FROM accounting_core.general_journal
                WHERE tenant_account_id = %s AND journal_reference = %s
                """,
                (self.tenant_id, journal_reference),
            ).fetchone()[0]


if __name__ == "__main__":
    unittest.main()
