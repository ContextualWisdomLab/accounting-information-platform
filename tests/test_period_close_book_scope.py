"""PostgreSQL regression tests for accounting-book fiscal-period isolation."""

from __future__ import annotations

import os
import unittest
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import psycopg

from accounting_information_platform import (
    AccountingPolicy,
    AccountingValidationError,
    IdempotencyConflictError,
    JournalLineProposal,
    JournalProposal,
    PostgresPostingLedger,
)
from accounting_information_platform.persistence import apply_foundation_migration


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "database/migrations/0001_accounting_foundation.sql"
DATABASE_URL = os.environ.get(
    "ACCOUNTING_DATABASE_URL",
    "postgresql://postgres:postgres@127.0.0.1:5432/accounting_test",
)
VALID_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)


class PostgresBookPeriodIsolationTests(unittest.TestCase):
    """Keep close state isolated to the accounting book that controllers close."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the complete foundation into a clean PostgreSQL catalog."""
        with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
            connection.execute("DROP SCHEMA IF EXISTS accounting_reporting CASCADE")
            connection.execute("DROP SCHEMA IF EXISTS accounting_integration CASCADE")
            connection.execute("DROP SCHEMA IF EXISTS accounting_core CASCADE")
        apply_foundation_migration(DATABASE_URL, MIGRATION_PATH)

    def setUp(self) -> None:
        """Seed one entity with statutory and management books sharing a calendar period."""
        suffix = uuid.uuid4().hex[:8]
        self.tenant_reference = f"urn:cwl:tenant_{suffix}"
        self.legal_entity_reference = f"urn:cwl:legal_entity:entity_{suffix}"
        self.stat_book_reference = f"urn:cwl:accounting_book:statutory_{suffix}"
        self.mgmt_book_reference = f"urn:cwl:accounting_book:management_{suffix}"

        with psycopg.connect(DATABASE_URL) as connection:
            tenant_id = connection.execute(
                """
                INSERT INTO accounting_core.tenant_account (tenant_account_code)
                VALUES (%s)
                RETURNING tenant_account_id
                """,
                (self.tenant_reference,),
            ).fetchone()[0]
            legal_entity_id = connection.execute(
                """
                INSERT INTO accounting_core.legal_entity_record (
                    tenant_account_id,
                    legal_entity_code,
                    entity_name,
                    functional_currency_code,
                    valid_from
                )
                VALUES (%s, %s, %s, 'KRW', %s)
                RETURNING legal_entity_id
                """,
                (
                    tenant_id,
                    self.legal_entity_reference,
                    f"Entity {suffix}",
                    VALID_FROM,
                ),
            ).fetchone()[0]
            stat_book_id = connection.execute(
                """
                INSERT INTO accounting_core.accounting_book (
                    tenant_account_id,
                    legal_entity_id,
                    book_role_code,
                    book_name,
                    reporting_currency_code,
                    valid_from
                )
                VALUES (%s, %s, 'primary_statutory', %s, 'KRW', %s)
                RETURNING accounting_book_id
                """,
                (tenant_id, legal_entity_id, self.stat_book_reference, VALID_FROM),
            ).fetchone()[0]
            mgmt_book_id = connection.execute(
                """
                INSERT INTO accounting_core.accounting_book (
                    tenant_account_id,
                    legal_entity_id,
                    book_role_code,
                    book_name,
                    reporting_currency_code,
                    valid_from
                )
                VALUES (%s, %s, 'management', %s, 'KRW', %s)
                RETURNING accounting_book_id
                """,
                (tenant_id, legal_entity_id, self.mgmt_book_reference, VALID_FROM),
            ).fetchone()[0]
            fiscal_calendar_id = connection.execute(
                """
                INSERT INTO accounting_core.fiscal_calendar (
                    tenant_account_id,
                    calendar_code,
                    calendar_name
                )
                VALUES (%s, 'monthly', 'Monthly calendar')
                RETURNING fiscal_calendar_id
                """,
                (tenant_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO accounting_core.fiscal_period (
                    tenant_account_id,
                    fiscal_calendar_id,
                    period_code,
                    period_start_date,
                    period_end_date,
                    period_status_code
                )
                VALUES (%s, %s, '2026-08', DATE '2026-08-01', DATE '2026-08-31', 'open')
                """,
                (tenant_id, fiscal_calendar_id),
            )

            account_rows = (
                ("110100", "Accounts receivable", "debit", "asset", "accounts_receivable"),
                ("410100", "Usage revenue", "credit", "revenue", "usage_revenue"),
            )
            for (
                account_code,
                account_name,
                normal_balance_code,
                account_class_code,
                account_role_code,
            ) in account_rows:
                chart_account_id = connection.execute(
                    """
                    INSERT INTO accounting_core.chart_account (
                        tenant_account_id,
                        accounting_book_id,
                        chart_account_code,
                        account_name,
                        normal_balance_code,
                        valid_from,
                        account_class_code
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING chart_account_id
                    """,
                    (
                        tenant_id,
                        mgmt_book_id,
                        account_code,
                        account_name,
                        normal_balance_code,
                        VALID_FROM,
                        account_class_code,
                    ),
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO accounting_core.account_role_mapping (
                        tenant_account_id,
                        accounting_book_id,
                        account_role_code,
                        chart_account_id,
                        accounting_policy_version,
                        posting_rule_version,
                        valid_from
                    )
                    VALUES (%s, %s, %s, %s, 'ifrs-v1', 'billing-issued-v1', %s)
                    """,
                    (
                        tenant_id,
                        mgmt_book_id,
                        account_role_code,
                        chart_account_id,
                        VALID_FROM,
                    ),
                )

        self.ledger = PostgresPostingLedger(
            DATABASE_URL,
            tenant_reference=self.tenant_reference,
        )
        self.management_policy = AccountingPolicy(
            tenant_reference=self.tenant_reference,
            legal_entity_reference=self.legal_entity_reference,
            accounting_book_reference=self.mgmt_book_reference,
            intended_book_role_code="management",
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

    def test_soft_close_one_book_does_not_block_sibling_book_posting(self) -> None:
        """Closing the statutory book must not close the independent management book."""
        close_receipt = self.ledger.close_fiscal_period(
            self.legal_entity_reference,
            self.stat_book_reference,
            "2026-08",
            "KRW",
            period_status_code="soft_closed",
            idempotency_key=f"{self.tenant_reference}:statutory:2026-08:soft-close",
        )
        proposal = JournalProposal(
            proposal_id=str(uuid.uuid4()),
            proposal_contract_version=1,
            idempotency_key=f"{self.tenant_reference}:management:posting",
            tenant_reference=self.tenant_reference,
            legal_entity_reference=self.legal_entity_reference,
            intended_book_role_code="management",
            transaction_currency="KRW",
            transaction_date=date(2026, 8, 31),
            accounting_date=date(2026, 8, 31),
            source_payload_hash="sha256:" + ("a" * 64),
            source_event_references=(
                f"urn:cwl:billing:journal_proposal:{uuid.uuid4()}",
            ),
            lines=(
                JournalLineProposal(
                    line_number=1,
                    account_role_code="accounts_receivable",
                    debit_amount=Decimal("125.00"),
                    credit_amount=Decimal("0"),
                ),
                JournalLineProposal(
                    line_number=2,
                    account_role_code="usage_revenue",
                    debit_amount=Decimal("0"),
                    credit_amount=Decimal("125.00"),
                ),
            ),
        )

        posting_receipt = self.ledger.post(proposal, self.management_policy)

        self.assertEqual(close_receipt.period_status_code, "soft_closed")
        self.assertEqual(posting_receipt.posting_status_code, "posted")
        self.assertEqual(
            posting_receipt.accounting_book_reference,
            self.mgmt_book_reference,
        )

    def test_soft_close_replay_rejects_a_different_command_key(self) -> None:
        """A soft-close replay is exact and cannot accept a different command identity."""
        original_key = f"{self.tenant_reference}:statutory:2026-08:soft-close"
        first = self.ledger.close_fiscal_period(
            self.legal_entity_reference,
            self.stat_book_reference,
            "2026-08",
            "KRW",
            period_status_code="soft_closed",
            idempotency_key=original_key,
        )
        replay = self.ledger.close_fiscal_period(
            self.legal_entity_reference,
            self.stat_book_reference,
            "2026-08",
            "KRW",
            period_status_code="soft_closed",
            idempotency_key=original_key,
        )

        self.assertTrue(replay.replayed)
        self.assertEqual(replay.source_payload_hash, first.source_payload_hash)
        self.assertEqual(replay.source_journal_count, first.source_journal_count)
        with self.assertRaises(IdempotencyConflictError):
            self.ledger.close_fiscal_period(
                self.legal_entity_reference,
                self.stat_book_reference,
                "2026-08",
                "KRW",
                period_status_code="soft_closed",
                idempotency_key=f"{original_key}:different",
            )

        with psycopg.connect(DATABASE_URL) as connection:
            with self.assertRaises(psycopg.errors.CheckViolation):
                connection.execute(
                    """
                    UPDATE accounting_core.accounting_book_period_control AS period_control
                    SET soft_close_idempotency_key = %s
                    FROM accounting_core.accounting_book AS accounting_book,
                         accounting_core.fiscal_period AS fiscal_period,
                         accounting_core.tenant_account AS tenant_account
                    WHERE period_control.tenant_account_id = tenant_account.tenant_account_id
                      AND period_control.accounting_book_id = accounting_book.accounting_book_id
                      AND period_control.fiscal_period_id = fiscal_period.fiscal_period_id
                      AND tenant_account.tenant_account_code = %s
                      AND accounting_book.book_name = %s
                      AND fiscal_period.period_code = '2026-08'
                    """,
                    (f"{original_key}:tampered", self.tenant_reference, self.stat_book_reference),
                )
            connection.rollback()

    def test_legacy_soft_close_without_command_evidence_fails_closed(self) -> None:
        """Do not manufacture replay evidence for a migrated legacy soft-close row."""
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                INSERT INTO accounting_core.accounting_book_period_control (
                    tenant_account_id,
                    accounting_book_id,
                    fiscal_period_id,
                    period_status_code,
                    period_closed_at
                )
                SELECT tenant_account.tenant_account_id,
                       accounting_book.accounting_book_id,
                       fiscal_period.fiscal_period_id,
                       'soft_closed',
                       clock_timestamp()
                FROM accounting_core.tenant_account AS tenant_account
                JOIN accounting_core.accounting_book AS accounting_book
                  ON accounting_book.tenant_account_id = tenant_account.tenant_account_id
                JOIN accounting_core.fiscal_period AS fiscal_period
                  ON fiscal_period.tenant_account_id = tenant_account.tenant_account_id
                WHERE tenant_account.tenant_account_code = %s
                  AND accounting_book.book_name = %s
                  AND fiscal_period.period_code = '2026-08'
                """,
                (self.tenant_reference, self.stat_book_reference),
            )
        with self.assertRaises(AccountingValidationError):
            self.ledger.close_fiscal_period(
                self.legal_entity_reference,
                self.stat_book_reference,
                "2026-08",
                "KRW",
                period_status_code="soft_closed",
                idempotency_key=f"{self.tenant_reference}:legacy-soft-close",
            )


if __name__ == "__main__":
    unittest.main()
