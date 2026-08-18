"""Realistic PostgreSQL posting tests against the foundation migration."""

from __future__ import annotations

import http.client
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest import mock

from accounting_information_platform import (
    AccountingPolicy,
    AccountingValidationError,
    IdempotencyConflictError,
    JournalLineProposal,
    JournalProposal,
    PeriodCloseReceipt,
    PostgresPostingLedger,
    accept_adjusting_journal,
    accept_billing_proposal_pull,
    accept_journal_proposal,
    accept_journal_reversal,
    accept_period_close,
    accept_period_open,
    accept_pulled_proposals,
    create_journal_proposal_server,
    ingest_journal_proposal,
    lookup_account_balances,
    lookup_account_rollforward,
    lookup_account_ledger,
    lookup_account_role_mappings,
    lookup_accounting_books,
    lookup_chart_accounts,
    lookup_legal_entities,
    lookup_financial_statement,
    lookup_financial_statement_package,
    lookup_fiscal_period,
    lookup_fiscal_periods,
    lookup_audit_events,
    lookup_outbox_events,
    lookup_journal_reversals,
    lookup_period_closes,
    lookup_period_journals,
    lookup_posted_journal,
    publish_outbox_event,
    lookup_published_receipt,
    lookup_period_close_package,
    lookup_payable_aging,
    lookup_receivable_aging,
    lookup_trial_balance,
    lookup_unapplied_cash_rollforward,
    lookup_vat_period_register,
    lookup_home_tax_submissions,
    pull_journal_proposal,
    pull_validated_journal_proposals,
    run_journal_proposal_server,
)
import psycopg

from accounting_information_platform.persistence import (
    _fiscal_year_identity,
    apply_foundation_migration,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "database/migrations/0001_accounting_foundation.sql"
DATABASE_URL = os.environ.get(
    "ACCOUNTING_DATABASE_URL",
    "postgresql://postgres:postgres@127.0.0.1:5432/accounting_test",
)
VALID_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)


class FakeBillingServer(ThreadingHTTPServer):
    """Serves Billing #15 list + get-by-id fixtures without the live network."""

    def __init__(
        self,
        server_address: tuple[str, int],
        proposals: list[object],
        *,
        list_status: int = 200,
        get_status: int = 200,
        list_raw: bytes | None = None,
        get_raw: bytes | None = None,
    ) -> None:
        self.proposals = proposals
        self.list_status = list_status
        self.get_status = get_status
        self.list_raw = list_raw
        self.get_raw = get_raw
        self.last_list_query: dict[str, list[str]] = {}
        self.last_list_body: dict[str, object] = {}
        self.list_queries: list[dict[str, list[str]]] = []
        super().__init__(server_address, FakeBillingHandler)


class FakeBillingHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        header_tenant = self.headers.get("X-CWL-Tenant-Reference")
        query_tenants = query.get("tenant_reference", [])
        query_tenant = query_tenants[0] if query_tenants else None
        if header_tenant is None or query_tenant is None or header_tenant != query_tenant:
            self._json(422, {"rejection_reason_code": "request_invalid"})
            return
        server = self.server
        assert isinstance(server, FakeBillingServer)
        if parsed.path == "/v1/journal-proposals":
            if server.list_status != 200:
                self._json(server.list_status, {"rejection_reason_code": "request_invalid"})
                return
            server.last_list_query = query
            server.list_queries.append(query)
            if server.list_raw is not None:
                self._raw(200, server.list_raw)
                return
            filtered = [
                item
                for item in server.proposals
                if isinstance(item, dict) and item.get("tenant_reference") == header_tenant
            ]
            filtered.sort(
                key=lambda item: (
                    str(item.get("proposed_at", "")),
                    str(item.get("proposal_id", "")),
                )
            )
            proposed_after = query.get("proposed_after", [None])[0]
            if proposed_after:
                filtered = [
                    item
                    for item in filtered
                    if str(item.get("proposed_at", "")) >= proposed_after
                ]
            cursor = query.get("cursor", [None])[0]
            if cursor:
                cursor_at, separator, cursor_id = cursor.partition("|")
                if separator:
                    filtered = [
                        item
                        for item in filtered
                        if (
                            str(item.get("proposed_at", "")),
                            str(item.get("proposal_id", "")),
                        )
                        > (cursor_at, cursor_id)
                    ]
            limit_values = query.get("page_limit", [])
            try:
                limit = int(limit_values[0]) if limit_values else 50
            except ValueError:
                limit = 50
            if limit < 1:
                limit = 50
            if limit > 100:
                limit = 100
            page = filtered[:limit]
            remainder = filtered[limit:]
            next_cursor = None
            if remainder and page:
                last = page[-1]
                next_cursor = f"{last['proposed_at']}|{last['proposal_id']}"
            body = {"journal_proposals": page, "next_cursor": next_cursor}
            server.last_list_body = body
            self._json(200, body)
            return
        if parsed.path.startswith("/v1/journal-proposals/"):
            if server.get_status != 200:
                self._json(server.get_status, {"rejection_reason_code": "proposal_not_found"})
                return
            if server.get_raw is not None:
                self._raw(200, server.get_raw)
                return
            proposal_id = parsed.path.rsplit("/", 1)[-1]
            for item in server.proposals:
                if (
                    isinstance(item, dict)
                    and str(item.get("proposal_id")) == proposal_id
                    and item.get("tenant_reference") == header_tenant
                ):
                    self._json(200, item)
                    return
            self._json(404, {"rejection_reason_code": "proposal_not_found"})
            return
        self._json(404, {"rejection_reason_code": "proposal_not_found"})

    def _json(self, status: int, payload: dict[str, object]) -> None:
        self._raw(status, json.dumps(payload).encode("utf-8"))

    def _raw(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


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
                "cash_receipt": "110200",
                "tax_payable": "210100",
                "write_off_expense": "510100",
                "unapplied_cash": "210200",
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
        with tempfile.TemporaryDirectory() as temporary_directory:
            only_foundation = Path(temporary_directory) / "0001_accounting_foundation.sql"
            only_foundation.write_text(
                MIGRATION_PATH.read_text(encoding="utf-8"), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                AccountingValidationError, "0002_chart_account_class"
            ):
                apply_foundation_migration(DATABASE_URL, only_foundation)
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            foundation = temporary_root / "0001_accounting_foundation.sql"
            foundation.write_text(
                MIGRATION_PATH.read_text(encoding="utf-8"), encoding="utf-8"
            )
            (temporary_root / "0002_chart_account_class.sql").write_text(
                (ROOT / "database/migrations/0002_chart_account_class.sql").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                AccountingValidationError, "0003_home_tax_submission"
            ):
                apply_foundation_migration(DATABASE_URL, foundation)
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
        self.assertEqual(receipt.source_journal_count, 2)
        self.assertEqual(self._period_status("2026-08"), "hard_closed")
        self.assertIsNotNone(self._period_closed_at("2026-08"))
        self.assertEqual(self._count_table("accounting_reporting.trial_balance_snapshot"), 1)
        self.assertEqual(self._count_table("accounting_reporting.trial_balance_line"), 3)
        self.assertEqual(self._count_outbox("period_close"), 1)
        self.assertEqual(self._count_closing_journals(), 1)
        self.assertEqual(snapshot_lines["110100"][0], Decimal("25000"))
        self.assertEqual(snapshot_lines["410100"][0], Decimal("25000"))
        self.assertEqual(snapshot_lines["410100"][1], Decimal("25000"))
        self.assertEqual(snapshot_lines["310100"][1], Decimal("25000"))
        self.assertEqual(snapshot_lines["110100"][0], population["110100"][0])
        self.assertEqual(snapshot_lines["410100"][1], population["410100"][1])
        self.assertEqual(snapshot_lines["110100"][2], Decimal("25000"))
        self.assertEqual(snapshot_lines["410100"][2], Decimal("0"))
        self.assertEqual(snapshot_lines["310100"][2], Decimal("-25000"))

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

        self.assertEqual(self._count_table("accounting_integration.journal_proposal_record"), 2)
        self.assertEqual(self._count_table("accounting_core.general_journal"), 2)
        self.assertEqual(self._count_table("accounting_core.journal_entry_line"), 4)
        self.assertEqual(self._count_table("accounting_integration.posting_receipt"), 1)
        self.assertEqual(self.ledger.journal_count, 2)

    def test_reclose_is_idempotent(self) -> None:
        """Re-closing a hard-closed period replays the same snapshot and event."""
        self.ledger.post(self._two_line_proposal(), self.policy)
        first = self._close_period()
        second = self._close_period()

        self.assertTrue(second.replayed)
        self.assertEqual(second.snapshot_record_id, first.snapshot_record_id)
        self.assertEqual(second.source_payload_hash, first.source_payload_hash)
        self.assertEqual(second.snapshot_generated_at, first.snapshot_generated_at)
        self.assertEqual(second.source_journal_count, 2)
        self.assertEqual(self._count_table("accounting_reporting.trial_balance_snapshot"), 1)
        self.assertEqual(self._count_table("accounting_reporting.trial_balance_line"), 3)
        self.assertEqual(self._count_outbox("period_close"), 1)
        self.assertEqual(self._count_closing_journals(), 1)

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
        self.assertEqual(self.ledger.journal_count, 3)
        self.assertEqual(self._period_status("2026-08"), "hard_closed")
        self.assertEqual(self._period_status("2026-09"), "open")

    def test_soft_close_rejects_ordinary_posts_and_allows_reversal_until_hard_close(
        self,
    ) -> None:
        """soft_closed locks ordinary posts, keeps live TB, and allows adjusting reversal."""
        posted = self.ledger.post(self._two_line_proposal(), self.policy)
        later_posted = self.ledger.post(
            self._two_line_proposal(
                proposal_id=str(uuid.uuid4()),
                idempotency_key="invoice-two-line-later-v1",
                source_payload_hash="sha256:" + "d" * 64,
            ),
            self.policy,
        )
        soft = self._close_period(period_status_code="soft_closed")
        replayed_soft = self._close_period(period_status_code="soft_closed")
        self.assertEqual(soft.period_status_code, "soft_closed")
        self.assertEqual(soft.snapshot_record_id, "")
        self.assertFalse(soft.replayed)
        self.assertTrue(replayed_soft.replayed)
        self.assertEqual(replayed_soft.period_status_code, "soft_closed")
        self.assertEqual(replayed_soft.snapshot_record_id, "")
        self.assertEqual(replayed_soft.snapshot_generated_at, soft.snapshot_generated_at)
        self.assertEqual(replayed_soft.source_payload_hash, soft.source_payload_hash)
        self.assertEqual(self._count_table("accounting_reporting.trial_balance_snapshot"), 0)
        self.assertEqual(self._count_outbox("period_close"), 1)
        self.assertEqual(self._count_closing_journals(), 0)

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
        self.assertEqual(self.ledger.journal_count, 2)

        reversal = self.ledger.reverse(
            posted.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
        )
        live = self.ledger.load_period_trial_balance(
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "2026-08",
        )
        self.assertEqual(reversal.reversal_of_journal_reference, posted.journal_reference)
        self.assertEqual(self.ledger.journal_count, 3)
        self.assertEqual(live["balance_source_code"], "live")
        self.assertEqual(live["period_status_code"], "soft_closed")
        self.assertEqual(
            Decimal(str(self._trial_balance_line(live, "110100")["net_balance_amount"])),
            Decimal("25000"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(live, "410100")["net_balance_amount"])),
            Decimal("-25000"),
        )

        hard = self._close_period()
        snapshot = self.ledger.load_period_trial_balance(
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "2026-08",
        )
        snapshots_after_hard = self._count_table("accounting_reporting.trial_balance_snapshot")
        outbox_after_hard = self._count_outbox("period_close")
        self.assertFalse(hard.replayed)
        self.assertEqual(hard.period_status_code, "hard_closed")
        self.assertTrue(hard.snapshot_record_id)
        self.assertEqual(hard.source_journal_count, 4)
        self.assertEqual(snapshots_after_hard, 1)
        self.assertEqual(outbox_after_hard, 2)
        self.assertEqual(self._count_closing_journals(), 1)
        self.assertEqual(snapshot["balance_source_code"], "snapshot")
        self.assertEqual(snapshot["period_status_code"], "hard_closed")
        self.assertEqual(
            Decimal(str(self._trial_balance_line(snapshot, "110100")["net_balance_amount"])),
            Decimal("25000"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(snapshot, "410100")["net_balance_amount"])),
            Decimal("0"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(snapshot, "310100")["credit_amount"])),
            Decimal("25000"),
        )

        with self.assertRaisesRegex(
            AccountingValidationError,
            "Reverse into an open or soft-closed period",
        ):
            self.ledger.reverse(
                later_posted.journal_reference,
                date(2026, 8, 31),
                "billing_correction",
                self.policy,
            )
        with self.assertRaisesRegex(
            AccountingValidationError,
            "cannot be soft-closed",
        ):
            self._close_period(period_status_code="soft_closed")
        self.assertEqual(self.ledger.journal_count, 4)
        self.assertEqual(
            self._count_table("accounting_reporting.trial_balance_snapshot"),
            snapshots_after_hard,
        )
        self.assertEqual(self._count_outbox("period_close"), outbox_after_hard)
        self.assertEqual(self._period_status("2026-08"), "hard_closed")

    def test_close_empty_period_and_catalog_failures_name_the_next_action(self) -> None:
        """Empty-period close is durable; catalog and status errors name the retry action."""
        empty = self._close_period()
        empty_income = lookup_financial_statement(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
            "income_statement",
        )
        empty_sheet = lookup_financial_statement(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
            "balance_sheet",
        )
        self.assertEqual(empty.source_journal_count, 0)
        self.assertEqual(self._count_closing_journals(), 0)
        self.assertEqual(self._count_table("accounting_reporting.trial_balance_snapshot"), 1)
        self.assertEqual(self._count_table("accounting_reporting.trial_balance_line"), 0)
        self.assertEqual(self._period_status("2026-08"), "hard_closed")
        self.assertEqual(empty_income["statement_lines"], [])
        self.assertEqual(empty_income["net_income_amount"], "0")
        self.assertEqual(empty_sheet["statement_lines"], [])
        self.assertEqual(empty_sheet["net_income_amount"], "0")

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

    def test_hard_close_parks_earnings_and_rejects_billing_retained_earnings(self) -> None:
        """Hard-close posts one AIS closing journal; Billing cannot use retained_earnings."""
        self.ledger.post(self._two_line_proposal(), self.policy)
        soft = self._close_period(period_status_code="soft_closed")
        self.assertEqual(soft.period_status_code, "soft_closed")
        self.assertEqual(self._count_closing_journals(), 0)
        live = self.ledger.load_period_trial_balance(
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "2026-08",
        )
        self.assertNotIn(
            "310100",
            {str(item["chart_account_code"]) for item in live["lines"]},
        )

        hard = self._close_period()
        replayed = self._close_period()
        snapshot = self.ledger.load_period_trial_balance(
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "2026-08",
        )
        self.assertFalse(hard.replayed)
        self.assertTrue(replayed.replayed)
        self.assertEqual(self._count_closing_journals(), 1)
        self.assertEqual(hard.source_journal_count, 2)
        self.assertEqual(
            Decimal(str(self._trial_balance_line(snapshot, "410100")["net_balance_amount"])),
            Decimal("0"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(snapshot, "310100")["credit_amount"])),
            Decimal("25000"),
        )

        loss_ledger_journals = self.ledger.journal_count
        self._seed_additional_period("2026-09", date(2026, 9, 1), date(2026, 9, 30))
        self.ledger.post(
            self._two_line_proposal(
                proposal_id=str(uuid.uuid4()),
                idempotency_key="credit-loss-v1",
                accounting_date=date(2026, 9, 15),
                transaction_date=date(2026, 9, 15),
                source_payload_hash="sha256:" + "9" * 64,
                lines=(
                    JournalLineProposal(1, "usage_revenue", "4000", "0"),
                    JournalLineProposal(2, "accounts_receivable", "0", "4000"),
                ),
            ),
            self._policy_with(
                open_period_start=date(2026, 9, 1),
                open_period_end=date(2026, 9, 30),
            ),
        )
        september = self._close_period(period_code="2026-09")
        self.assertEqual(september.source_journal_count, 4)
        self.assertEqual(self._count_closing_journals(), 2)
        self.assertEqual(self.ledger.journal_count, loss_ledger_journals + 2)

        with self.assertRaisesRegex(
            AccountingValidationError,
            "retained_earnings is reserved for AIS period-close",
        ):
            self.ledger.post(
                self._two_line_proposal(
                    proposal_id=str(uuid.uuid4()),
                    idempotency_key="billing-retained-earnings",
                    source_payload_hash="sha256:" + "8" * 64,
                    lines=(
                        JournalLineProposal(1, "accounts_receivable", "1000", "0"),
                        JournalLineProposal(2, "retained_earnings", "0", "1000"),
                    ),
                ),
                self._policy_with(
                    chart_account_mapping={
                        **self.policy.chart_account_mapping,
                        "retained_earnings": "310100",
                    }
                ),
            )

    def test_hard_close_without_retained_earnings_mapping_writes_zero_rows(self) -> None:
        """Missing retained_earnings catalog fails closed and writes no close rows."""
        self.ledger.post(self._two_line_proposal(), self.policy)
        self._delete_role_mapping("retained_earnings")
        with self.assertRaisesRegex(
            AccountingValidationError,
            "retained_earnings",
        ):
            self._close_period()
        self.assertEqual(self._period_status("2026-08"), "open")
        self.assertEqual(self._count_closing_journals(), 0)
        self.assertEqual(self._count_table("accounting_reporting.trial_balance_snapshot"), 0)
        self.assertEqual(self.ledger.journal_count, 1)

    def test_hard_close_clears_offsetting_revenue_and_expense_without_earnings_plug(
        self,
    ) -> None:
        """Zero net income still closes income-statement balances when they are non-zero."""
        self.ledger.post(self._two_line_proposal(), self.policy)
        self.ledger.post(
            self._two_line_proposal(
                proposal_id=str(uuid.uuid4()),
                idempotency_key="write-off-expense-v1",
                source_payload_hash="sha256:" + "7" * 64,
                lines=(
                    JournalLineProposal(1, "write_off_expense", "25000", "0"),
                    JournalLineProposal(2, "accounts_receivable", "0", "25000"),
                ),
            ),
            self.policy,
        )
        receipt = self._close_period()
        snapshot = self.ledger.load_period_trial_balance(
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "2026-08",
        )
        self.assertEqual(self._count_closing_journals(), 1)
        self.assertEqual(receipt.source_journal_count, 3)
        self.assertEqual(
            Decimal(str(self._trial_balance_line(snapshot, "410100")["net_balance_amount"])),
            Decimal("0"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(snapshot, "510100")["net_balance_amount"])),
            Decimal("0"),
        )
        self.assertNotIn(
            "310100",
            {str(item["chart_account_code"]) for item in snapshot["lines"]},
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

    def test_post_proposal_posts_billing_cash_receipt_from_catalog(self) -> None:
        """A Billing cash receipt proposal posts debit cash / credit AR from catalog mapping."""
        payload = self._billing_cash_payload()
        proposal = ingest_journal_proposal(payload)

        policy = self.ledger.resolve_accounting_policy(proposal)
        receipt = self.ledger.post_proposal(proposal)
        replayed = self.ledger.post_proposal(proposal)
        balances = self.ledger.trial_balance(
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            date(2026, 8, 31),
        )

        self.assertEqual(policy.chart_account_mapping["cash_receipt"], "110200")
        self.assertEqual(policy.chart_account_mapping["accounts_receivable"], "110100")
        self.assertEqual(policy.accounting_policy_version, "ifrs-v1")
        self.assertEqual(policy.posting_rule_version, "billing-issued-v1")
        self.assertEqual(policy.intended_book_role_code, "primary_statutory")
        self.assertNotIn("110200", json.dumps(payload["lines"]))
        self.assertNotIn("110100", json.dumps(payload["lines"]))
        self.assertEqual(receipt, replayed)
        self.assertEqual(self.ledger.journal_count, 1)
        self.assertEqual(self._count_table("accounting_core.general_journal"), 1)
        self.assertEqual(self._posted_chart_accounts(), {"110200", "110100"})
        self.assertEqual(balances["110200"].debit_total, Decimal("18000"))
        self.assertEqual(balances["110100"].credit_total, Decimal("18000"))

    def test_accept_and_http_post_billing_proposal_replay_and_reject_zero_rows(self) -> None:
        """HTTP POST accepts a Billing proposal, replays the receipt, and writes zero rows on reject."""
        payload = self._billing_validated_payload()
        first = accept_journal_proposal(payload, DATABASE_URL, self.policy.tenant_reference)
        replayed = accept_journal_proposal(payload, DATABASE_URL, self.policy.tenant_reference)
        self._assert_published_receipt(first, payload)
        self.assertEqual(first, replayed)
        self.assertEqual(self._posted_chart_accounts(), {"110100", "410100"})
        self.assertEqual(self._count_table("accounting_core.general_journal"), 1)

        server = self._start_http_server()
        status, document = self._http_json("POST", "/journal-proposals", payload)
        self.assertEqual(status, 200)
        self.assertEqual(document, first)
        replay_status, replay_document = self._http_json("POST", "/journal-proposals", payload)
        self.assertEqual(replay_status, 200)
        self.assertEqual(replay_document, first)

        draft = self._billing_validated_payload(proposal_status="draft")
        reject_row = {
            "proposal_contract_version": 1,
            "journal_proposal_outcome_code": "rejected",
            "rejection_reason_code": "invoice_draft_not_found",
            "tenant_reference": self.policy.tenant_reference,
        }
        self.ledger.close_fiscal_period(
            legal_entity_reference=self.policy.legal_entity_reference,
            accounting_book_reference=self.policy.accounting_book_reference,
            period_code="2026-08",
            snapshot_currency_code="KRW",
        )
        journals_after_close = self._count_table("accounting_core.general_journal")
        proposals_after_close = self._count_table(
            "accounting_integration.journal_proposal_record"
        )
        closed = self._billing_validated_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=f"{self.policy.tenant_reference}:invoice_draft:closed:sha256:{'b' * 64}:v1",
            source_payload_hash="sha256:" + "b" * 64,
        )
        draft_status, _draft_body = self._http_json("POST", "/journal-proposals", draft)
        reject_status, _reject_body = self._http_json("POST", "/journal-proposals", reject_row)
        closed_status, _closed_body = self._http_json("POST", "/journal-proposals", closed)
        self.assertEqual(draft_status, 422)
        self.assertEqual(reject_status, 422)
        self.assertEqual(closed_status, 422)
        self.assertEqual(journals_after_close, 2)
        self.assertEqual(self._count_closing_journals(), 1)
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_after_close)
        self.assertEqual(
            self._count_table("accounting_integration.journal_proposal_record"),
            proposals_after_close,
        )
        server.shutdown()

    def test_http_posts_and_looks_up_invoice_and_cash_receipts(self) -> None:
        """HTTP posts invoice and cash proposals, then looks up the same receipts by key."""
        invoice = self._billing_validated_payload()
        cash = self._billing_cash_payload()
        server = self._start_http_server()

        invoice_status, invoice_receipt = self._http_json("POST", "/journal-proposals", invoice)
        cash_status, cash_receipt = self._http_json("POST", "/journal-proposals", cash)
        replay_status, replay_receipt = self._http_json("POST", "/journal-proposals", invoice)
        invoice_lookup_status, invoice_lookup = self._http_lookup(str(invoice["idempotency_key"]))
        cash_lookup_status, cash_lookup = self._http_lookup(str(cash["idempotency_key"]))
        replay_lookup_status, replay_lookup = self._http_lookup(str(invoice["idempotency_key"]))
        library_lookup = lookup_published_receipt(
            DATABASE_URL,
            self.policy.tenant_reference,
            str(cash["idempotency_key"]),
        )

        self.assertEqual(invoice_status, 200)
        self.assertEqual(cash_status, 200)
        self.assertEqual(replay_status, 200)
        self.assertEqual(invoice_lookup_status, 200)
        self.assertEqual(cash_lookup_status, 200)
        self.assertEqual(replay_lookup_status, 200)
        self._assert_published_receipt(invoice_receipt, invoice)
        self._assert_published_receipt(cash_receipt, cash)
        self.assertEqual(invoice_receipt, replay_receipt)
        self.assertEqual(invoice_receipt, invoice_lookup)
        self.assertEqual(invoice_receipt, replay_lookup)
        self.assertEqual(cash_receipt, cash_lookup)
        self.assertEqual(cash_receipt, library_lookup)
        self.assertEqual(self._count_table("accounting_core.general_journal"), 2)
        self.assertEqual(self._posted_chart_accounts(), {"110100", "410100", "110200"})

        journals_before = self._count_table("accounting_core.general_journal")
        receipts_before = self._count_table("accounting_integration.posting_receipt")
        cross_status, cross_body = self._http_lookup(
            str(invoice["idempotency_key"]),
            tenant_header="urn:cwl:tenant_other",
        )
        unknown_status, unknown_body = self._http_lookup(
            f"{self.policy.tenant_reference}:cash_receipt:missing:sha256:{'d' * 64}:v1"
        )
        missing_key_status, _missing_key = self._http_json("GET", "/posting-receipts", None)
        empty_key_status, _empty_key = self._http_json(
            "GET", "/posting-receipts?idempotency_key=", None
        )
        missing_header_status, _missing_header = self._http_lookup(
            str(invoice["idempotency_key"]),
            tenant_header=None,
        )
        unknown_get_status, _unknown_get = self._http_json("GET", "/unknown", None)
        with self.assertRaisesRegex(AccountingValidationError, "Supply the Billing idempotency key"):
            lookup_published_receipt(DATABASE_URL, self.policy.tenant_reference, "")
        with self.assertRaisesRegex(AccountingValidationError, "Accept the proposal"):
            lookup_published_receipt(
                DATABASE_URL,
                self.policy.tenant_reference,
                f"{self.policy.tenant_reference}:invoice_draft:missing:sha256:{'e' * 64}:v1",
            )

        self.assertEqual(cross_status, 403)
        self.assertIn("Send the lookup to that tenant's endpoint", str(cross_body["error_message"]))
        self.assertEqual(unknown_status, 404)
        self.assertIn("Accept the proposal", str(unknown_body["error_message"]))
        self.assertEqual(missing_key_status, 400)
        self.assertEqual(empty_key_status, 400)
        self.assertEqual(missing_header_status, 400)
        self.assertEqual(unknown_get_status, 404)
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before)
        self.assertEqual(self._count_table("accounting_integration.posting_receipt"), receipts_before)
        server.shutdown()

    def test_http_posts_and_pulls_billing_credit_adjustment(self) -> None:
        """Billing #17 credit_adjustment uses the published proposal path and pinned key."""
        credit = self._billing_credit_payload()
        draft = self._billing_credit_payload(
            proposal_id=str(uuid.uuid4()),
            proposal_status="draft",
            idempotency_key=(
                f"{self.policy.tenant_reference}:credit_adjustment:draft:sha256:{'f' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "f" * 64,
            proposed_at="2026-08-30T00:00:00Z",
        )
        exported = self._billing_credit_payload(
            proposal_id=str(uuid.uuid4()),
            proposal_status="exported",
            idempotency_key=(
                f"{self.policy.tenant_reference}:credit_adjustment:exported:sha256:{'e' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "e" * 64,
            proposed_at="2026-08-30T12:00:00Z",
        )
        rejected = self._billing_credit_payload(
            proposal_id=str(uuid.uuid4()),
            proposal_status="rejected",
            idempotency_key=(
                f"{self.policy.tenant_reference}:credit_adjustment:rejected:sha256:{'d' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "d" * 64,
            proposed_at="2026-08-30T18:00:00Z",
        )
        billing_url = self._start_fake_billing([draft, exported, rejected, credit])
        server = self._start_http_server()

        self.assertEqual(
            credit["idempotency_key"],
            (
                f"{self.policy.tenant_reference}:credit_adjustment:"
                f"11111111-1111-1111-1111-111111111111:{credit['source_payload_hash']}:v1"
            ),
        )
        self.assertEqual(credit["lines"][0]["account_role_code"], "usage_revenue")
        self.assertEqual(credit["lines"][1]["account_role_code"], "accounts_receivable")

        pull_status, pull_body = self._http_json(
            "POST",
            "/billing-proposal-pulls",
            {"tenant_reference": self.policy.tenant_reference, "billing_base_url": billing_url},
        )
        post_status, post_receipt = self._http_json("POST", "/journal-proposals", credit)
        lookup_status, lookup = self._http_lookup(str(credit["idempotency_key"]))
        replayed = accept_pulled_proposals(
            billing_url, DATABASE_URL, self.policy.tenant_reference
        )

        self.assertEqual(pull_status, 200)
        self.assertEqual(post_status, 200)
        self.assertEqual(lookup_status, 200)
        self.assertEqual(len(pull_body["posting_receipts"]), 1)
        self._assert_published_receipt(post_receipt, credit)
        self.assertEqual(post_receipt, pull_body["posting_receipts"][0])
        self.assertEqual(post_receipt, lookup)
        self.assertEqual(replayed["posting_receipts"], [post_receipt])
        self.assertEqual(replayed["rejected_proposals"], [])
        self.assertEqual(self._count_table("accounting_core.general_journal"), 1)
        self.assertEqual(self._posted_chart_accounts(), {"110100", "410100"})

        journals_before = self._count_table("accounting_core.general_journal")
        cross_post = self._http_json(
            "POST",
            "/journal-proposals",
            credit,
            tenant_header="urn:cwl:tenant_other",
        )
        cross_lookup = self._http_lookup(
            str(credit["idempotency_key"]),
            tenant_header="urn:cwl:tenant_other",
        )
        self.assertEqual(cross_post[0], 403)
        self.assertEqual(cross_lookup[0], 403)
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before)
        server.shutdown()

    def test_http_posts_and_pulls_billing_taxed_credit(self) -> None:
        """Billing #20 taxed credit posts Billing's three-line unwind without AIS tax math."""
        taxed_credit = self._billing_taxed_credit_payload()
        untaxed_credit = self._billing_credit_payload()
        billing_url = self._start_fake_billing([taxed_credit])
        server = self._start_http_server()

        self.assertEqual(
            taxed_credit["idempotency_key"],
            (
                f"{self.policy.tenant_reference}:credit_adjustment:"
                f"{taxed_credit['proposal_id']}:{taxed_credit['source_payload_hash']}:v1"
            ),
        )
        self.assertNotIn(":tax_", str(taxed_credit["idempotency_key"]))
        self.assertNotIn("tax_receivable", json.dumps(taxed_credit))
        self.assertEqual(taxed_credit["lines"][0]["account_role_code"], "usage_revenue")
        self.assertEqual(taxed_credit["lines"][1]["account_role_code"], "tax_payable")
        self.assertEqual(taxed_credit["lines"][2]["account_role_code"], "accounts_receivable")

        production = Path(__file__).resolve().parents[1] / "src" / "accounting_information_platform"
        for path in production.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("credit_tax_amount", text)
            self.assertNotIn("tax_receivable", text)

        pull_status, pull_body = self._http_json(
            "POST",
            "/billing-proposal-pulls",
            {"tenant_reference": self.policy.tenant_reference, "billing_base_url": billing_url},
        )
        post_status, post_receipt = self._http_json("POST", "/journal-proposals", taxed_credit)
        replay_status, replay_receipt = self._http_json("POST", "/journal-proposals", taxed_credit)
        journal_status, journal = self._http_journal(
            idempotency_key=str(taxed_credit["idempotency_key"])
        )
        untaxed_status, untaxed_receipt = self._http_json(
            "POST", "/journal-proposals", untaxed_credit
        )
        untaxed_journal_status, untaxed_journal = self._http_journal(
            idempotency_key=str(untaxed_credit["idempotency_key"])
        )
        by_code = {str(item["chart_account_code"]): item for item in journal["lines"]}

        self.assertEqual(pull_status, 200)
        self.assertEqual(post_status, 200)
        self.assertEqual(replay_status, 200)
        self.assertEqual(post_receipt, replay_receipt)
        self.assertEqual(post_receipt, pull_body["posting_receipts"][0])
        self.assertEqual(post_receipt["line_count"], 3)
        self.assertEqual(post_receipt["idempotency_key"], taxed_credit["idempotency_key"])
        self.assertEqual(journal_status, 200)
        self.assertEqual(set(by_code), {"410100", "210100", "110100"})
        self.assertEqual(Decimal(str(by_code["410100"]["debit_amount"])), Decimal("25000"))
        self.assertEqual(Decimal(str(by_code["410100"]["credit_amount"])), Decimal("0"))
        self.assertEqual(by_code["410100"]["account_role_code"], "usage_revenue")
        self.assertEqual(Decimal(str(by_code["210100"]["debit_amount"])), Decimal("2500"))
        self.assertEqual(Decimal(str(by_code["210100"]["credit_amount"])), Decimal("0"))
        self.assertEqual(by_code["210100"]["account_role_code"], "tax_payable")
        self.assertEqual(Decimal(str(by_code["110100"]["debit_amount"])), Decimal("0"))
        self.assertEqual(Decimal(str(by_code["110100"]["credit_amount"])), Decimal("27500"))
        self.assertEqual(by_code["110100"]["account_role_code"], "accounts_receivable")
        self.assertEqual(untaxed_status, 200)
        self.assertEqual(untaxed_journal_status, 200)
        self.assertEqual(untaxed_receipt["line_count"], 2)
        untaxed_by_code = {
            str(item["chart_account_code"]): item for item in untaxed_journal["lines"]
        }
        self.assertEqual(set(untaxed_by_code), {"410100", "110100"})
        self.assertEqual(Decimal(str(untaxed_by_code["410100"]["debit_amount"])), Decimal("4000"))
        self.assertEqual(Decimal(str(untaxed_by_code["110100"]["credit_amount"])), Decimal("4000"))
        self.assertNotIn("210100", untaxed_by_code)
        self.assertEqual(self._count_table("accounting_core.general_journal"), 2)

        journals_before = self._count_table("accounting_core.general_journal")
        cross_post = self._http_json(
            "POST",
            "/journal-proposals",
            taxed_credit,
            tenant_header="urn:cwl:tenant_other",
        )
        self.assertEqual(cross_post[0], 403)
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before)
        server.shutdown()

    def test_http_posts_and_pulls_billing_taxed_invoice(self) -> None:
        """Billing #19 taxed invoice posts AR inclusive, revenue exclusive, and tax_payable."""
        taxed = self._billing_taxed_payload()
        untaxed = self._billing_validated_payload()
        billing_url = self._start_fake_billing([taxed])
        server = self._start_http_server()

        self.assertEqual(
            taxed["idempotency_key"],
            (
                f"{self.policy.tenant_reference}:invoice_draft:"
                f"{taxed['proposal_id']}:{taxed['source_payload_hash']}:v1"
            ),
        )
        self.assertNotIn(":tax_", str(taxed["idempotency_key"]))
        self.assertEqual(taxed["lines"][0]["account_role_code"], "accounts_receivable")
        self.assertEqual(taxed["lines"][1]["account_role_code"], "usage_revenue")
        self.assertEqual(taxed["lines"][2]["account_role_code"], "tax_payable")

        mapping_status, mappings = self._http_account_role_mappings()
        pull_status, pull_body = self._http_json(
            "POST",
            "/billing-proposal-pulls",
            {"tenant_reference": self.policy.tenant_reference, "billing_base_url": billing_url},
        )
        post_status, post_receipt = self._http_json("POST", "/journal-proposals", taxed)
        replay_status, replay_receipt = self._http_json("POST", "/journal-proposals", taxed)
        journal_status, journal = self._http_journal(
            idempotency_key=str(taxed["idempotency_key"])
        )
        untaxed_status, untaxed_receipt = self._http_json("POST", "/journal-proposals", untaxed)
        untaxed_journal_status, untaxed_journal = self._http_journal(
            idempotency_key=str(untaxed["idempotency_key"])
        )
        by_code = {str(item["chart_account_code"]): item for item in journal["lines"]}
        mapping_by_role = {
            str(item["account_role_code"]): item for item in mappings["mappings"]
        }

        self.assertEqual(mapping_status, 200)
        self.assertEqual(mapping_by_role["tax_payable"]["chart_account_code"], "210100")
        self.assertEqual(pull_status, 200)
        self.assertEqual(post_status, 200)
        self.assertEqual(replay_status, 200)
        self.assertEqual(post_receipt, replay_receipt)
        self.assertEqual(post_receipt, pull_body["posting_receipts"][0])
        self.assertEqual(post_receipt["line_count"], 3)
        self.assertEqual(post_receipt["idempotency_key"], taxed["idempotency_key"])
        self.assertEqual(journal_status, 200)
        self.assertEqual(set(by_code), {"110100", "410100", "210100"})
        self.assertEqual(Decimal(str(by_code["110100"]["debit_amount"])), Decimal("27500"))
        self.assertEqual(Decimal(str(by_code["410100"]["credit_amount"])), Decimal("25000"))
        self.assertEqual(Decimal(str(by_code["210100"]["credit_amount"])), Decimal("2500"))
        self.assertEqual(by_code["210100"]["account_role_code"], "tax_payable")
        self.assertEqual(untaxed_status, 200)
        self.assertEqual(untaxed_journal_status, 200)
        self.assertEqual(untaxed_receipt["line_count"], 2)
        self.assertEqual(
            {str(item["chart_account_code"]) for item in untaxed_journal["lines"]},
            {"110100", "410100"},
        )
        self.assertEqual(self._count_table("accounting_core.general_journal"), 2)

        journals_before = self._count_table("accounting_core.general_journal")
        missing_taxed_id = "019d7b92-4dd3-7a7f-b61c-962c0f4bf616"
        missing_hash = "sha256:" + "2" * 64
        missing_taxed = self._billing_taxed_payload(
            proposal_id=missing_taxed_id,
            source_payload_hash=missing_hash,
            idempotency_key=(
                f"{self.policy.tenant_reference}:invoice_draft:"
                f"{missing_taxed_id}:{missing_hash}:v1"
            ),
        )
        self._delete_role_mapping("tax_payable")
        missing_mapping = self._http_json("POST", "/journal-proposals", missing_taxed)
        self._seed_role_mapping("tax_payable", "210100")
        cross_post = self._http_json(
            "POST",
            "/journal-proposals",
            taxed,
            tenant_header="urn:cwl:tenant_other",
        )
        self.assertEqual(missing_mapping[0], 422)
        self.assertIn("tax_payable", str(missing_mapping[1]["error_message"]))
        self.assertIn("Create the account_role_mapping row", str(missing_mapping[1]["error_message"]))
        self.assertEqual(cross_post[0], 403)
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before)
        server.shutdown()

    def test_http_posts_and_pulls_billing_collection_write_off(self) -> None:
        """Billing #51 collection write-off posts expense / AR and parks into RE on hard-close."""
        write_off = self._billing_write_off_payload()
        unknown_role = self._billing_write_off_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:collection_write_off:unknown:"
                f"sha256:{'9' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "9" * 64,
            source_event_references=(
                f"{self.policy.tenant_reference}:collection_write_off:unknown",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "not_a_catalog_role",
                    "debit_amount": "7000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0",
                    "credit_amount": "7000",
                },
            ],
        )
        billing_url = self._start_fake_billing([write_off])
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")

        self.assertEqual(
            write_off["idempotency_key"],
            (
                f"{self.policy.tenant_reference}:collection_write_off:"
                f"{write_off['proposal_id']}:{write_off['source_payload_hash']}:v1"
            ),
        )
        self.assertEqual(write_off["proposal_status"], "validated")
        self.assertEqual(write_off["intended_book_role_code"], "primary_statutory")
        self.assertEqual(write_off["lines"][0]["account_role_code"], "write_off_expense")
        self.assertEqual(write_off["lines"][1]["account_role_code"], "accounts_receivable")
        self.assertNotIn("510100", json.dumps(write_off["lines"]))
        self.assertNotIn("110100", json.dumps(write_off["lines"]))

        mapping_status, mappings = self._http_account_role_mappings()
        pull_status, pull_body = self._http_json(
            "POST",
            "/billing-proposal-pulls",
            {"tenant_reference": self.policy.tenant_reference, "billing_base_url": billing_url},
        )
        post_status, post_receipt = self._http_json("POST", "/journal-proposals", write_off)
        replay_status, replay_receipt = self._http_json("POST", "/journal-proposals", write_off)
        journal_status, journal = self._http_journal(
            idempotency_key=str(write_off["idempotency_key"])
        )
        billing_list_status, billing_list = self._http_period_journals(
            journal_source_code="billing"
        )
        unknown_status, unknown_body = self._http_json("POST", "/journal-proposals", unknown_role)
        by_code = {str(item["chart_account_code"]): item for item in journal["lines"]}
        mapping_by_role = {
            str(item["account_role_code"]): item for item in mappings["mappings"]
        }

        self.assertEqual(mapping_status, 200)
        self.assertEqual(mapping_by_role["write_off_expense"]["chart_account_code"], "510100")
        self.assertEqual(pull_status, 200)
        self.assertEqual(post_status, 200)
        self.assertEqual(replay_status, 200)
        self.assertEqual(post_receipt, replay_receipt)
        self.assertEqual(post_receipt, pull_body["posting_receipts"][0])
        self.assertEqual(post_receipt["line_count"], 2)
        self.assertEqual(post_receipt["idempotency_key"], write_off["idempotency_key"])
        self.assertEqual(journal_status, 200)
        self.assertEqual(set(by_code), {"510100", "110100"})
        self.assertEqual(by_code["510100"]["account_role_code"], "write_off_expense")
        self.assertEqual(Decimal(str(by_code["510100"]["debit_amount"])), Decimal("7000"))
        self.assertEqual(Decimal(str(by_code["110100"]["credit_amount"])), Decimal("7000"))
        self.assertEqual(billing_list_status, 200)
        self.assertEqual(
            [item["idempotency_key"] for item in billing_list["journals"]],
            [write_off["idempotency_key"]],
        )
        self.assertFalse(
            str(journal["journal_reference"]).startswith(
                "urn:cwl:accounting:general_journal:period_closing:"
            )
        )
        self.assertEqual(unknown_status, 422)
        self.assertIn("not_a_catalog_role", str(unknown_body["error_message"]))

        income_status, income = self._http_financial_statement("income_statement")
        income_by_code = {
            str(item["chart_account_code"]): item for item in income["statement_lines"]
        }
        self.assertEqual(income_status, 200)
        self.assertEqual(income_by_code["510100"]["account_role_code"], "write_off_expense")
        self.assertEqual(income_by_code["510100"]["account_class_code"], "expense")
        self.assertEqual(Decimal(str(income_by_code["510100"]["debit_amount"])), Decimal("7000"))

        soft_status, _soft = self._http_json(
            "POST",
            "/period-closes",
            self._period_close_payload(period_status_code="soft_closed"),
        )
        replay_after_soft_status, replay_after_soft = self._http_json(
            "POST", "/journal-proposals", write_off
        )
        hard_status, _hard = self._http_json(
            "POST", "/period-closes", self._period_close_payload()
        )
        closed_income_status, closed_income = self._http_financial_statement("income_statement")
        closed_tb_status, closed_tb = self._http_json(
            "GET",
            "/trial-balances?"
            + urllib.parse.urlencode(
                {
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "book_reference": self.policy.accounting_book_reference,
                    "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
                }
            ),
            None,
        )
        closed_income_by_code = {
            str(item["chart_account_code"]): item for item in closed_income["statement_lines"]
        }
        closing_list_status, closing_list = self._http_period_journals(
            journal_source_code="period_closing"
        )

        self.assertEqual(soft_status, 200)
        self.assertEqual(replay_after_soft_status, 200)
        self.assertEqual(replay_after_soft, post_receipt)
        self.assertEqual(hard_status, 200)
        self.assertEqual(closed_income_status, 200)
        self.assertEqual(closed_tb_status, 200)
        self.assertEqual(self._count_closing_journals(), 1)
        self.assertEqual(
            Decimal(str(closed_income_by_code["510100"]["debit_amount"])),
            Decimal("7000"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(closed_tb, "510100")["net_balance_amount"])),
            Decimal("0"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(closed_tb, "310100")["debit_amount"])),
            Decimal("7000"),
        )
        self.assertEqual(closing_list_status, 200)
        self.assertEqual(len(closing_list["journals"]), 1)
        self.assertNotEqual(
            closing_list["journals"][0]["idempotency_key"],
            write_off["idempotency_key"],
        )
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before + 2)
        server.shutdown()

    def test_http_posts_and_pulls_billing_issued_invoice_void(self) -> None:
        """Billing issued-invoice-void uses the published key and opposite invoice roles."""
        invoice = self._billing_taxed_payload()
        void = self._billing_issued_invoice_void_payload()
        conflict = self._billing_issued_invoice_void_payload(
            source_payload_hash="sha256:" + "9" * 64,
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "10000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0",
                    "credit_amount": "10000",
                },
            ],
        )
        billing_url = self._start_fake_billing([void])
        server = self._start_http_server()

        issued_invoice_void_id = "019d7b92-9dd6-7a7f-b61c-962c0f4bf630"
        void_source_payload_hash = "sha256:" + "7" * 64
        issued_invoice_void_contract_version = 1
        self.assertEqual(
            void["idempotency_key"],
            (
                f"{self.policy.tenant_reference}:issued_invoice_void:"
                f"{issued_invoice_void_id}:{void_source_payload_hash}"
                f":v{issued_invoice_void_contract_version}"
            ),
        )
        self.assertNotEqual(void["proposal_id"], issued_invoice_void_id)
        self.assertNotEqual(void["source_payload_hash"], void_source_payload_hash)
        self.assertNotEqual(
            void["idempotency_key"],
            (
                f"{self.policy.tenant_reference}:issued_invoice_void:"
                f"{void['proposal_id']}:{void['source_payload_hash']}:v1"
            ),
        )
        self.assertEqual(void["proposal_status"], "validated")
        self.assertEqual(void["intended_book_role_code"], "primary_statutory")
        self.assertEqual(
            [line["account_role_code"] for line in void["lines"]],
            ["usage_revenue", "tax_payable", "accounts_receivable"],
        )
        self.assertEqual(
            list(void["source_event_references"]),
            [f"{self.policy.tenant_reference}:issued_invoice_void:{issued_invoice_void_id}"],
        )
        self.assertNotIn("journal_entry_id", json.dumps(void))
        self.assertNotIn("reversed_journal_proposal_id", json.dumps(void))
        self.assertNotIn("invoice_draft_id", json.dumps(void))
        self.assertNotIn("110100", json.dumps(void["lines"]))

        invoice_status, _invoice = self._http_json("POST", "/journal-proposals", invoice)
        before_status, before = self._http_receivable_aging()
        before_payable_status, before_payable = self._http_payable_aging()
        mapping_status, mappings = self._http_account_role_mappings()
        pull_status, pull_body = self._http_json(
            "POST",
            "/billing-proposal-pulls",
            {"tenant_reference": self.policy.tenant_reference, "billing_base_url": billing_url},
        )
        post_status, post_receipt = self._http_json("POST", "/journal-proposals", void)
        replay_status, replay_receipt = self._http_json("POST", "/journal-proposals", void)
        conflict_status, _conflict = self._http_json("POST", "/journal-proposals", conflict)
        after_status, after = self._http_receivable_aging()
        after_payable_status, after_payable = self._http_payable_aging()
        tax_balances_status, tax_balances = self._http_account_balances(
            chart_account_code="210100"
        )
        clearing_payable = self._http_payable_aging(chart_account_code="210200")
        journal_status, journal = self._http_journal(
            idempotency_key=str(void["idempotency_key"])
        )
        billing_list_status, billing_list = self._http_period_journals(
            journal_source_code="billing"
        )
        balances_status, balances = self._http_account_balances(chart_account_code="110100")
        by_code = {str(item["chart_account_code"]): item for item in journal["lines"]}
        mapping_by_role = {
            str(item["account_role_code"]): item for item in mappings["mappings"]
        }

        self.assertEqual(invoice_status, 200)
        self.assertEqual(before_status, 200)
        self.assertEqual(before["total_outstanding_amount"], "27500")
        self.assertEqual(before_payable_status, 200)
        self.assertEqual(before_payable["chart_account_code"], "210100")
        self.assertEqual(before_payable["total_outstanding_amount"], "2500")
        self.assertEqual(mapping_status, 200)
        self.assertEqual(mapping_by_role["accounts_receivable"]["chart_account_code"], "110100")
        self.assertEqual(mapping_by_role["usage_revenue"]["chart_account_code"], "410100")
        self.assertEqual(mapping_by_role["tax_payable"]["chart_account_code"], "210100")
        self.assertNotIn("issued_invoice_void", mapping_by_role)
        self.assertEqual(pull_status, 200)
        self.assertEqual(post_status, 200)
        self.assertEqual(replay_status, 200)
        self.assertEqual(conflict_status, 409)
        self.assertEqual(post_receipt, replay_receipt)
        self.assertEqual(post_receipt, pull_body["posting_receipts"][0])
        self.assertEqual(post_receipt["line_count"], 3)
        self.assertEqual(post_receipt["idempotency_key"], void["idempotency_key"])
        self.assertEqual(after_status, 200)
        self.assertEqual(after["total_outstanding_amount"], "0")
        self.assertEqual(after_payable_status, 200)
        self.assertEqual(tax_balances_status, 200)
        self.assertEqual(after_payable["chart_account_code"], "210100")
        self.assertEqual(after_payable["total_outstanding_amount"], "0")
        self.assertEqual(
            Decimal(str(after_payable["total_outstanding_amount"])),
            Decimal(str(tax_balances["account_balances"][0]["credit_amount"]))
            - Decimal(str(tax_balances["account_balances"][0]["debit_amount"])),
        )
        self.assertEqual(clearing_payable[0], 422)
        self.assertIn("tax_payable", str(clearing_payable[1]["error_message"]))
        self.assertEqual(balances_status, 200)
        self.assertEqual(
            Decimal(str(after["total_outstanding_amount"])),
            self._account_balance_net(balances, "110100"),
        )
        self.assertEqual(journal_status, 200)
        self.assertEqual(set(by_code), {"410100", "210100", "110100"})
        self.assertEqual(by_code["410100"]["account_role_code"], "usage_revenue")
        self.assertEqual(Decimal(str(by_code["410100"]["debit_amount"])), Decimal("25000"))
        self.assertEqual(by_code["210100"]["account_role_code"], "tax_payable")
        self.assertEqual(Decimal(str(by_code["210100"]["debit_amount"])), Decimal("2500"))
        self.assertEqual(by_code["110100"]["account_role_code"], "accounts_receivable")
        self.assertEqual(Decimal(str(by_code["110100"]["credit_amount"])), Decimal("27500"))
        self.assertEqual(billing_list_status, 200)
        self.assertIn(
            void["idempotency_key"],
            [item["idempotency_key"] for item in billing_list["journals"]],
        )
        self.assertFalse(
            str(journal["journal_reference"]).startswith(
                "urn:cwl:accounting:general_journal:period_closing:"
            )
        )
        server.shutdown()

    def test_http_posts_and_pulls_billing_unapplied_cash_refund(self) -> None:
        """Billing #59 refund maps unapplied_cash to 210200 and stays off payable aging."""
        refund = self._billing_unapplied_cash_refund_payload()
        park = self._billing_unapplied_cash_park_payload()
        later_refund = self._billing_unapplied_cash_refund_payload(
            proposal_id="019d7b92-8cc5-7a7f-b61c-962c0f4bf623",
            idempotency_key=(
                f"{self.policy.tenant_reference}:unapplied_cash_refund:"
                f"019d7b92-8cc5-7a7f-b61c-962c0f4bf623:sha256:{'2' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "2" * 64,
            source_event_references=(
                f"{self.policy.tenant_reference}:unapplied_cash_refund:"
                "019d7b92-8cc5-7a7f-b61c-962c0f4bf623",
            ),
        )
        unknown_role = self._billing_unapplied_cash_refund_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:unapplied_cash_refund:unknown:"
                f"sha256:{'9' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "9" * 64,
            source_event_references=(
                f"{self.policy.tenant_reference}:unapplied_cash_refund:unknown",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "not_a_catalog_role",
                    "debit_amount": "8000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "cash_receipt",
                    "debit_amount": "0",
                    "credit_amount": "8000",
                },
            ],
        )
        retained = self._billing_unapplied_cash_refund_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:unapplied_cash_refund:retained:"
                f"sha256:{'0' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "0" * 64,
            source_event_references=(
                f"{self.policy.tenant_reference}:unapplied_cash_refund:retained",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "retained_earnings",
                    "debit_amount": "8000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "cash_receipt",
                    "debit_amount": "0",
                    "credit_amount": "8000",
                },
            ],
        )
        billing_url = self._start_fake_billing([refund])
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")

        self.assertEqual(
            refund["idempotency_key"],
            (
                f"{self.policy.tenant_reference}:unapplied_cash_refund:"
                f"{refund['proposal_id']}:{refund['source_payload_hash']}:v1"
            ),
        )
        self.assertEqual(refund["proposal_status"], "validated")
        self.assertEqual(refund["intended_book_role_code"], "primary_statutory")
        self.assertEqual(refund["lines"][0]["account_role_code"], "unapplied_cash")
        self.assertEqual(refund["lines"][1]["account_role_code"], "cash_receipt")
        self.assertNotIn("210200", json.dumps(refund["lines"]))
        self.assertNotIn("110200", json.dumps(refund["lines"]))
        self.assertEqual(
            park["idempotency_key"],
            (
                f"{self.policy.tenant_reference}:unapplied_cash:"
                f"{park['proposal_id']}:{park['source_payload_hash']}:v1"
            ),
        )
        self.assertEqual(park["proposal_status"], "validated")
        self.assertEqual(park["lines"][0]["account_role_code"], "cash_receipt")
        self.assertEqual(park["lines"][1]["account_role_code"], "unapplied_cash")
        self.assertNotIn("210200", json.dumps(park["lines"]))
        self.assertNotIn("110200", json.dumps(park["lines"]))

        mapping_status, mappings = self._http_account_role_mappings()
        pull_status, pull_body = self._http_json(
            "POST",
            "/billing-proposal-pulls",
            {"tenant_reference": self.policy.tenant_reference, "billing_base_url": billing_url},
        )
        post_status, post_receipt = self._http_json("POST", "/journal-proposals", refund)
        replay_status, replay_receipt = self._http_json("POST", "/journal-proposals", refund)
        park_status, park_receipt = self._http_json("POST", "/journal-proposals", park)
        park_replay_status, park_replay = self._http_json("POST", "/journal-proposals", park)
        journal_status, journal = self._http_journal(
            idempotency_key=str(refund["idempotency_key"])
        )
        billing_list_status, billing_list = self._http_period_journals(
            journal_source_code="billing"
        )
        unknown_status, unknown_body = self._http_json("POST", "/journal-proposals", unknown_role)
        retained_status, retained_body = self._http_json("POST", "/journal-proposals", retained)
        payable_status, payable = self._http_payable_aging()
        clearing_payable = self._http_payable_aging(chart_account_code="210200")
        mapping_by_role = {
            str(item["account_role_code"]): item for item in mappings["mappings"]
        }

        self.assertEqual(mapping_status, 200)
        self.assertIn("unapplied_cash", mapping_by_role)
        self.assertEqual(mapping_by_role["unapplied_cash"]["chart_account_code"], "210200")
        self.assertEqual(post_status, 200)
        self.assertEqual(journal_status, 200)
        by_code = {str(item["chart_account_code"]): item for item in journal["lines"]}
        self.assertEqual(mapping_by_role["cash_receipt"]["chart_account_code"], "110200")
        self.assertEqual(mapping_by_role["tax_payable"]["chart_account_code"], "210100")
        self.assertEqual(pull_status, 200)
        self.assertEqual(post_status, 200)
        self.assertEqual(replay_status, 200)
        self.assertEqual(park_status, 200)
        self.assertEqual(park_replay_status, 200)
        self.assertEqual(post_receipt, replay_receipt)
        self.assertEqual(post_receipt, pull_body["posting_receipts"][0])
        self.assertEqual(post_receipt["line_count"], 2)
        self.assertEqual(post_receipt["idempotency_key"], refund["idempotency_key"])
        self.assertEqual(park_receipt, park_replay)
        self.assertEqual(park_receipt["idempotency_key"], park["idempotency_key"])
        self.assertNotEqual(park_receipt["idempotency_key"], refund["idempotency_key"])
        self.assertEqual(journal_status, 200)
        self.assertEqual(set(by_code), {"210200", "110200"})
        self.assertEqual(by_code["210200"]["account_role_code"], "unapplied_cash")
        self.assertEqual(Decimal(str(by_code["210200"]["debit_amount"])), Decimal("8000"))
        self.assertEqual(Decimal(str(by_code["110200"]["credit_amount"])), Decimal("8000"))
        self.assertEqual(billing_list_status, 200)
        self.assertEqual(
            {item["idempotency_key"] for item in billing_list["journals"]},
            {refund["idempotency_key"], park["idempotency_key"]},
        )
        self.assertFalse(
            str(journal["journal_reference"]).startswith(
                "urn:cwl:accounting:general_journal:period_closing:"
            )
        )
        self.assertEqual(unknown_status, 422)
        self.assertIn("not_a_catalog_role", str(unknown_body["error_message"]))
        self.assertEqual(retained_status, 422)
        self.assertIn("reserved for AIS period-close", str(retained_body["error_message"]))
        self.assertEqual(payable_status, 200)
        self.assertEqual(payable["chart_account_code"], "210100")
        self.assertEqual(payable["total_outstanding_amount"], "0")
        self.assertNotIn("party_reference", payable)
        self.assertEqual(clearing_payable[0], 422)
        self.assertIn("tax_payable", str(clearing_payable[1]["error_message"]))

        soft_status, _soft = self._http_json(
            "POST",
            "/period-closes",
            self._period_close_payload(period_status_code="soft_closed"),
        )
        replay_after_soft_status, replay_after_soft = self._http_json(
            "POST", "/journal-proposals", refund
        )
        rejected_status, rejected = self._http_json("POST", "/journal-proposals", later_refund)
        hard_status, _hard = self._http_json(
            "POST", "/period-closes", self._period_close_payload()
        )
        closed_tb_status, closed_tb = self._http_json(
            "GET",
            "/trial-balances?"
            + urllib.parse.urlencode(
                {
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "book_reference": self.policy.accounting_book_reference,
                    "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
                }
            ),
            None,
        )
        closed_balances_status, closed_balances = self._http_account_balances(
            chart_account_code="210200"
        )
        closing_list_status, closing_list = self._http_period_journals(
            journal_source_code="period_closing"
        )

        self.assertEqual(soft_status, 200)
        self.assertEqual(replay_after_soft_status, 200)
        self.assertEqual(replay_after_soft, post_receipt)
        self.assertEqual(rejected_status, 422)
        self.assertIn("open period", str(rejected["error_message"]))
        self.assertEqual(hard_status, 200)
        self.assertEqual(closed_tb_status, 200)
        self.assertEqual(closed_balances_status, 200)
        self.assertEqual(self._count_closing_journals(), 0)
        self.assertEqual(
            Decimal(str(self._trial_balance_line(closed_tb, "210200")["debit_amount"])),
            Decimal("8000"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(closed_tb, "210200")["credit_amount"])),
            Decimal("3000"),
        )
        self.assertEqual(self._trial_balance_account_net(closed_tb, "210200"), Decimal("5000"))
        self.assertIsNone(
            next(
                (
                    line
                    for line in closed_tb["lines"]
                    if isinstance(line, dict) and line.get("chart_account_code") == "310100"
                ),
                None,
            )
        )
        self.assertEqual(self._account_balance_net(closed_balances, "210200"), Decimal("5000"))
        self.assertEqual(closing_list_status, 200)
        self.assertEqual(closing_list["journals"], [])
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before + 2)
        server.shutdown()

    def test_collection_write_off_fifo_clears_and_exposes_unapplied_credit(self) -> None:
        """Write-off credits consume the oldest AR debit; excess stays unsigned."""
        self._seed_additional_period("2026-06", date(2026, 6, 1), date(2026, 6, 30))
        june_invoice = self._billing_validated_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:invoice_draft:june-write-off:"
                f"sha256:{'6' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "6" * 64,
            transaction_date="2026-06-01",
            accounting_date="2026-06-01",
            proposed_at="2026-06-01T00:00:00Z",
            source_event_references=(
                f"{self.policy.tenant_reference}:invoice_draft:june-write-off",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "5000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "0",
                    "credit_amount": "5000",
                },
            ],
        )
        august_invoice = self._billing_validated_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:invoice_draft:august-write-off:"
                f"sha256:{'8' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "8" * 64,
            transaction_date="2026-08-15",
            accounting_date="2026-08-15",
            proposed_at="2026-08-15T00:00:00Z",
            source_event_references=(
                f"{self.policy.tenant_reference}:invoice_draft:august-write-off",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "12000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "0",
                    "credit_amount": "12000",
                },
            ],
        )
        oldest_write_off = self._billing_write_off_payload(
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "write_off_expense",
                    "debit_amount": "5000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0",
                    "credit_amount": "5000",
                },
            ],
        )
        clearing_write_off = self._billing_write_off_payload(
            proposal_id="019d7b92-5ee4-7a7f-b61c-962c0f4bf619",
            idempotency_key=(
                f"{self.policy.tenant_reference}:collection_write_off:"
                f"019d7b92-5ee4-7a7f-b61c-962c0f4bf619:sha256:{'7' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "7" * 64,
            source_event_references=(
                f"{self.policy.tenant_reference}:collection_write_off:"
                "019d7b92-5ee4-7a7f-b61c-962c0f4bf619",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "write_off_expense",
                    "debit_amount": "12000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0",
                    "credit_amount": "12000",
                },
            ],
        )
        excess_write_off = self._billing_write_off_payload(
            proposal_id="019d7b92-5ee4-7a7f-b61c-962c0f4bf61a",
            idempotency_key=(
                f"{self.policy.tenant_reference}:collection_write_off:"
                f"019d7b92-5ee4-7a7f-b61c-962c0f4bf61a:sha256:{'9' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "9" * 64,
            source_event_references=(
                f"{self.policy.tenant_reference}:collection_write_off:"
                "019d7b92-5ee4-7a7f-b61c-962c0f4bf61a",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "write_off_expense",
                    "debit_amount": "3000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0",
                    "credit_amount": "3000",
                },
            ],
        )
        server = self._start_http_server()
        june_status, _june = self._http_json("POST", "/journal-proposals", june_invoice)
        august_status, _august = self._http_json("POST", "/journal-proposals", august_invoice)
        before_status, before = self._http_receivable_aging()
        oldest_status, _oldest = self._http_json("POST", "/journal-proposals", oldest_write_off)
        fifo_status, fifo = self._http_receivable_aging()
        fifo_balances_status, fifo_balances = self._http_account_balances(
            chart_account_code="110100"
        )
        persisted = self.ledger.load_receivable_aging(
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "2026-08",
        )
        looked_up = lookup_receivable_aging(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
        )

        self.assertEqual(june_status, 200)
        self.assertEqual(august_status, 200)
        self.assertEqual(before_status, 200)
        self.assertEqual(before["days_over_90_amount"], "5000")
        self.assertEqual(before["current_amount"], "12000")
        self.assertEqual(before["total_outstanding_amount"], "17000")
        self.assertEqual(oldest_status, 200)
        self.assertEqual(fifo_status, 200)
        self.assertEqual(fifo_balances_status, 200)
        self.assertEqual(fifo["days_over_90_amount"], "0")
        self.assertEqual(fifo["days_31_60_amount"], "0")
        self.assertEqual(fifo["days_61_90_amount"], "0")
        self.assertEqual(fifo["current_amount"], "12000")
        self.assertEqual(fifo["total_outstanding_amount"], "12000")
        self.assertEqual(
            Decimal(str(fifo["total_outstanding_amount"])),
            Decimal(str(before["total_outstanding_amount"])) - Decimal("5000"),
        )
        self.assertEqual(
            Decimal(str(fifo["total_outstanding_amount"])),
            self._account_balance_net(fifo_balances, "110100"),
        )
        self.assertNotIn("unapplied_credit_amount", fifo)
        self.assertEqual(fifo, persisted)
        self.assertEqual(fifo, looked_up)

        clear_status, _clear = self._http_json("POST", "/journal-proposals", clearing_write_off)
        cleared_status, cleared = self._http_receivable_aging()
        excess_status, _excess = self._http_json("POST", "/journal-proposals", excess_write_off)
        excess_aging_status, excess_aging = self._http_receivable_aging()
        excess_balances_status, excess_balances = self._http_account_balances(
            chart_account_code="110100"
        )
        excess_net = self._account_balance_net(excess_balances, "110100")

        self.assertEqual(clear_status, 200)
        self.assertEqual(cleared_status, 200)
        self.assertEqual(cleared["current_amount"], "0")
        self.assertEqual(cleared["days_31_60_amount"], "0")
        self.assertEqual(cleared["days_61_90_amount"], "0")
        self.assertEqual(cleared["days_over_90_amount"], "0")
        self.assertEqual(cleared["total_outstanding_amount"], "0")
        self.assertNotIn("unapplied_credit_amount", cleared)
        self.assertEqual(excess_status, 200)
        self.assertEqual(excess_aging_status, 200)
        self.assertEqual(excess_balances_status, 200)
        self.assertEqual(excess_aging["current_amount"], "0")
        self.assertEqual(excess_aging["days_31_60_amount"], "0")
        self.assertEqual(excess_aging["days_61_90_amount"], "0")
        self.assertEqual(excess_aging["days_over_90_amount"], "0")
        self.assertEqual(excess_aging["total_outstanding_amount"], "0")
        self.assertEqual(excess_aging["unapplied_credit_amount"], "3000")
        self.assertEqual(excess_net, Decimal("-3000"))
        self.assertEqual(
            Decimal(str(excess_aging["total_outstanding_amount"])),
            excess_net + Decimal(str(excess_aging["unapplied_credit_amount"])),
        )
        server.shutdown()

    def test_unapplied_cash_apply_fifo_clears_and_exposes_unapplied_credit(self) -> None:
        """#61 apply credits consume the oldest AR debit; excess stays unsigned."""
        self._seed_additional_period("2026-06", date(2026, 6, 1), date(2026, 6, 30))
        june_invoice = self._billing_validated_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:invoice_draft:june-apply:"
                f"sha256:{'6' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "6" * 64,
            transaction_date="2026-06-01",
            accounting_date="2026-06-01",
            proposed_at="2026-06-01T00:00:00Z",
            source_event_references=(
                f"{self.policy.tenant_reference}:invoice_draft:june-apply",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "5000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "0",
                    "credit_amount": "5000",
                },
            ],
        )
        august_invoice = self._billing_validated_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:invoice_draft:august-apply:"
                f"sha256:{'8' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "8" * 64,
            transaction_date="2026-08-15",
            accounting_date="2026-08-15",
            proposed_at="2026-08-15T00:00:00Z",
            source_event_references=(
                f"{self.policy.tenant_reference}:invoice_draft:august-apply",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "12000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "0",
                    "credit_amount": "12000",
                },
            ],
        )
        park = self._billing_unapplied_cash_park_payload(
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "cash_receipt",
                    "debit_amount": "20000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "unapplied_cash",
                    "debit_amount": "0",
                    "credit_amount": "20000",
                },
            ],
        )
        oldest_apply = self._billing_unapplied_cash_application_payload(
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "unapplied_cash",
                    "debit_amount": "5000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0",
                    "credit_amount": "5000",
                },
            ],
        )
        clearing_apply = self._billing_unapplied_cash_application_payload(
            proposal_id="019d7b92-8cc5-7a7f-b61c-962c0f4bf626",
            idempotency_key=(
                f"{self.policy.tenant_reference}:unapplied_cash_application:"
                f"019d7b92-8cc5-7a7f-b61c-962c0f4bf626:sha256:{'7' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "7" * 64,
            source_event_references=(
                f"{self.policy.tenant_reference}:unapplied_cash_application:"
                "019d7b92-8cc5-7a7f-b61c-962c0f4bf626",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "unapplied_cash",
                    "debit_amount": "12000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0",
                    "credit_amount": "12000",
                },
            ],
        )
        excess_apply = self._billing_unapplied_cash_application_payload(
            proposal_id="019d7b92-8cc5-7a7f-b61c-962c0f4bf627",
            idempotency_key=(
                f"{self.policy.tenant_reference}:unapplied_cash_application:"
                f"019d7b92-8cc5-7a7f-b61c-962c0f4bf627:sha256:{'9' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "9" * 64,
            source_event_references=(
                f"{self.policy.tenant_reference}:unapplied_cash_application:"
                "019d7b92-8cc5-7a7f-b61c-962c0f4bf627",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "unapplied_cash",
                    "debit_amount": "3000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0",
                    "credit_amount": "3000",
                },
            ],
        )
        server = self._start_http_server()
        june_status, _june = self._http_json("POST", "/journal-proposals", june_invoice)
        august_status, _august = self._http_json("POST", "/journal-proposals", august_invoice)
        park_status, _park = self._http_json("POST", "/journal-proposals", park)
        before_status, before = self._http_receivable_aging()
        oldest_status, oldest_receipt = self._http_json(
            "POST", "/journal-proposals", oldest_apply
        )
        oldest_replay_status, oldest_replay = self._http_json(
            "POST", "/journal-proposals", oldest_apply
        )
        fifo_status, fifo = self._http_receivable_aging()
        fifo_balances_status, fifo_balances = self._http_account_balances(
            chart_account_code="110100"
        )

        self.assertEqual(
            oldest_apply["idempotency_key"],
            (
                f"{self.policy.tenant_reference}:unapplied_cash_application:"
                f"{oldest_apply['proposal_id']}:{oldest_apply['source_payload_hash']}:v1"
            ),
        )
        self.assertEqual(june_status, 200)
        self.assertEqual(august_status, 200)
        self.assertEqual(park_status, 200)
        self.assertEqual(before_status, 200)
        self.assertEqual(before["days_over_90_amount"], "5000")
        self.assertEqual(before["current_amount"], "12000")
        self.assertEqual(before["total_outstanding_amount"], "17000")
        self.assertEqual(oldest_status, 200)
        self.assertEqual(oldest_replay_status, 200)
        self.assertEqual(oldest_receipt, oldest_replay)
        self.assertEqual(fifo_status, 200)
        self.assertEqual(fifo_balances_status, 200)
        self.assertEqual(fifo["days_over_90_amount"], "0")
        self.assertEqual(fifo["days_31_60_amount"], "0")
        self.assertEqual(fifo["days_61_90_amount"], "0")
        self.assertEqual(fifo["current_amount"], "12000")
        self.assertEqual(fifo["total_outstanding_amount"], "12000")
        self.assertEqual(
            Decimal(str(fifo["total_outstanding_amount"])),
            Decimal(str(before["total_outstanding_amount"])) - Decimal("5000"),
        )
        self.assertEqual(
            Decimal(str(fifo["total_outstanding_amount"])),
            self._account_balance_net(fifo_balances, "110100"),
        )
        self.assertNotIn("unapplied_credit_amount", fifo)

        clear_status, _clear = self._http_json("POST", "/journal-proposals", clearing_apply)
        cleared_status, cleared = self._http_receivable_aging()
        excess_status, _excess = self._http_json("POST", "/journal-proposals", excess_apply)
        excess_aging_status, excess_aging = self._http_receivable_aging()
        excess_balances_status, excess_balances = self._http_account_balances(
            chart_account_code="110100"
        )
        excess_net = self._account_balance_net(excess_balances, "110100")

        self.assertEqual(clear_status, 200)
        self.assertEqual(cleared_status, 200)
        self.assertEqual(cleared["current_amount"], "0")
        self.assertEqual(cleared["days_31_60_amount"], "0")
        self.assertEqual(cleared["days_61_90_amount"], "0")
        self.assertEqual(cleared["days_over_90_amount"], "0")
        self.assertEqual(cleared["total_outstanding_amount"], "0")
        self.assertNotIn("unapplied_credit_amount", cleared)
        self.assertEqual(excess_status, 200)
        self.assertEqual(excess_aging_status, 200)
        self.assertEqual(excess_balances_status, 200)
        self.assertEqual(excess_aging["current_amount"], "0")
        self.assertEqual(excess_aging["days_31_60_amount"], "0")
        self.assertEqual(excess_aging["days_61_90_amount"], "0")
        self.assertEqual(excess_aging["days_over_90_amount"], "0")
        self.assertEqual(excess_aging["total_outstanding_amount"], "0")
        self.assertEqual(excess_aging["unapplied_credit_amount"], "3000")
        self.assertEqual(excess_net, Decimal("-3000"))
        self.assertEqual(
            Decimal(str(excess_aging["total_outstanding_amount"])),
            excess_net + Decimal(str(excess_aging["unapplied_credit_amount"])),
        )
        server.shutdown()

    def test_http_reads_unapplied_cash_rollforward(self) -> None:
        """GET /unapplied-cash-rollforwards ties leftover movements to 210200 credit-normal."""
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")
        empty_status, empty = self._http_unapplied_cash_rollforward()
        empty_library = lookup_unapplied_cash_rollforward(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        persist = PostgresPostingLedger(
            DATABASE_URL, self.policy.tenant_reference
        ).load_unapplied_cash_rollforward(
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "2026-08",
        )

        self.assertEqual(empty_status, 200)
        self.assertEqual(empty, empty_library)
        self.assertEqual(empty, persist)
        self.assertEqual(empty["chart_account_code"], "210200")
        self.assertEqual(empty["account_role_code"], "unapplied_cash")
        self.assertEqual(empty["as_of_date"], "2026-08-31")
        self.assertNotIn("party_reference", empty)
        self.assertNotIn("other_movement_amount", empty)
        for key in (
            "parked_amount",
            "applied_amount",
            "refunded_amount",
            "opening_amount",
            "closing_amount",
        ):
            self.assertEqual(empty[key], "0")

        invoice_status, _invoice = self._http_json(
            "POST", "/journal-proposals", self._billing_validated_payload()
        )
        park_status, _park = self._http_json(
            "POST",
            "/journal-proposals",
            self._billing_unapplied_cash_park_payload(
                lines=[
                    {
                        "line_number": 1,
                        "account_role_code": "cash_receipt",
                        "debit_amount": "8000",
                        "credit_amount": "0",
                    },
                    {
                        "line_number": 2,
                        "account_role_code": "unapplied_cash",
                        "debit_amount": "0",
                        "credit_amount": "8000",
                    },
                ],
            ),
        )
        apply_status, _apply = self._http_json(
            "POST",
            "/journal-proposals",
            self._billing_unapplied_cash_application_payload(
                lines=[
                    {
                        "line_number": 1,
                        "account_role_code": "unapplied_cash",
                        "debit_amount": "3000",
                        "credit_amount": "0",
                    },
                    {
                        "line_number": 2,
                        "account_role_code": "accounts_receivable",
                        "debit_amount": "0",
                        "credit_amount": "3000",
                    },
                ],
            ),
        )
        refund_status, _refund = self._http_json(
            "POST",
            "/journal-proposals",
            self._billing_unapplied_cash_refund_payload(
                lines=[
                    {
                        "line_number": 1,
                        "account_role_code": "unapplied_cash",
                        "debit_amount": "2000",
                        "credit_amount": "0",
                    },
                    {
                        "line_number": 2,
                        "account_role_code": "cash_receipt",
                        "debit_amount": "0",
                        "credit_amount": "2000",
                    },
                ],
            ),
        )
        roll_status, rollforward = self._http_unapplied_cash_rollforward()
        library = lookup_unapplied_cash_rollforward(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        balances_status, balances = self._http_account_balances(chart_account_code="210200")
        leftover_net = Decimal(str(balances["account_balances"][0]["credit_amount"])) - Decimal(
            str(balances["account_balances"][0]["debit_amount"])
        )

        self.assertEqual(invoice_status, 200)
        self.assertEqual(park_status, 200)
        self.assertEqual(apply_status, 200)
        self.assertEqual(refund_status, 200)
        self.assertEqual(roll_status, 200)
        self.assertEqual(balances_status, 200)
        self.assertEqual(rollforward, library)
        self.assertEqual(rollforward["parked_amount"], "8000")
        self.assertEqual(rollforward["applied_amount"], "3000")
        self.assertEqual(rollforward["refunded_amount"], "2000")
        self.assertEqual(rollforward["opening_amount"], "0")
        self.assertEqual(rollforward["closing_amount"], "3000")
        self.assertEqual(
            Decimal(str(rollforward["closing_amount"])),
            Decimal(str(rollforward["opening_amount"]))
            + Decimal(str(rollforward["parked_amount"]))
            - Decimal(str(rollforward["applied_amount"]))
            - Decimal(str(rollforward["refunded_amount"])),
        )
        self.assertEqual(Decimal(str(rollforward["closing_amount"])), leftover_net)
        self.assertNotIn("other_movement_amount", rollforward)

        package_status, package = self._http_period_close_package()
        self.assertEqual(package_status, 200)
        self.assertEqual(package["unapplied_cash_rollforward"], rollforward)
        self.assertEqual(package["unapplied_cash_rollforward"], library)
        self.assertEqual(
            Decimal(str(package["unapplied_cash_rollforward"]["closing_amount"])),
            leftover_net,
        )

        soft_status, _soft = self._http_json(
            "POST",
            "/period-closes",
            self._period_close_payload(period_status_code="soft_closed"),
        )
        soft_roll_status, soft_roll = self._http_unapplied_cash_rollforward()
        hard_status, _hard = self._http_json("POST", "/period-closes", self._period_close_payload())
        hard_roll_status, hard_roll = self._http_unapplied_cash_rollforward()
        closed_balances_status, closed_balances = self._http_account_balances(
            chart_account_code="210200"
        )
        closed_net = Decimal(str(closed_balances["account_balances"][0]["credit_amount"])) - Decimal(
            str(closed_balances["account_balances"][0]["debit_amount"])
        )

        self.assertEqual(soft_status, 200)
        self.assertEqual(soft_roll_status, 200)
        self.assertEqual(soft_roll["closing_amount"], "3000")
        self.assertEqual(hard_status, 200)
        self.assertEqual(hard_roll_status, 200)
        self.assertEqual(closed_balances_status, 200)
        self.assertEqual(hard_roll["closing_amount"], "3000")
        self.assertEqual(Decimal(str(hard_roll["closing_amount"])), closed_net)
        self.assertEqual(self._count_closing_journals(), 1)

        alias_query = urllib.parse.urlencode(
            {
                "legal_entity_reference": self.policy.legal_entity_reference,
                "accounting_book_reference": self.policy.accounting_book_reference,
                "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
            }
        )
        alias_status, alias_document = self._http_json(
            "GET", f"/unapplied-cash-rollforwards?{alias_query}", None
        )
        missing_query = self._http_json("GET", "/unapplied-cash-rollforwards", None)
        missing_book_query = self._http_json(
            "GET",
            "/unapplied-cash-rollforwards?"
            + urllib.parse.urlencode(
                {
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
                }
            ),
            None,
        )
        missing_period_query = self._http_json(
            "GET",
            "/unapplied-cash-rollforwards?"
            + urllib.parse.urlencode(
                {
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "book_reference": self.policy.accounting_book_reference,
                }
            ),
            None,
        )
        post_status, _post = self._http_json("POST", "/unapplied-cash-rollforwards", {})
        unknown_period = self._http_unapplied_cash_rollforward(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01"
        )
        unknown_entity = self._http_unapplied_cash_rollforward(
            legal_entity_reference="urn:cwl:legal_entity:missing"
        )
        unknown_book = self._http_unapplied_cash_rollforward(
            book_reference="urn:cwl:accounting_book:missing"
        )
        missing_header = self._http_unapplied_cash_rollforward(tenant_header=None)
        cross_status, _cross = self._http_unapplied_cash_rollforward(
            tenant_header="urn:cwl:tenant_other"
        )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            lookup_unapplied_cash_rollforward(
                DATABASE_URL,
                self.policy.tenant_reference,
                "",
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "book_reference"):
            lookup_unapplied_cash_rollforward(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                "",
                "urn:cwl:accounting:fiscal_period:2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "fiscal_period_reference"):
            lookup_unapplied_cash_rollforward(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "",
            )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_unapplied_cash_rollforward(
                "",
                self.policy.accounting_book_reference,
                "2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "book_reference"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_unapplied_cash_rollforward(
                self.policy.legal_entity_reference,
                "",
                "2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "fiscal_period_reference"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_unapplied_cash_rollforward(
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "",
            )
        empty_chart_book = self._seed_book_without_chart_accounts()
        empty_chart = self._http_unapplied_cash_rollforward(book_reference=empty_chart_book)

        self.assertEqual(alias_status, 200)
        self.assertEqual(alias_document["closing_amount"], "3000")
        self.assertEqual(missing_query[0], 400)
        self.assertEqual(missing_book_query[0], 400)
        self.assertEqual(missing_period_query[0], 400)
        self.assertEqual(post_status, 405)
        self.assertEqual(unknown_period[0], 404)
        self.assertEqual(unknown_entity[0], 404)
        self.assertEqual(unknown_book[0], 404)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(empty_chart[0], 404)
        self.assertEqual(
            self._count_table("accounting_core.general_journal"),
            journals_before + 5,
        )
        server.shutdown()

    def test_http_unapplied_cash_rollforward_opens_from_prior_hard_close(self) -> None:
        """August leftover opening is the prior hard-close 210200 credit-normal snapshot."""
        self._seed_additional_period("2026-07", date(2026, 7, 1), date(2026, 7, 31))
        server = self._start_http_server()
        july_park = self._billing_unapplied_cash_park_payload(
            transaction_date="2026-07-15",
            accounting_date="2026-07-15",
            proposed_at="2026-07-15T00:00:00Z",
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "cash_receipt",
                    "debit_amount": "5000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "unapplied_cash",
                    "debit_amount": "0",
                    "credit_amount": "5000",
                },
            ],
        )
        august_apply = self._billing_unapplied_cash_application_payload(
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "unapplied_cash",
                    "debit_amount": "2000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0",
                    "credit_amount": "2000",
                },
            ],
        )
        august_refund = self._billing_unapplied_cash_refund_payload(
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "unapplied_cash",
                    "debit_amount": "1000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "cash_receipt",
                    "debit_amount": "0",
                    "credit_amount": "1000",
                },
            ],
        )

        july_park_status, _july_park = self._http_json("POST", "/journal-proposals", july_park)
        july_close_status, _july_close = self._http_json(
            "POST",
            "/period-closes",
            self._period_close_payload(
                fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07"
            ),
        )
        apply_status, _apply = self._http_json("POST", "/journal-proposals", august_apply)
        refund_status, _refund = self._http_json("POST", "/journal-proposals", august_refund)
        july_status, july = self._http_unapplied_cash_rollforward(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07"
        )
        august_status, august = self._http_unapplied_cash_rollforward()
        august_balances_status, august_balances = self._http_account_balances(
            chart_account_code="210200"
        )
        august_net = Decimal(
            str(august_balances["account_balances"][0]["credit_amount"])
        ) - Decimal(str(august_balances["account_balances"][0]["debit_amount"]))

        self.assertEqual(july_park_status, 200)
        self.assertEqual(july_close_status, 200)
        self.assertEqual(apply_status, 200)
        self.assertEqual(refund_status, 200)
        self.assertEqual(july_status, 200)
        self.assertEqual(august_status, 200)
        self.assertEqual(august_balances_status, 200)
        self.assertEqual(july["as_of_date"], "2026-07-31")
        self.assertEqual(july["parked_amount"], "5000")
        self.assertEqual(july["applied_amount"], "0")
        self.assertEqual(july["refunded_amount"], "0")
        self.assertEqual(july["opening_amount"], "0")
        self.assertEqual(july["closing_amount"], "5000")
        self.assertEqual(august["opening_amount"], "5000")
        self.assertEqual(august["parked_amount"], "0")
        self.assertEqual(august["applied_amount"], "2000")
        self.assertEqual(august["refunded_amount"], "1000")
        self.assertEqual(august["closing_amount"], "2000")
        self.assertEqual(Decimal(str(august["closing_amount"])), august_net)
        server.shutdown()

    def test_http_unapplied_cash_rollforward_classifies_role_pairs_and_other(self) -> None:
        """Role-pair leftover journals count; unclassified 210200 is other_movement_amount."""
        server = self._start_http_server()
        role_park = self._billing_unapplied_cash_park_payload(
            proposal_id="019d7b92-8cc5-7a7f-b61c-962c0f4bf630",
            idempotency_key=(
                f"{self.policy.tenant_reference}:manual_leftover_park:"
                f"019d7b92-8cc5-7a7f-b61c-962c0f4bf630:sha256:{'a' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "a" * 64,
            source_event_references=(
                f"{self.policy.tenant_reference}:manual_leftover_park:"
                "019d7b92-8cc5-7a7f-b61c-962c0f4bf630",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "cash_receipt",
                    "debit_amount": "1000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "unapplied_cash",
                    "debit_amount": "0",
                    "credit_amount": "1000",
                },
            ],
        )
        role_apply = self._billing_unapplied_cash_application_payload(
            proposal_id="019d7b92-8cc5-7a7f-b61c-962c0f4bf631",
            idempotency_key=(
                f"{self.policy.tenant_reference}:manual_leftover_apply:"
                f"019d7b92-8cc5-7a7f-b61c-962c0f4bf631:sha256:{'b' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "b" * 64,
            source_event_references=(
                f"{self.policy.tenant_reference}:manual_leftover_apply:"
                "019d7b92-8cc5-7a7f-b61c-962c0f4bf631",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "unapplied_cash",
                    "debit_amount": "200",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0",
                    "credit_amount": "200",
                },
            ],
        )
        role_refund = self._billing_unapplied_cash_refund_payload(
            proposal_id="019d7b92-8cc5-7a7f-b61c-962c0f4bf632",
            idempotency_key=(
                f"{self.policy.tenant_reference}:manual_leftover_refund:"
                f"019d7b92-8cc5-7a7f-b61c-962c0f4bf632:sha256:{'c' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "c" * 64,
            source_event_references=(
                f"{self.policy.tenant_reference}:manual_leftover_refund:"
                "019d7b92-8cc5-7a7f-b61c-962c0f4bf632",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "unapplied_cash",
                    "debit_amount": "100",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "cash_receipt",
                    "debit_amount": "0",
                    "credit_amount": "100",
                },
            ],
        )
        adjusting = self._adjusting_journal_payload(
            idempotency_key=f"{self.policy.tenant_reference}:adjusting_journal:leftover:v1",
            journal_description="Reclass leftover cash to revenue",
            journal_lines=[
                {
                    "chart_account_code": "210200",
                    "debit_credit_code": "debit",
                    "amount": "400",
                    "currency_code": "KRW",
                },
                {
                    "chart_account_code": "410100",
                    "debit_credit_code": "credit",
                    "amount": "400",
                    "currency_code": "KRW",
                },
            ],
        )

        park_status, _park = self._http_json("POST", "/journal-proposals", role_park)
        apply_status, _apply = self._http_json("POST", "/journal-proposals", role_apply)
        refund_status, _refund = self._http_json("POST", "/journal-proposals", role_refund)
        adjust_status, _adjust = self._http_json("POST", "/journals", adjusting)
        roll_status, rollforward = self._http_unapplied_cash_rollforward()
        balances_status, balances = self._http_account_balances(chart_account_code="210200")
        leftover_net = Decimal(str(balances["account_balances"][0]["credit_amount"])) - Decimal(
            str(balances["account_balances"][0]["debit_amount"])
        )

        self.assertEqual(park_status, 200)
        self.assertEqual(apply_status, 200)
        self.assertEqual(refund_status, 200)
        self.assertEqual(adjust_status, 200)
        self.assertEqual(roll_status, 200)
        self.assertEqual(balances_status, 200)
        self.assertEqual(rollforward["parked_amount"], "1000")
        self.assertEqual(rollforward["applied_amount"], "200")
        self.assertEqual(rollforward["refunded_amount"], "100")
        self.assertEqual(rollforward["other_movement_amount"], "-400")
        self.assertEqual(rollforward["opening_amount"], "0")
        self.assertEqual(rollforward["closing_amount"], "300")
        self.assertEqual(
            Decimal(str(rollforward["closing_amount"])),
            Decimal(str(rollforward["opening_amount"]))
            + Decimal(str(rollforward["parked_amount"]))
            - Decimal(str(rollforward["applied_amount"]))
            - Decimal(str(rollforward["refunded_amount"]))
            + Decimal(str(rollforward["other_movement_amount"])),
        )
        self.assertEqual(Decimal(str(rollforward["closing_amount"])), leftover_net)
        server.shutdown()

    def test_http_reads_vat_period_register(self) -> None:
        """GET /vat-period-registers ties issued and voided tax to posted 210100."""
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")
        empty_status, empty = self._http_vat_period_register()
        empty_library = lookup_vat_period_register(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        persist = PostgresPostingLedger(
            DATABASE_URL, self.policy.tenant_reference
        ).load_vat_period_register(
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "2026-08",
        )

        self.assertEqual(empty_status, 200)
        self.assertEqual(empty, empty_library)
        self.assertEqual(empty, persist)
        self.assertEqual(
            set(empty),
            {
                "tenant_reference",
                "legal_entity_reference",
                "accounting_book_reference",
                "book_reference",
                "fiscal_period_reference",
                "as_of_date",
                "chart_account_code",
                "account_role_code",
                "issued_amount",
                "voided_amount",
                "closing_amount",
            },
        )
        self.assertEqual(empty["tenant_reference"], self.policy.tenant_reference)
        self.assertEqual(empty["legal_entity_reference"], self.policy.legal_entity_reference)
        self.assertEqual(empty["accounting_book_reference"], self.policy.accounting_book_reference)
        self.assertEqual(empty["book_reference"], self.policy.accounting_book_reference)
        self.assertEqual(
            empty["fiscal_period_reference"],
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        self.assertEqual(empty["as_of_date"], "2026-08-31")
        self.assertEqual(empty["chart_account_code"], "210100")
        self.assertEqual(empty["account_role_code"], "tax_payable")
        self.assertEqual(empty["issued_amount"], "0")
        self.assertEqual(empty["voided_amount"], "0")
        self.assertEqual(empty["closing_amount"], "0")
        self.assertNotIn("party_reference", empty)
        self.assertNotIn("next_cursor", empty)
        self.assertNotIn("other_movement_amount", empty)
        self.assertNotIn("210200", json.dumps(empty))

        untaxed_status, _untaxed = self._http_json(
            "POST", "/journal-proposals", self._billing_validated_payload()
        )
        untaxed_register_status, untaxed_register = self._http_vat_period_register()
        self.assertEqual(untaxed_status, 200)
        self.assertEqual(untaxed_register_status, 200)
        self.assertEqual(untaxed_register["issued_amount"], "0")
        self.assertEqual(untaxed_register["voided_amount"], "0")
        self.assertEqual(untaxed_register["closing_amount"], "0")

        taxed_status, _taxed = self._http_json(
            "POST", "/journal-proposals", self._billing_taxed_payload()
        )
        issued_status, issued = self._http_vat_period_register()
        issued_library = lookup_vat_period_register(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        issued_balances_status, issued_balances = self._http_account_balances(
            chart_account_code="210100"
        )
        issued_aging_status, issued_aging = self._http_payable_aging()
        issued_net = Decimal(
            str(issued_balances["account_balances"][0]["credit_amount"])
        ) - Decimal(str(issued_balances["account_balances"][0]["debit_amount"]))

        self.assertEqual(taxed_status, 200)
        self.assertEqual(issued_status, 200)
        self.assertEqual(issued_balances_status, 200)
        self.assertEqual(issued_aging_status, 200)
        self.assertEqual(issued, issued_library)
        self.assertEqual(issued["issued_amount"], "2500")
        self.assertEqual(issued["voided_amount"], "0")
        self.assertEqual(issued["closing_amount"], "2500")
        self.assertEqual(
            Decimal(str(issued["closing_amount"])),
            Decimal(str(issued["issued_amount"])) - Decimal(str(issued["voided_amount"])),
        )
        self.assertEqual(Decimal(str(issued["closing_amount"])), issued_net)
        self.assertEqual(issued_aging["chart_account_code"], "210100")
        self.assertEqual(issued_aging["total_outstanding_amount"], "2500")
        self.assertEqual(
            Decimal(str(issued["closing_amount"])),
            Decimal(str(issued_aging["total_outstanding_amount"])),
        )

        void_status, _void = self._http_json(
            "POST", "/journal-proposals", self._billing_issued_invoice_void_payload()
        )
        voided_status, voided = self._http_vat_period_register()
        voided_library = lookup_vat_period_register(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        voided_balances_status, voided_balances = self._http_account_balances(
            chart_account_code="210100"
        )
        voided_aging_status, voided_aging = self._http_payable_aging()
        leftover_status, leftover = self._http_unapplied_cash_rollforward()
        voided_net = Decimal(
            str(voided_balances["account_balances"][0]["credit_amount"])
        ) - Decimal(str(voided_balances["account_balances"][0]["debit_amount"]))

        self.assertEqual(void_status, 200)
        self.assertEqual(voided_status, 200)
        self.assertEqual(voided_balances_status, 200)
        self.assertEqual(voided_aging_status, 200)
        self.assertEqual(leftover_status, 200)
        self.assertEqual(voided, voided_library)
        self.assertEqual(voided["issued_amount"], "2500")
        self.assertEqual(voided["voided_amount"], "2500")
        self.assertEqual(voided["closing_amount"], "0")
        self.assertEqual(
            Decimal(str(voided["closing_amount"])),
            Decimal(str(voided["issued_amount"])) - Decimal(str(voided["voided_amount"])),
        )
        self.assertEqual(Decimal(str(voided["closing_amount"])), voided_net)
        self.assertEqual(voided_aging["total_outstanding_amount"], "0")
        self.assertEqual(leftover["chart_account_code"], "210200")
        self.assertEqual(leftover["closing_amount"], "0")
        self.assertNotIn("210200", json.dumps(voided))

        soft_status, _soft = self._http_json(
            "POST",
            "/period-closes",
            self._period_close_payload(period_status_code="soft_closed"),
        )
        soft_register_status, soft_register = self._http_vat_period_register()
        hard_status, _hard = self._http_json("POST", "/period-closes", self._period_close_payload())
        hard_register_status, hard_register = self._http_vat_period_register()
        closed_balances_status, closed_balances = self._http_account_balances(
            chart_account_code="210100"
        )
        closed_aging_status, closed_aging = self._http_payable_aging()
        closed_net = Decimal(
            str(closed_balances["account_balances"][0]["credit_amount"])
        ) - Decimal(str(closed_balances["account_balances"][0]["debit_amount"]))

        self.assertEqual(soft_status, 200)
        self.assertEqual(soft_register_status, 200)
        self.assertEqual(soft_register["issued_amount"], "2500")
        self.assertEqual(soft_register["voided_amount"], "2500")
        self.assertEqual(soft_register["closing_amount"], "0")
        self.assertEqual(hard_status, 200)
        self.assertEqual(hard_register_status, 200)
        self.assertEqual(closed_balances_status, 200)
        self.assertEqual(closed_aging_status, 200)
        self.assertEqual(hard_register["closing_amount"], "0")
        self.assertEqual(Decimal(str(hard_register["closing_amount"])), closed_net)
        self.assertEqual(closed_aging["total_outstanding_amount"], "0")

        alias_query = urllib.parse.urlencode(
            {
                "legal_entity_reference": self.policy.legal_entity_reference,
                "accounting_book_reference": self.policy.accounting_book_reference,
                "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
            }
        )
        alias_status, alias_document = self._http_json(
            "GET", f"/vat-period-registers?{alias_query}", None
        )
        missing_query = self._http_json("GET", "/vat-period-registers", None)
        missing_book_query = self._http_json(
            "GET",
            "/vat-period-registers?"
            + urllib.parse.urlencode(
                {
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
                }
            ),
            None,
        )
        missing_period_query = self._http_json(
            "GET",
            "/vat-period-registers?"
            + urllib.parse.urlencode(
                {
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "book_reference": self.policy.accounting_book_reference,
                }
            ),
            None,
        )
        post_status, _post = self._http_json("POST", "/vat-period-registers", {})
        unknown_period = self._http_vat_period_register(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01"
        )
        unknown_entity = self._http_vat_period_register(
            legal_entity_reference="urn:cwl:legal_entity:missing"
        )
        unknown_book = self._http_vat_period_register(
            book_reference="urn:cwl:accounting_book:missing"
        )
        missing_header = self._http_vat_period_register(tenant_header=None)
        cross_status, _cross = self._http_vat_period_register(
            tenant_header="urn:cwl:tenant_other"
        )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            lookup_vat_period_register(
                DATABASE_URL,
                self.policy.tenant_reference,
                "",
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "book_reference"):
            lookup_vat_period_register(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                "",
                "urn:cwl:accounting:fiscal_period:2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "fiscal_period_reference"):
            lookup_vat_period_register(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "",
            )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_vat_period_register(
                "",
                self.policy.accounting_book_reference,
                "2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "book_reference"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_vat_period_register(
                self.policy.legal_entity_reference,
                "",
                "2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "fiscal_period_reference"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_vat_period_register(
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "",
            )
        empty_chart_book = self._seed_book_without_chart_accounts()
        empty_chart = self._http_vat_period_register(book_reference=empty_chart_book)

        self.assertEqual(alias_status, 200)
        self.assertEqual(alias_document["closing_amount"], "0")
        self.assertEqual(missing_query[0], 400)
        self.assertEqual(missing_book_query[0], 400)
        self.assertEqual(missing_period_query[0], 400)
        self.assertEqual(post_status, 405)
        self.assertEqual(unknown_period[0], 404)
        self.assertEqual(unknown_entity[0], 404)
        self.assertEqual(unknown_book[0], 404)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(empty_chart[0], 404)
        self.assertEqual(
            self._count_table("accounting_core.general_journal"),
            journals_before + 4,
        )
        server.shutdown()

    def test_http_vat_period_register_classifies_role_pairs_and_other(self) -> None:
        """Role-pair tax journals count; unclassified 210100 is other_movement_amount."""
        self._seed_additional_period("2026-07", date(2026, 7, 1), date(2026, 7, 31))
        server = self._start_http_server()
        july_taxed = self._billing_taxed_payload(
            proposal_id="019d7b92-3cc2-7a7f-b61c-962c0f4bf701",
            idempotency_key=(
                f"{self.policy.tenant_reference}:manual_taxed_invoice:"
                f"019d7b92-3cc2-7a7f-b61c-962c0f4bf701:sha256:{'d' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "d" * 64,
            transaction_date="2026-07-15",
            accounting_date="2026-07-15",
            proposed_at="2026-07-15T00:00:00Z",
            source_event_references=(
                f"{self.policy.tenant_reference}:manual_taxed_invoice:"
                "019d7b92-3cc2-7a7f-b61c-962c0f4bf701",
            ),
        )
        july_status, _july = self._http_json("POST", "/journal-proposals", july_taxed)
        july_close_status, _july_close = self._http_json(
            "POST",
            "/period-closes",
            self._period_close_payload(
                fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07"
            ),
        )
        july_register_status, july_register = self._http_vat_period_register(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07"
        )
        carried_status, carried = self._http_vat_period_register()

        self.assertEqual(july_status, 200)
        self.assertEqual(july_close_status, 200)
        self.assertEqual(july_register_status, 200)
        self.assertEqual(carried_status, 200)
        self.assertEqual(july_register["as_of_date"], "2026-07-31")
        self.assertEqual(july_register["issued_amount"], "2500")
        self.assertEqual(july_register["voided_amount"], "0")
        self.assertEqual(july_register["closing_amount"], "2500")
        self.assertEqual(carried["as_of_date"], "2026-08-31")
        self.assertEqual(carried["issued_amount"], "2500")
        self.assertEqual(carried["voided_amount"], "0")
        self.assertEqual(carried["closing_amount"], "2500")

        role_void = self._billing_issued_invoice_void_payload(
            proposal_id="019d7b92-9ee8-7a7f-b61c-962c0f4bf702",
            idempotency_key=(
                f"{self.policy.tenant_reference}:manual_tax_void:"
                f"019d7b92-9ee8-7a7f-b61c-962c0f4bf702:sha256:{'e' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "e" * 64,
            source_event_references=(
                f"{self.policy.tenant_reference}:manual_tax_void:"
                "019d7b92-9ee8-7a7f-b61c-962c0f4bf702",
            ),
        )
        adjusting = self._adjusting_journal_payload(
            idempotency_key=f"{self.policy.tenant_reference}:adjusting_journal:vat:v1",
            journal_description="Reclass revenue to tax payable",
            journal_lines=[
                {
                    "chart_account_code": "410100",
                    "debit_credit_code": "debit",
                    "amount": "400",
                    "currency_code": "KRW",
                },
                {
                    "chart_account_code": "210100",
                    "debit_credit_code": "credit",
                    "amount": "400",
                    "currency_code": "KRW",
                },
            ],
        )
        void_status, _void = self._http_json("POST", "/journal-proposals", role_void)
        adjust_status, _adjust = self._http_json("POST", "/journals", adjusting)
        register_status, register = self._http_vat_period_register()
        balances_status, balances = self._http_account_balances(chart_account_code="210100")
        aging_status, aging = self._http_payable_aging()
        tax_net = Decimal(str(balances["account_balances"][0]["credit_amount"])) - Decimal(
            str(balances["account_balances"][0]["debit_amount"])
        )

        self.assertEqual(void_status, 200)
        self.assertEqual(adjust_status, 200)
        self.assertEqual(register_status, 200)
        self.assertEqual(balances_status, 200)
        self.assertEqual(aging_status, 200)
        self.assertEqual(register["issued_amount"], "2500")
        self.assertEqual(register["voided_amount"], "2500")
        self.assertEqual(register["other_movement_amount"], "400")
        self.assertEqual(register["closing_amount"], "400")
        self.assertEqual(
            Decimal(str(register["closing_amount"])),
            Decimal(str(register["issued_amount"]))
            - Decimal(str(register["voided_amount"]))
            + Decimal(str(register["other_movement_amount"])),
        )
        self.assertEqual(Decimal(str(register["closing_amount"])), tax_net)
        self.assertEqual(
            Decimal(str(register["closing_amount"])),
            Decimal(str(aging["total_outstanding_amount"])),
        )
        server.shutdown()

    def test_http_rejects_home_tax_submission_without_register(self) -> None:
        """POST /home-tax-submissions fail-closes when the VAT register cannot load."""
        server = self._start_http_server()
        submissions_before = self._count_table(
            "accounting_integration.home_tax_submission"
        )
        empty_list_status, empty_list = self._http_home_tax_submissions()
        missing_fields_status, missing_fields = self._http_home_tax_submission(
            legal_entity_reference="",
            book_reference="",
            fiscal_period_reference="",
        )
        unknown_period = self._http_home_tax_submission(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01"
        )
        unknown_entity = self._http_home_tax_submission(
            legal_entity_reference="urn:cwl:legal_entity:missing"
        )
        unknown_book = self._http_home_tax_submission(
            book_reference="urn:cwl:accounting_book:missing"
        )
        missing_header = self._http_home_tax_submission(tenant_header=None)
        cross_status, _cross = self._http_home_tax_submission(
            tenant_header="urn:cwl:tenant_other"
        )
        body_mismatch_status, _body_mismatch = self._http_json(
            "POST",
            "/home-tax-submissions",
            {
                "tenant_reference": "urn:cwl:tenant_other",
                "legal_entity_reference": self.policy.legal_entity_reference,
                "book_reference": self.policy.accounting_book_reference,
                "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
            },
        )
        vat_post_status, _vat_post = self._http_json("POST", "/vat-period-registers", {})
        vat_get_status, vat_get = self._http_vat_period_register()
        missing_query = self._http_json("GET", "/home-tax-submissions", None)
        unknown_list = self._http_home_tax_submissions(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01"
        )
        with self.assertRaisesRegex(AccountingValidationError, "are required"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_home_tax_submissions("", self.policy.accounting_book_reference, "2026-08")

        self.assertEqual(empty_list_status, 200)
        self.assertEqual(empty_list["home_tax_submissions"], [])
        self.assertEqual(empty_list["tenant_reference"], self.policy.tenant_reference)
        self.assertEqual(
            empty_list["legal_entity_reference"], self.policy.legal_entity_reference
        )
        self.assertEqual(empty_list["book_reference"], self.policy.accounting_book_reference)
        self.assertEqual(
            empty_list["fiscal_period_reference"],
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        self.assertEqual(missing_fields_status, 422)
        self.assertEqual(missing_fields["submission_status_code"], "rejected")
        self.assertEqual(missing_fields["rejection_reason_code"], "register_unavailable")
        self.assertNotEqual(missing_fields["submission_status_code"], "transmitted")
        self.assertEqual(unknown_period[0], 404)
        self.assertEqual(unknown_entity[0], 404)
        self.assertEqual(unknown_book[0], 404)
        self.assertIn("error_message", unknown_period[1])
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(body_mismatch_status, 403)
        self.assertEqual(vat_post_status, 405)
        self.assertEqual(vat_get_status, 200)
        self.assertEqual(vat_get["chart_account_code"], "210100")
        self.assertEqual(missing_query[0], 400)
        self.assertEqual(unknown_list[0], 404)
        self.assertEqual(
            self._count_table("accounting_integration.home_tax_submission"),
            submissions_before,
        )
        server.shutdown()

    def test_http_rejects_home_tax_submission_without_credential(self) -> None:
        """A loadable VAT register without ACCOUNTING_HOMETAX_CREDENTIAL is 422 rejected."""
        server = self._start_http_server()
        previous_credential = os.environ.pop("ACCOUNTING_HOMETAX_CREDENTIAL", None)
        try:
            empty_register_status, empty_register = self._http_vat_period_register()
            empty_status, empty = self._http_home_tax_submission()
            taxed_status, _taxed = self._http_json(
                "POST", "/journal-proposals", self._billing_taxed_payload()
            )
            live_register_status, live_register = self._http_vat_period_register()
            live_status, live = self._http_home_tax_submission()
            listed_status, listed = self._http_home_tax_submissions()
            alias_query = urllib.parse.urlencode(
                {
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "accounting_book_reference": self.policy.accounting_book_reference,
                    "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
                }
            )
            alias_status, alias_listed = self._http_json(
                "GET", f"/home-tax-submissions?{alias_query}", None
            )
            library = lookup_home_tax_submissions(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
            )
        finally:
            if previous_credential is not None:
                os.environ["ACCOUNTING_HOMETAX_CREDENTIAL"] = previous_credential

        self.assertEqual(empty_register_status, 200)
        self.assertEqual(empty_status, 422)
        self.assertEqual(empty["submission_status_code"], "rejected")
        self.assertEqual(empty["rejection_reason_code"], "hometax_credential_missing")
        self.assertNotEqual(empty["submission_status_code"], "transmitted")
        self.assertEqual(empty["vat_period_register"], empty_register)
        self.assertEqual(empty["tenant_reference"], self.policy.tenant_reference)
        self.assertEqual(empty["legal_entity_reference"], self.policy.legal_entity_reference)
        self.assertEqual(empty["book_reference"], self.policy.accounting_book_reference)
        self.assertEqual(
            empty["fiscal_period_reference"],
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        self.assertNotIn("party_reference", empty)
        self.assertNotIn("nts_payload", empty)
        self.assertEqual(taxed_status, 200)
        self.assertEqual(live_register_status, 200)
        self.assertEqual(live_status, 422)
        self.assertEqual(live["rejection_reason_code"], "hometax_credential_missing")
        self.assertEqual(live["vat_period_register"]["closing_amount"], "2500")
        self.assertEqual(live["vat_period_register"], live_register)
        self.assertNotEqual(live["submission_status_code"], "transmitted")
        self.assertEqual(listed_status, 200)
        self.assertEqual(len(listed["home_tax_submissions"]), 2)
        self.assertEqual(alias_status, 200)
        self.assertEqual(alias_listed, listed)
        self.assertEqual(listed, library)
        self.assertEqual(
            listed["home_tax_submissions"][0]["rejection_reason_code"],
            "hometax_credential_missing",
        )
        self.assertEqual(
            listed["home_tax_submissions"][0]["submission_status_code"],
            "rejected",
        )
        self.assertEqual(
            listed["home_tax_submissions"][0]["vat_period_register"]["closing_amount"],
            "0",
        )
        self.assertEqual(
            listed["home_tax_submissions"][1]["vat_period_register"]["closing_amount"],
            "2500",
        )
        self.assertEqual(
            listed["home_tax_submissions"][1]["vat_period_register"]["as_of_date"],
            live_register["as_of_date"],
        )
        self.assertEqual(
            self._count_table("accounting_integration.home_tax_submission"),
            2,
        )
        server.shutdown()

    def test_http_rejects_home_tax_submission_when_transport_unavailable(self) -> None:
        """A present purpose-limited credential still does not transmit in this slice."""
        server = self._start_http_server()
        with mock.patch.dict(os.environ, {"ACCOUNTING_HOMETAX_CREDENTIAL": "present"}):
            status, document = self._http_home_tax_submission()
            listed_status, listed = self._http_home_tax_submissions()
        vat_status, vat_register = self._http_vat_period_register()

        self.assertEqual(status, 422)
        self.assertEqual(document["submission_status_code"], "rejected")
        self.assertEqual(document["rejection_reason_code"], "hometax_transport_unavailable")
        self.assertNotEqual(document["submission_status_code"], "transmitted")
        self.assertEqual(document["vat_period_register"], vat_register)
        self.assertNotIn("ACCOUNTING_HOMETAX_CREDENTIAL", json.dumps(document))
        self.assertEqual(listed_status, 200)
        self.assertEqual(len(listed["home_tax_submissions"]), 1)
        self.assertEqual(
            listed["home_tax_submissions"][0]["rejection_reason_code"],
            "hometax_transport_unavailable",
        )
        self.assertEqual(vat_status, 200)
        server.shutdown()

    def test_http_rejects_home_tax_submission_when_register_document_is_incomplete(self) -> None:
        """A loaded object missing always-present register keys is 422 register_unavailable."""
        server = self._start_http_server()
        with mock.patch.object(
            PostgresPostingLedger,
            "load_vat_period_register",
            return_value={"tenant_reference": self.policy.tenant_reference},
        ):
            status, document = self._http_home_tax_submission()
        listed_status, listed = self._http_home_tax_submissions()

        self.assertEqual(status, 422)
        self.assertEqual(document["submission_status_code"], "rejected")
        self.assertEqual(document["rejection_reason_code"], "register_unavailable")
        self.assertNotEqual(document["submission_status_code"], "transmitted")
        self.assertEqual(listed_status, 200)
        self.assertEqual(len(listed["home_tax_submissions"]), 1)
        self.assertEqual(
            listed["home_tax_submissions"][0]["rejection_reason_code"],
            "register_unavailable",
        )
        server.shutdown()

    def test_http_reads_account_role_mappings_from_catalog(self) -> None:
        """GET returns seeded role-to-chart mappings and rejects cross-tenant or unknown books."""
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")

        status, document = self._http_account_role_mappings()
        alias_query = urllib.parse.urlencode(
            {
                "legal_entity_reference": self.policy.legal_entity_reference,
                "accounting_book_reference": self.policy.accounting_book_reference,
            }
        )
        alias_status, alias_document = self._http_json(
            "GET", f"/account-role-mappings?{alias_query}", None
        )
        aliased = lookup_account_role_mappings(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
        )
        by_code = {
            str(item["account_role_code"]): item for item in document["mappings"]
        }

        self.assertEqual(status, 200)
        self.assertEqual(alias_status, 200)
        self.assertEqual(document, aliased)
        self.assertEqual(alias_document, document)
        self.assertEqual(document["tenant_reference"], self.policy.tenant_reference)
        self.assertEqual(document["legal_entity_reference"], self.policy.legal_entity_reference)
        self.assertEqual(document["accounting_book_reference"], self.policy.accounting_book_reference)
        self.assertEqual(document["book_reference"], self.policy.accounting_book_reference)
        self.assertEqual(
            set(by_code),
            {
                "accounts_receivable",
                "usage_revenue",
                "cash_receipt",
                "tax_payable",
                "retained_earnings",
                "write_off_expense",
                "unapplied_cash",
            },
        )
        self.assertEqual(by_code["accounts_receivable"]["chart_account_code"], "110100")
        self.assertEqual(by_code["usage_revenue"]["chart_account_code"], "410100")
        self.assertEqual(by_code["cash_receipt"]["chart_account_code"], "110200")
        self.assertEqual(by_code["tax_payable"]["chart_account_code"], "210100")
        self.assertEqual(by_code["retained_earnings"]["chart_account_code"], "310100")
        self.assertEqual(by_code["write_off_expense"]["chart_account_code"], "510100")
        self.assertEqual(by_code["unapplied_cash"]["chart_account_code"], "210200")
        self.assertEqual(by_code["cash_receipt"]["accounting_policy_version"], "ifrs-v1")
        self.assertEqual(by_code["cash_receipt"]["posting_rule_version"], "billing-issued-v1")

        post_status, _post_body = self._http_json("POST", "/account-role-mappings", {})
        missing_header = self._http_account_role_mappings(tenant_header=None)
        cross_status, _cross = self._http_account_role_mappings(
            tenant_header="urn:cwl:tenant_other"
        )
        missing_query = self._http_json("GET", "/account-role-mappings", None)
        missing_book = self._http_json(
            "GET",
            "/account-role-mappings?"
            + urllib.parse.urlencode(
                {"legal_entity_reference": self.policy.legal_entity_reference}
            ),
            None,
        )
        unknown_entity = self._http_account_role_mappings(
            legal_entity_reference="urn:cwl:legal_entity:missing"
        )
        unknown_book = self._http_account_role_mappings(
            book_reference="urn:cwl:accounting_book:missing"
        )
        with self.assertRaisesRegex(AccountingValidationError, "book_reference"):
            lookup_account_role_mappings(
                DATABASE_URL, self.policy.tenant_reference, "", ""
            )
        with self.assertRaisesRegex(AccountingValidationError, "book_reference"):
            lookup_account_role_mappings(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                "",
            )
        with self.assertRaisesRegex(AccountingValidationError, "book_reference"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_account_role_mappings("", "")
        with self.assertRaisesRegex(AccountingValidationError, "book_reference"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_account_role_mappings(self.policy.legal_entity_reference, "")
        self._delete_role_mappings()
        with self.assertRaisesRegex(AccountingValidationError, "account_role_mapping"):
            lookup_account_role_mappings(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
            )

        self.assertEqual(post_status, 405)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(missing_query[0], 400)
        self.assertEqual(missing_book[0], 400)
        self.assertEqual(unknown_entity[0], 404)
        self.assertEqual(unknown_book[0], 404)
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before)
        server.shutdown()

    def test_http_reads_chart_accounts_from_catalog(self) -> None:
        """GET returns seeded chart_account rows including durable account_class_code."""
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")
        empty_book = self._seed_book_without_chart_accounts()

        status, document = self._http_chart_accounts()
        alias_query = urllib.parse.urlencode(
            {
                "legal_entity_reference": self.policy.legal_entity_reference,
                "accounting_book_reference": self.policy.accounting_book_reference,
            }
        )
        alias_status, alias_document = self._http_json(
            "GET", f"/chart-accounts?{alias_query}", None
        )
        library = lookup_chart_accounts(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
        )
        empty_status, empty_page = self._http_chart_accounts(book_reference=empty_book)
        by_code = {
            str(item["chart_account_code"]): item for item in document["chart_accounts"]
        }

        self.assertEqual(status, 200)
        self.assertEqual(alias_status, 200)
        self.assertEqual(document, library)
        self.assertEqual(alias_document, document)
        self.assertEqual(document["tenant_reference"], self.policy.tenant_reference)
        self.assertEqual(document["legal_entity_reference"], self.policy.legal_entity_reference)
        self.assertEqual(document["accounting_book_reference"], self.policy.accounting_book_reference)
        self.assertEqual(document["book_reference"], self.policy.accounting_book_reference)
        self.assertEqual(
            set(by_code),
            {"110100", "410100", "110200", "210100", "210200", "310100", "510100"},
        )
        self.assertEqual(by_code["110100"]["account_name"], "Accounts receivable")
        self.assertEqual(by_code["110100"]["normal_balance_code"], "debit")
        self.assertEqual(by_code["110100"]["account_class_code"], "asset")
        self.assertEqual(by_code["410100"]["account_name"], "Usage revenue")
        self.assertEqual(by_code["410100"]["normal_balance_code"], "credit")
        self.assertEqual(by_code["410100"]["account_class_code"], "revenue")
        self.assertEqual(by_code["110200"]["account_name"], "Cash receipts")
        self.assertEqual(by_code["110200"]["normal_balance_code"], "debit")
        self.assertEqual(by_code["110200"]["account_class_code"], "asset")
        self.assertEqual(by_code["210100"]["account_name"], "Tax payable")
        self.assertEqual(by_code["210100"]["normal_balance_code"], "credit")
        self.assertEqual(by_code["210100"]["account_class_code"], "liability")
        self.assertEqual(by_code["310100"]["account_name"], "Retained earnings")
        self.assertEqual(by_code["310100"]["normal_balance_code"], "credit")
        self.assertEqual(by_code["310100"]["account_class_code"], "equity")
        self.assertEqual(by_code["510100"]["account_name"], "Write-off expense")
        self.assertEqual(by_code["510100"]["normal_balance_code"], "debit")
        self.assertEqual(by_code["510100"]["account_class_code"], "expense")
        self.assertEqual(by_code["210200"]["account_name"], "unapplied_cash")
        self.assertEqual(by_code["210200"]["normal_balance_code"], "credit")
        self.assertEqual(by_code["210200"]["account_class_code"], "liability")
        self.assertEqual(empty_status, 200)
        self.assertEqual(empty_page["chart_accounts"], [])
        self.assertEqual(empty_page["book_reference"], empty_book)

        post_status, _post_body = self._http_json("POST", "/chart-accounts", {})
        missing_header = self._http_chart_accounts(tenant_header=None)
        cross_status, _cross = self._http_chart_accounts(tenant_header="urn:cwl:tenant_other")
        missing_query = self._http_json("GET", "/chart-accounts", None)
        missing_book = self._http_json(
            "GET",
            "/chart-accounts?"
            + urllib.parse.urlencode(
                {"legal_entity_reference": self.policy.legal_entity_reference}
            ),
            None,
        )
        unknown_entity = self._http_chart_accounts(
            legal_entity_reference="urn:cwl:legal_entity:missing"
        )
        unknown_book = self._http_chart_accounts(
            book_reference="urn:cwl:accounting_book:missing"
        )
        with self.assertRaisesRegex(AccountingValidationError, "book_reference"):
            lookup_chart_accounts(DATABASE_URL, self.policy.tenant_reference, "", "")
        with self.assertRaisesRegex(AccountingValidationError, "book_reference"):
            lookup_chart_accounts(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                "",
            )
        with self.assertRaisesRegex(AccountingValidationError, "book_reference"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_chart_accounts("", "")
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity"):
            lookup_chart_accounts(
                DATABASE_URL,
                self.policy.tenant_reference,
                "urn:cwl:legal_entity:missing",
                self.policy.accounting_book_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "accounting_book"):
            lookup_chart_accounts(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                "urn:cwl:accounting_book:missing",
            )

        self.assertEqual(post_status, 405)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(missing_query[0], 400)
        self.assertEqual(missing_book[0], 400)
        self.assertEqual(unknown_entity[0], 404)
        self.assertEqual(unknown_book[0], 404)
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before)
        server.shutdown()

    def test_http_lists_accounting_books_for_legal_entity(self) -> None:
        """GET returns existing accounting_book rows so a controller can discover book_reference."""
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")
        management_book = self._seed_book_without_chart_accounts()
        empty_entity = self._seed_entity_without_books()

        status, document = self._http_accounting_books()
        library = lookup_accounting_books(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
        )
        empty_status, empty_page = self._http_accounting_books(
            legal_entity_reference=empty_entity
        )
        by_role = {
            str(item["intended_book_role_code"]): item
            for item in document["accounting_books"]
        }

        self.assertEqual(status, 200)
        self.assertEqual(document, library)
        self.assertEqual(document["tenant_reference"], self.policy.tenant_reference)
        self.assertEqual(document["legal_entity_reference"], self.policy.legal_entity_reference)
        self.assertNotIn("next_cursor", document)
        self.assertEqual(set(by_role), {"primary_statutory", "management"})
        self.assertEqual(
            by_role["primary_statutory"]["accounting_book_reference"],
            self.policy.accounting_book_reference,
        )
        self.assertEqual(
            by_role["primary_statutory"]["book_reference"],
            self.policy.accounting_book_reference,
        )
        self.assertEqual(
            by_role["primary_statutory"]["book_name"],
            self.policy.accounting_book_reference,
        )
        self.assertEqual(by_role["management"]["book_reference"], management_book)
        self.assertEqual(by_role["management"]["accounting_book_reference"], management_book)
        self.assertEqual(empty_status, 200)
        self.assertEqual(empty_page["accounting_books"], [])
        self.assertEqual(empty_page["legal_entity_reference"], empty_entity)

        post_status, _post_body = self._http_json("POST", "/accounting-books", {})
        missing_header = self._http_accounting_books(tenant_header=None)
        cross_status, _cross = self._http_accounting_books(
            tenant_header="urn:cwl:tenant_other"
        )
        missing_query = self._http_json("GET", "/accounting-books", None)
        unknown_entity = self._http_accounting_books(
            legal_entity_reference="urn:cwl:legal_entity:missing"
        )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            lookup_accounting_books(DATABASE_URL, self.policy.tenant_reference, "")
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_accounting_books("")
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity"):
            lookup_accounting_books(
                DATABASE_URL,
                self.policy.tenant_reference,
                "urn:cwl:legal_entity:missing",
            )

        self.assertEqual(post_status, 405)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(missing_query[0], 400)
        self.assertEqual(unknown_entity[0], 404)
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before)
        server.shutdown()

    def test_http_lists_legal_entities_for_tenant(self) -> None:
        """GET /legal-entities returns the tenant catalog so a controller can discover legal_entity_reference."""
        extra_entity = self._seed_entity_without_books()
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")

        status, document = self._http_legal_entities()
        library = lookup_legal_entities(DATABASE_URL, self.policy.tenant_reference)
        persist = PostgresPostingLedger(
            DATABASE_URL, self.policy.tenant_reference
        ).load_legal_entities()
        by_reference = {
            str(item["legal_entity_reference"]): item
            for item in document["legal_entities"]
        }

        self.assertEqual(status, 200)
        self.assertEqual(document, library)
        self.assertEqual(document, persist)
        self.assertEqual(document["tenant_reference"], self.policy.tenant_reference)
        self.assertNotIn("next_cursor", document)
        self.assertEqual(
            set(by_reference),
            {self.policy.legal_entity_reference, extra_entity},
        )
        self.assertEqual(
            by_reference[self.policy.legal_entity_reference]["entity_name"],
            "Statutory entity",
        )
        self.assertEqual(by_reference[extra_entity]["entity_name"], "Entity without books")

        post_status, _post_body = self._http_json("POST", "/legal-entities", {})
        missing_header = self._http_legal_entities(tenant_header=None)
        cross_status, _cross = self._http_legal_entities(
            tenant_header="urn:cwl:tenant_other"
        )
        self.assertEqual(post_status, 405)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before)
        server.shutdown()

        empty_tenant = self._seed_tenant_without_entities()
        empty_library = lookup_legal_entities(DATABASE_URL, empty_tenant)
        empty_server = self._start_http_server(empty_tenant)
        empty_status, empty_page = self._http_legal_entities(tenant_header=empty_tenant)
        self.assertEqual(empty_library["legal_entities"], [])
        self.assertEqual(empty_status, 200)
        self.assertEqual(empty_page["legal_entities"], [])
        self.assertEqual(empty_page["tenant_reference"], empty_tenant)
        empty_server.shutdown()
        missing_tenant = "urn:cwl:tenant:missing_entities"
        missing_server = self._start_http_server(missing_tenant)
        missing_status, _missing = self._http_legal_entities(tenant_header=missing_tenant)
        with self.assertRaisesRegex(AccountingValidationError, "tenant_account"):
            lookup_legal_entities(DATABASE_URL, missing_tenant)
        self.assertEqual(missing_status, 404)
        missing_server.shutdown()

    def test_http_reads_income_statement_and_balance_sheet(self) -> None:
        """GET projects IAS 1 classes from the same trial-balance totals as GET /trial-balances."""
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")

        empty_income_status, empty_income = self._http_financial_statement("income_statement")
        empty_sheet_status, empty_sheet = self._http_financial_statement("balance_sheet")
        empty_library = lookup_financial_statement(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
            "income_statement",
        )

        taxed = self._billing_taxed_payload()
        cash = self._billing_cash_payload()
        taxed_credit = self._billing_taxed_credit_payload()
        invoice_status, _invoice_receipt = self._http_json("POST", "/journal-proposals", taxed)
        cash_status, _cash_receipt = self._http_json("POST", "/journal-proposals", cash)
        credit_status, _credit_receipt = self._http_json(
            "POST", "/journal-proposals", taxed_credit
        )
        live_tb_status, live_tb = self._http_trial_balance()
        income_status, income = self._http_financial_statement("income_statement")
        sheet_status, sheet = self._http_financial_statement("balance_sheet")
        income_library = lookup_financial_statement(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
            "income_statement",
        )
        sheet_library = lookup_financial_statement(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
            "balance_sheet",
        )
        close_status, _close_receipt = self._http_json(
            "POST", "/period-closes", self._period_close_payload()
        )
        closed_tb_status, closed_tb = self._http_trial_balance()
        closed_income_status, closed_income = self._http_financial_statement(
            "income_statement"
        )
        closed_sheet_status, closed_sheet = self._http_financial_statement("balance_sheet")

        self.assertEqual(empty_income_status, 200)
        self.assertEqual(empty_sheet_status, 200)
        self.assertEqual(empty_income, empty_library)
        self.assertEqual(empty_income["statement_lines"], [])
        self.assertEqual(empty_sheet["statement_lines"], [])
        self.assertEqual(empty_income["total_debit_amount"], "0")
        self.assertEqual(empty_income["total_credit_amount"], "0")
        self.assertEqual(empty_income["net_income_amount"], "0")
        self.assertEqual(empty_sheet["total_debit_amount"], "0")
        self.assertEqual(empty_sheet["total_credit_amount"], "0")
        self.assertEqual(empty_sheet["net_income_amount"], "0")
        self.assertEqual(invoice_status, 200)
        self.assertEqual(cash_status, 200)
        self.assertEqual(credit_status, 200)
        self.assertEqual(live_tb_status, 200)
        self.assertEqual(income_status, 200)
        self.assertEqual(sheet_status, 200)
        self.assertEqual(income, income_library)
        self.assertEqual(sheet, sheet_library)
        self.assertNotIn("comparison_fiscal_period_reference", income)
        self.assertNotIn("comparison_statement_lines", income)
        self.assertNotIn("comparison_net_income_amount", sheet)
        self.assertEqual(income["tenant_reference"], self.policy.tenant_reference)
        self.assertEqual(income["legal_entity_reference"], self.policy.legal_entity_reference)
        self.assertEqual(income["accounting_book_reference"], self.policy.accounting_book_reference)
        self.assertEqual(income["book_reference"], self.policy.accounting_book_reference)
        self.assertEqual(
            income["fiscal_period_reference"], "urn:cwl:accounting:fiscal_period:2026-08"
        )
        self.assertEqual(income["statement_type_code"], "income_statement")
        self.assertEqual(sheet["statement_type_code"], "balance_sheet")
        income_by_code = {
            str(item["chart_account_code"]): item for item in income["statement_lines"]
        }
        sheet_by_code = {
            str(item["chart_account_code"]): item for item in sheet["statement_lines"]
        }
        self.assertEqual(set(income_by_code), {"410100"})
        self.assertNotIn("110100", income_by_code)
        self.assertNotIn("110200", income_by_code)
        self.assertNotIn("210100", income_by_code)
        self.assertEqual(income_by_code["410100"]["account_role_code"], "usage_revenue")
        self.assertEqual(income_by_code["410100"]["account_class_code"], "revenue")
        self.assertEqual(
            Decimal(str(income_by_code["410100"]["debit_amount"])),
            Decimal(str(self._trial_balance_line(live_tb, "410100")["debit_amount"])),
        )
        self.assertEqual(
            Decimal(str(income_by_code["410100"]["credit_amount"])),
            Decimal(str(self._trial_balance_line(live_tb, "410100")["credit_amount"])),
        )
        self.assertEqual(set(sheet_by_code), {"110100", "110200", "210100"})
        self.assertNotIn("410100", sheet_by_code)
        self.assertEqual(sheet_by_code["110100"]["account_role_code"], "accounts_receivable")
        self.assertEqual(sheet_by_code["110100"]["account_class_code"], "asset")
        self.assertEqual(sheet_by_code["110200"]["account_role_code"], "cash_receipt")
        self.assertEqual(sheet_by_code["110200"]["account_class_code"], "asset")
        self.assertEqual(sheet_by_code["210100"]["account_role_code"], "tax_payable")
        self.assertEqual(sheet_by_code["210100"]["account_class_code"], "liability")
        for account_code in ("110100", "110200", "210100"):
            self.assertEqual(
                Decimal(str(sheet_by_code[account_code]["debit_amount"])),
                Decimal(str(self._trial_balance_line(live_tb, account_code)["debit_amount"])),
            )
            self.assertEqual(
                Decimal(str(sheet_by_code[account_code]["credit_amount"])),
                Decimal(str(self._trial_balance_line(live_tb, account_code)["credit_amount"])),
            )
        income_debit_total = sum(
            Decimal(str(item["debit_amount"])) for item in income["statement_lines"]
        )
        income_credit_total = sum(
            Decimal(str(item["credit_amount"])) for item in income["statement_lines"]
        )
        self.assertEqual(Decimal(str(income["total_debit_amount"])), income_debit_total)
        self.assertEqual(Decimal(str(income["total_credit_amount"])), income_credit_total)
        self.assertEqual(
            Decimal(str(income["net_income_amount"])),
            income_credit_total - income_debit_total,
        )
        sheet_debit_total = sum(
            Decimal(str(item["debit_amount"])) for item in sheet["statement_lines"]
        )
        sheet_credit_total = sum(
            Decimal(str(item["credit_amount"])) for item in sheet["statement_lines"]
        )
        self.assertEqual(Decimal(str(sheet["total_debit_amount"])), sheet_debit_total)
        self.assertEqual(Decimal(str(sheet["total_credit_amount"])), sheet_credit_total)
        self.assertEqual(sheet["net_income_amount"], income["net_income_amount"])
        asset_net = sum(
            Decimal(str(item["debit_amount"])) - Decimal(str(item["credit_amount"]))
            for item in sheet["statement_lines"]
            if item["account_class_code"] == "asset"
        )
        liability_net = sum(
            Decimal(str(item["credit_amount"])) - Decimal(str(item["debit_amount"]))
            for item in sheet["statement_lines"]
            if item["account_class_code"] == "liability"
        )
        equity_net = sum(
            Decimal(str(item["credit_amount"])) - Decimal(str(item["debit_amount"]))
            for item in sheet["statement_lines"]
            if item["account_class_code"] == "equity"
        )
        self.assertEqual(
            asset_net,
            liability_net + equity_net + Decimal(str(sheet["net_income_amount"])),
        )
        self.assertEqual(close_status, 200)
        self.assertEqual(closed_tb_status, 200)
        self.assertEqual(closed_income_status, 200)
        self.assertEqual(closed_sheet_status, 200)
        self.assertEqual(closed_tb["balance_source_code"], "snapshot")
        self.assertEqual(
            closed_income["statement_lines"], income["statement_lines"]
        )
        self.assertEqual(closed_sheet["statement_lines"], sheet["statement_lines"])
        self.assertEqual(closed_income["net_income_amount"], income["net_income_amount"])
        self.assertEqual(closed_sheet["net_income_amount"], "0")
        self.assertEqual(self._count_closing_journals(), 0)
        self.assertEqual(
            Decimal(str(self._trial_balance_line(closed_tb, "410100")["net_balance_amount"])),
            Decimal("0"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(closed_tb, "410100")["debit_amount"])),
            Decimal(str(income_by_code["410100"]["debit_amount"])),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(closed_tb, "410100")["credit_amount"])),
            Decimal(str(income_by_code["410100"]["credit_amount"])),
        )

        post_status, _post_body = self._http_json("POST", "/financial-statements", {})
        missing_header = self._http_financial_statement(
            "income_statement", tenant_header=None
        )
        cross_status, _cross = self._http_financial_statement(
            "income_statement", tenant_header="urn:cwl:tenant_other"
        )
        missing_query = self._http_json("GET", "/financial-statements", None)
        missing_book_query = self._http_json(
            "GET",
            "/financial-statements?"
            + urllib.parse.urlencode(
                {
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
                    "statement_type_code": "income_statement",
                }
            ),
            None,
        )
        missing_type = self._http_json(
            "GET",
            "/financial-statements?"
            + urllib.parse.urlencode(
                {
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "book_reference": self.policy.accounting_book_reference,
                    "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
                }
            ),
            None,
        )
        bad_type = self._http_financial_statement("funds_flow")
        unknown_period = self._http_financial_statement(
            "income_statement",
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01",
        )
        unknown_entity = self._http_financial_statement(
            "income_statement",
            legal_entity_reference="urn:cwl:legal_entity:missing",
        )
        missing_book = self._http_financial_statement(
            "income_statement",
            book_reference="urn:cwl:accounting_book:missing",
        )
        with self.assertRaisesRegex(AccountingValidationError, "statement_type_code"):
            lookup_financial_statement(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
                "funds_flow",
            )
        with self.assertRaisesRegex(AccountingValidationError, "fiscal_period_reference"):
            lookup_financial_statement(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "",
                "income_statement",
            )
        with self.assertRaisesRegex(AccountingValidationError, "statement_type_code"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_financial_statement(
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "2026-08",
                "funds_flow",
            )
        self._delete_role_mapping("usage_revenue")
        with self.assertRaisesRegex(AccountingValidationError, "account_role_mapping"):
            lookup_financial_statement(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
                "income_statement",
            )
        with self.assertRaisesRegex(AccountingValidationError, "account_role_mapping"):
            lookup_financial_statement(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
                "balance_sheet",
            )
        self._seed_role_mapping("usage_revenue", "410100")

        self.assertEqual(post_status, 405)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(missing_query[0], 400)
        self.assertEqual(missing_book_query[0], 400)
        self.assertEqual(missing_type[0], 400)
        self.assertEqual(bad_type[0], 400)
        self.assertEqual(unknown_period[0], 404)
        self.assertEqual(unknown_entity[0], 404)
        self.assertEqual(missing_book[0], 404)
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before + 3)
        server.shutdown()

    def test_http_hard_close_parks_earnings_in_retained_earnings(self) -> None:
        """Hard-close parks remaining earnings in 310100 and keeps pre-close P&L."""
        taxed = self._billing_taxed_payload()
        cash = self._billing_cash_payload()
        taxed_credit = self._billing_taxed_credit_payload()
        remaining = self._billing_validated_payload()
        server = self._start_http_server()
        self._http_json("POST", "/journal-proposals", taxed)
        self._http_json("POST", "/journal-proposals", cash)
        self._http_json("POST", "/journal-proposals", taxed_credit)
        self._http_json("POST", "/journal-proposals", remaining)
        pre_income_status, pre_income = self._http_financial_statement("income_statement")
        pre_sheet_status, pre_sheet = self._http_financial_statement("balance_sheet")
        soft_status, _soft = self._http_json(
            "POST",
            "/period-closes",
            self._period_close_payload(period_status_code="soft_closed"),
        )
        soft_tb_status, soft_tb = self._http_trial_balance()
        self.assertEqual(soft_status, 200)
        self.assertEqual(soft_tb_status, 200)
        self.assertEqual(self._count_closing_journals(), 0)
        self.assertNotIn(
            "310100",
            {str(item["chart_account_code"]) for item in soft_tb["lines"]},
        )
        hard_status, hard_receipt = self._http_json(
            "POST", "/period-closes", self._period_close_payload()
        )
        replay_status, replay_receipt = self._http_json(
            "POST", "/period-closes", self._period_close_payload()
        )
        closed_income_status, closed_income = self._http_financial_statement(
            "income_statement"
        )
        closed_sheet_status, closed_sheet = self._http_financial_statement(
            "balance_sheet"
        )
        closed_tb_status, closed_tb = self._http_trial_balance()
        mapping_status, mappings = self._http_account_role_mappings()
        chart_status, charts = self._http_chart_accounts()
        snapshots = self._count_table("accounting_reporting.trial_balance_snapshot")
        cross_status, _cross = self._http_json(
            "POST",
            "/period-closes",
            self._period_close_payload(),
            tenant_header="urn:cwl:tenant_other",
        )
        income_by_code = {
            str(item["chart_account_code"]): item
            for item in closed_income["statement_lines"]
        }
        sheet_by_code = {
            str(item["chart_account_code"]): item
            for item in closed_sheet["statement_lines"]
        }
        mapping_by_role = {
            str(item["account_role_code"]): item for item in mappings["mappings"]
        }
        chart_by_code = {
            str(item["chart_account_code"]): item for item in charts["chart_accounts"]
        }

        self.assertEqual(pre_income_status, 200)
        self.assertEqual(pre_sheet_status, 200)
        self.assertEqual(hard_status, 200)
        self.assertEqual(replay_status, 200)
        self.assertTrue(replay_receipt["replayed"])
        self.assertEqual(replay_receipt["snapshot_record_id"], hard_receipt["snapshot_record_id"])
        self.assertEqual(self._count_closing_journals(), 1)
        self.assertEqual(closed_income_status, 200)
        self.assertEqual(closed_sheet_status, 200)
        self.assertEqual(closed_tb_status, 200)
        self.assertEqual(closed_income["net_income_amount"], pre_income["net_income_amount"])
        self.assertEqual(closed_income["statement_lines"], pre_income["statement_lines"])
        self.assertEqual(income_by_code["410100"]["account_role_code"], "usage_revenue")
        self.assertEqual(closed_sheet["net_income_amount"], "0")
        self.assertEqual(sheet_by_code["310100"]["account_role_code"], "retained_earnings")
        self.assertEqual(sheet_by_code["310100"]["account_class_code"], "equity")
        self.assertEqual(
            Decimal(str(sheet_by_code["310100"]["credit_amount"])),
            Decimal(str(pre_income["net_income_amount"])),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(closed_tb, "410100")["net_balance_amount"])),
            Decimal("0"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(closed_tb, "310100")["credit_amount"])),
            Decimal(str(pre_income["net_income_amount"])),
        )
        self.assertEqual(mapping_status, 200)
        self.assertEqual(mapping_by_role["retained_earnings"]["chart_account_code"], "310100")
        self.assertEqual(chart_status, 200)
        self.assertEqual(chart_by_code["310100"]["account_class_code"], "equity")
        self.assertEqual(cross_status, 403)
        self.assertEqual(
            self._count_table("accounting_reporting.trial_balance_snapshot"), snapshots
        )
        self.assertEqual(self._count_closing_journals(), 1)
        server.shutdown()

    def test_http_compares_financial_statements_across_periods(self) -> None:
        """Optional comparison_fiscal_period_reference returns prior-period lines on the same GET."""
        self._seed_additional_period("2026-06", date(2026, 6, 1), date(2026, 6, 30))
        self._seed_additional_period("2026-07", date(2026, 7, 1), date(2026, 7, 31))
        prior = self._billing_validated_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:invoice_draft:july:"
                f"sha256:{'4' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "4" * 64,
            transaction_date="2026-07-15",
            accounting_date="2026-07-15",
            proposed_at="2026-07-15T00:00:00Z",
            source_event_references=(
                f"{self.policy.tenant_reference}:invoice_draft:july",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "10000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "0",
                    "credit_amount": "10000",
                },
            ],
        )
        current = self._billing_taxed_payload()
        server = self._start_http_server()
        self._http_json("POST", "/journal-proposals", prior)
        self._http_json("POST", "/journal-proposals", current)
        journals_before = self._count_table("accounting_core.general_journal")
        snapshots_before = self._count_table("accounting_reporting.trial_balance_snapshot")

        omit_status, omit_income = self._http_financial_statement("income_statement")
        omit_sheet_status, omit_sheet = self._http_financial_statement("balance_sheet")
        compare_income_status, compare_income = self._http_financial_statement(
            "income_statement",
            comparison_fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07",
        )
        compare_sheet_status, compare_sheet = self._http_financial_statement(
            "balance_sheet",
            comparison_fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07",
        )
        empty_status, empty_compare = self._http_financial_statement(
            "income_statement",
            comparison_fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-06",
        )
        library = lookup_financial_statement(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
            "income_statement",
            comparison_fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07",
        )
        prior_only = lookup_financial_statement(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-07",
            "income_statement",
        )
        comparison_keys = {
            "comparison_fiscal_period_reference",
            "comparison_statement_lines",
            "comparison_total_debit_amount",
            "comparison_total_credit_amount",
            "comparison_net_income_amount",
        }
        current_income_by_code = {
            str(item["chart_account_code"]): item
            for item in compare_income["statement_lines"]
        }
        prior_income_by_code = {
            str(item["chart_account_code"]): item
            for item in compare_income["comparison_statement_lines"]
        }
        current_sheet_by_code = {
            str(item["chart_account_code"]): item
            for item in compare_sheet["statement_lines"]
        }
        prior_sheet_by_code = {
            str(item["chart_account_code"]): item
            for item in compare_sheet["comparison_statement_lines"]
        }

        self.assertEqual(omit_status, 200)
        self.assertEqual(omit_sheet_status, 200)
        self.assertEqual(compare_income_status, 200)
        self.assertEqual(compare_sheet_status, 200)
        self.assertEqual(empty_status, 200)
        self.assertFalse(comparison_keys & set(omit_income))
        self.assertFalse(comparison_keys & set(omit_sheet))
        self.assertEqual(compare_income, library)
        self.assertEqual(
            compare_income["comparison_fiscal_period_reference"],
            "urn:cwl:accounting:fiscal_period:2026-07",
        )
        self.assertEqual(compare_income["comparison_statement_lines"], prior_only["statement_lines"])
        self.assertEqual(compare_income["comparison_net_income_amount"], prior_only["net_income_amount"])
        self.assertEqual(Decimal(str(prior_only["net_income_amount"])), Decimal("10000"))
        self.assertEqual(Decimal(str(compare_income["net_income_amount"])), Decimal("25000"))
        self.assertEqual(
            Decimal(str(current_income_by_code["410100"]["credit_amount"])),
            Decimal("25000"),
        )
        self.assertEqual(
            Decimal(str(prior_income_by_code["410100"]["credit_amount"])),
            Decimal("10000"),
        )
        self.assertEqual(Decimal(str(compare_sheet["net_income_amount"])), Decimal("25000"))
        self.assertEqual(
            Decimal(str(compare_sheet["comparison_net_income_amount"])),
            Decimal("10000"),
        )
        self.assertEqual(
            Decimal(str(current_sheet_by_code["110100"]["debit_amount"])),
            Decimal("37500"),
        )
        self.assertEqual(
            Decimal(str(prior_sheet_by_code["110100"]["debit_amount"])),
            Decimal("10000"),
        )
        self.assertEqual(empty_compare["comparison_statement_lines"], [])
        self.assertEqual(
            Decimal(str(empty_compare["comparison_total_debit_amount"])),
            Decimal("0"),
        )
        self.assertEqual(
            Decimal(str(empty_compare["comparison_total_credit_amount"])),
            Decimal("0"),
        )
        self.assertEqual(
            Decimal(str(empty_compare["comparison_net_income_amount"])),
            Decimal("0"),
        )

        july_close_status, _july_close = self._http_json(
            "POST",
            "/period-closes",
            self._period_close_payload(
                fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07"
            ),
        )
        closed_compare_status, closed_compare = self._http_financial_statement(
            "balance_sheet",
            comparison_fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07",
        )
        live_current_status, live_current = self._http_trial_balance()
        closed_prior_status, closed_prior = self._http_trial_balance(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07"
        )
        closed_sheet_by_code = {
            str(item["chart_account_code"]): item
            for item in closed_compare["comparison_statement_lines"]
        }
        self.assertEqual(july_close_status, 200)
        self.assertEqual(closed_compare_status, 200)
        self.assertEqual(live_current_status, 200)
        self.assertEqual(closed_prior_status, 200)
        self.assertEqual(live_current["balance_source_code"], "live")
        self.assertEqual(closed_prior["balance_source_code"], "snapshot")
        self.assertEqual(Decimal(str(closed_compare["net_income_amount"])), Decimal("25000"))
        self.assertEqual(
            Decimal(str(closed_compare["comparison_net_income_amount"])),
            Decimal("0"),
        )
        self.assertEqual(closed_sheet_by_code["310100"]["account_role_code"], "retained_earnings")
        self.assertEqual(
            Decimal(str(closed_sheet_by_code["310100"]["credit_amount"])),
            Decimal("10000"),
        )

        unknown_status, _unknown = self._http_financial_statement(
            "income_statement",
            comparison_fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01",
        )
        other_entity_status, _other_entity = self._http_financial_statement(
            "income_statement",
            legal_entity_reference="urn:cwl:legal_entity:missing",
            comparison_fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07",
        )
        other_book_status, _other_book = self._http_financial_statement(
            "income_statement",
            book_reference="urn:cwl:accounting_book:missing",
            comparison_fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07",
        )
        cross_status, _cross = self._http_financial_statement(
            "income_statement",
            comparison_fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07",
            tenant_header="urn:cwl:tenant_other",
        )
        with self.assertRaisesRegex(AccountingValidationError, "Fiscal period 1999-01"):
            lookup_financial_statement(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
                "income_statement",
                comparison_fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01",
            )

        self.assertEqual(unknown_status, 404)
        self.assertEqual(other_entity_status, 404)
        self.assertEqual(other_book_status, 404)
        self.assertEqual(cross_status, 403)
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before + 1)
        self.assertEqual(
            self._count_table("accounting_reporting.trial_balance_snapshot"),
            snapshots_before + 1,
        )
        server.shutdown()

    def test_http_reads_year_to_date_financial_statements(self) -> None:
        """Optional statement_scope_code=year_to_date sums same-year P&L and reuses period BS."""
        self._seed_additional_period("midyear", date(2026, 5, 1), date(2026, 5, 31))
        self._seed_additional_period("2026-07", date(2026, 7, 1), date(2026, 7, 31))
        prior = self._billing_validated_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:invoice_draft:july:"
                f"sha256:{'4' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "4" * 64,
            transaction_date="2026-07-15",
            accounting_date="2026-07-15",
            proposed_at="2026-07-15T00:00:00Z",
            source_event_references=(
                f"{self.policy.tenant_reference}:invoice_draft:july",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "10000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "0",
                    "credit_amount": "10000",
                },
            ],
        )
        current = self._billing_taxed_payload()
        server = self._start_http_server()
        self._http_json("POST", "/journal-proposals", prior)
        july_close_status, _july_close = self._http_json(
            "POST",
            "/period-closes",
            self._period_close_payload(
                fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07"
            ),
        )
        self._http_json("POST", "/journal-proposals", current)
        journals_before = self._count_table("accounting_core.general_journal")
        snapshots_before = self._count_table("accounting_reporting.trial_balance_snapshot")

        omit_status, omit_income = self._http_financial_statement("income_statement")
        explicit_period_status, explicit_period = self._http_financial_statement(
            "income_statement",
            statement_scope_code="period",
        )
        ytd_income_status, ytd_income = self._http_financial_statement(
            "income_statement",
            statement_scope_code="year_to_date",
        )
        ytd_sheet_status, ytd_sheet = self._http_financial_statement(
            "balance_sheet",
            statement_scope_code="year_to_date",
        )
        period_sheet_status, period_sheet = self._http_financial_statement("balance_sheet")
        july_period_sheet_status, july_period_sheet = self._http_financial_statement(
            "balance_sheet",
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07",
        )
        july_ytd_sheet_status, july_ytd_sheet = self._http_financial_statement(
            "balance_sheet",
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07",
            statement_scope_code="year_to_date",
        )
        compare_ytd_status, compare_ytd = self._http_financial_statement(
            "income_statement",
            statement_scope_code="year_to_date",
            comparison_fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07",
        )
        midyear_ytd_status, midyear_ytd = self._http_financial_statement(
            "income_statement",
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:midyear",
            statement_scope_code="year_to_date",
        )
        library = lookup_financial_statement(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
            "income_statement",
            statement_scope_code="year_to_date",
        )
        persist = PostgresPostingLedger(
            DATABASE_URL, self.policy.tenant_reference
        ).load_financial_statement(
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "2026-08",
            "income_statement",
            statement_scope_code="year_to_date",
        )
        period_income_by_code = {
            str(item["chart_account_code"]): item
            for item in omit_income["statement_lines"]
        }
        ytd_income_by_code = {
            str(item["chart_account_code"]): item
            for item in ytd_income["statement_lines"]
        }
        july_sheet_by_code = {
            str(item["chart_account_code"]): item
            for item in july_ytd_sheet["statement_lines"]
        }

        self.assertEqual(july_close_status, 200)
        self.assertEqual(omit_status, 200)
        self.assertEqual(explicit_period_status, 200)
        self.assertEqual(ytd_income_status, 200)
        self.assertEqual(ytd_sheet_status, 200)
        self.assertEqual(period_sheet_status, 200)
        self.assertEqual(july_period_sheet_status, 200)
        self.assertEqual(july_ytd_sheet_status, 200)
        self.assertEqual(compare_ytd_status, 200)
        self.assertEqual(midyear_ytd_status, 200)
        self.assertEqual(omit_income, explicit_period)
        self.assertNotIn("statement_scope_code", omit_income)
        self.assertNotIn("statement_scope_code", explicit_period)
        self.assertEqual(ytd_income, library)
        self.assertEqual(ytd_income, persist)
        self.assertEqual(ytd_income["statement_scope_code"], "year_to_date")
        self.assertEqual(
            ytd_income["fiscal_period_reference"],
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        self.assertEqual(Decimal(str(omit_income["net_income_amount"])), Decimal("25000"))
        self.assertEqual(
            Decimal(str(period_income_by_code["410100"]["credit_amount"])),
            Decimal("25000"),
        )
        self.assertEqual(Decimal(str(ytd_income["net_income_amount"])), Decimal("35000"))
        self.assertEqual(
            Decimal(str(ytd_income_by_code["410100"]["credit_amount"])),
            Decimal("35000"),
        )
        self.assertEqual(ytd_sheet["statement_lines"], period_sheet["statement_lines"])
        self.assertEqual(ytd_sheet["total_debit_amount"], period_sheet["total_debit_amount"])
        self.assertEqual(ytd_sheet["total_credit_amount"], period_sheet["total_credit_amount"])
        self.assertEqual(ytd_sheet["net_income_amount"], period_sheet["net_income_amount"])
        self.assertEqual(ytd_sheet["statement_scope_code"], "year_to_date")
        self.assertNotIn("statement_scope_code", period_sheet)
        self.assertEqual(july_ytd_sheet["statement_lines"], july_period_sheet["statement_lines"])
        self.assertEqual(july_ytd_sheet["net_income_amount"], july_period_sheet["net_income_amount"])
        self.assertEqual(july_ytd_sheet["statement_scope_code"], "year_to_date")
        self.assertEqual(july_ytd_sheet["net_income_amount"], "0")
        self.assertEqual(july_sheet_by_code["310100"]["account_role_code"], "retained_earnings")
        self.assertEqual(
            Decimal(str(july_sheet_by_code["310100"]["credit_amount"])),
            Decimal("10000"),
        )
        self.assertEqual(compare_ytd["statement_scope_code"], "year_to_date")
        self.assertEqual(
            compare_ytd["comparison_fiscal_period_reference"],
            "urn:cwl:accounting:fiscal_period:2026-07",
        )
        self.assertEqual(Decimal(str(compare_ytd["net_income_amount"])), Decimal("35000"))
        self.assertEqual(
            Decimal(str(compare_ytd["comparison_net_income_amount"])),
            Decimal("10000"),
        )
        self.assertEqual(midyear_ytd["statement_lines"], [])
        self.assertEqual(midyear_ytd["net_income_amount"], "0")
        self.assertEqual(midyear_ytd["statement_scope_code"], "year_to_date")

        bad_scope = self._http_financial_statement(
            "income_statement",
            statement_scope_code="quarter_to_date",
        )
        cross_status, _cross = self._http_financial_statement(
            "income_statement",
            statement_scope_code="year_to_date",
            tenant_header="urn:cwl:tenant_other",
        )
        with self.assertRaisesRegex(AccountingValidationError, "statement_scope_code"):
            lookup_financial_statement(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
                "income_statement",
                statement_scope_code="quarter_to_date",
            )
        with self.assertRaisesRegex(AccountingValidationError, "statement_scope_code"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_financial_statement(
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "2026-08",
                "income_statement",
                statement_scope_code="quarter_to_date",
            )
        with self.assertRaisesRegex(AccountingValidationError, "fiscal year"):
            _fiscal_year_identity("", None)

        self.assertEqual(bad_scope[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before)
        self.assertEqual(
            self._count_table("accounting_reporting.trial_balance_snapshot"),
            snapshots_before,
        )
        server.shutdown()

    def test_http_reads_changes_in_equity_statement(self) -> None:
        """GET /financial-statements?statement_type_code=changes_in_equity ties opening + NI + other to closing equity."""
        self._seed_issued_capital_account()
        self._seed_additional_period("2026-06", date(2026, 6, 1), date(2026, 6, 30))
        self._seed_additional_period("2026-07", date(2026, 7, 1), date(2026, 7, 31))
        july = self._billing_validated_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:invoice_draft:july:"
                f"sha256:{'4' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "4" * 64,
            transaction_date="2026-07-15",
            accounting_date="2026-07-15",
            proposed_at="2026-07-15T00:00:00Z",
            source_event_references=(
                f"{self.policy.tenant_reference}:invoice_draft:july",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "10000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "0",
                    "credit_amount": "10000",
                },
            ],
        )
        june_loss = self._adjusting_journal_payload(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-06",
            journal_date="2026-06-15",
            idempotency_key=f"{self.policy.tenant_reference}:adjusting_journal:june-loss:v1",
            journal_description="June usage cost",
            journal_lines=[
                {
                    "chart_account_code": "510100",
                    "debit_credit_code": "debit",
                    "amount": "1000",
                    "currency_code": "KRW",
                },
                {
                    "chart_account_code": "110200",
                    "debit_credit_code": "credit",
                    "amount": "1000",
                    "currency_code": "KRW",
                },
            ],
        )
        capital = self._adjusting_journal_payload(
            idempotency_key=f"{self.policy.tenant_reference}:adjusting_journal:issued-capital:v1",
            journal_description="Owner capital contribution",
            journal_lines=[
                {
                    "chart_account_code": "110200",
                    "debit_credit_code": "debit",
                    "amount": "5000",
                    "currency_code": "KRW",
                },
                {
                    "chart_account_code": "320100",
                    "debit_credit_code": "credit",
                    "amount": "5000",
                    "currency_code": "KRW",
                },
            ],
        )
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")

        empty_status, empty = self._http_financial_statement("changes_in_equity")
        empty_library = lookup_financial_statement(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
            "changes_in_equity",
        )
        empty_by_role = {
            str(item["account_role_code"]): item for item in empty["statement_lines"]
        }

        self.assertEqual(empty_status, 200)
        self.assertEqual(empty, empty_library)
        self.assertEqual(empty["statement_type_code"], "changes_in_equity")
        self.assertNotIn("statement_scope_code", empty)
        self.assertNotIn("comparison_fiscal_period_reference", empty)
        self.assertEqual(
            list(empty_by_role),
            [
                "opening_equity",
                "period_net_income",
                "other_equity_movements",
                "closing_equity",
            ],
        )
        for role_code in empty_by_role:
            self.assertEqual(empty_by_role[role_code]["account_class_code"], "equity")
            self.assertEqual(Decimal(str(empty_by_role[role_code]["debit_amount"])), Decimal("0"))
            self.assertEqual(Decimal(str(empty_by_role[role_code]["credit_amount"])), Decimal("0"))
        self.assertEqual(empty["net_income_amount"], "0")

        june_status, _june = self._http_json("POST", "/journals", june_loss)
        july_status, _july = self._http_json("POST", "/journal-proposals", july)
        august_status, _august = self._http_json(
            "POST", "/journal-proposals", self._billing_taxed_payload()
        )
        capital_status, _capital = self._http_json("POST", "/journals", capital)
        income_status, income = self._http_financial_statement("income_statement")
        sheet_status, sheet = self._http_financial_statement("balance_sheet")
        equity_status, equity = self._http_financial_statement("changes_in_equity")
        library = lookup_financial_statement(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
            "changes_in_equity",
        )
        persist = PostgresPostingLedger(
            DATABASE_URL, self.policy.tenant_reference
        ).load_financial_statement(
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "2026-08",
            "changes_in_equity",
        )
        by_role = {str(item["account_role_code"]): item for item in equity["statement_lines"]}
        sheet_equity = sum(
            Decimal(str(item["credit_amount"])) - Decimal(str(item["debit_amount"]))
            for item in sheet["statement_lines"]
            if item["account_class_code"] == "equity"
        )
        opening = Decimal(str(by_role["opening_equity"]["credit_amount"])) - Decimal(
            str(by_role["opening_equity"]["debit_amount"])
        )
        period_ni = Decimal(str(by_role["period_net_income"]["credit_amount"])) - Decimal(
            str(by_role["period_net_income"]["debit_amount"])
        )
        other = Decimal(str(by_role["other_equity_movements"]["credit_amount"])) - Decimal(
            str(by_role["other_equity_movements"]["debit_amount"])
        )
        closing = Decimal(str(by_role["closing_equity"]["credit_amount"])) - Decimal(
            str(by_role["closing_equity"]["debit_amount"])
        )

        self.assertEqual(june_status, 200)
        self.assertEqual(july_status, 200)
        self.assertEqual(august_status, 200)
        self.assertEqual(capital_status, 200)
        self.assertEqual(income_status, 200)
        self.assertEqual(sheet_status, 200)
        self.assertEqual(equity_status, 200)
        self.assertEqual(equity, library)
        self.assertEqual(equity, persist)
        self.assertEqual(period_ni, Decimal(str(income["net_income_amount"])))
        self.assertEqual(opening, Decimal("0"))
        self.assertEqual(other, Decimal("5000"))
        self.assertEqual(closing, opening + period_ni + other)
        self.assertEqual(closing, sheet_equity + Decimal(str(sheet["net_income_amount"])))

        soft_status, _soft = self._http_json(
            "POST", "/period-closes", self._period_close_payload(period_status_code="soft_closed")
        )
        soft_equity_status, soft_equity = self._http_financial_statement("changes_in_equity")
        self.assertEqual(soft_status, 200)
        self.assertEqual(soft_equity_status, 200)
        self.assertEqual(soft_equity["statement_lines"], equity["statement_lines"])
        self.assertEqual(soft_equity["net_income_amount"], equity["net_income_amount"])

        july_close_status, _july_close = self._http_json(
            "POST",
            "/period-closes",
            self._period_close_payload(
                fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07"
            ),
        )
        opened_after_july_status, opened_after_july = self._http_financial_statement(
            "changes_in_equity"
        )
        opened_after_july_roles = {
            str(item["account_role_code"]): item
            for item in opened_after_july["statement_lines"]
        }
        self.assertEqual(july_close_status, 200)
        self.assertEqual(opened_after_july_status, 200)
        self.assertEqual(
            Decimal(str(opened_after_july_roles["opening_equity"]["credit_amount"]))
            - Decimal(str(opened_after_july_roles["opening_equity"]["debit_amount"])),
            Decimal("9000"),
        )
        self.assertEqual(
            Decimal(str(opened_after_july_roles["other_equity_movements"]["credit_amount"]))
            - Decimal(str(opened_after_july_roles["other_equity_movements"]["debit_amount"])),
            Decimal("5000"),
        )

        hard_status, _hard = self._http_json("POST", "/period-closes", self._period_close_payload())
        closed_income_status, closed_income = self._http_financial_statement("income_statement")
        closed_sheet_status, closed_sheet = self._http_financial_statement("balance_sheet")
        closed_equity_status, closed_equity = self._http_financial_statement("changes_in_equity")
        closed_roles = {
            str(item["account_role_code"]): item
            for item in closed_equity["statement_lines"]
        }
        closed_sheet_equity = sum(
            Decimal(str(item["credit_amount"])) - Decimal(str(item["debit_amount"]))
            for item in closed_sheet["statement_lines"]
            if item["account_class_code"] == "equity"
        )
        closed_ni = Decimal(str(closed_roles["period_net_income"]["credit_amount"])) - Decimal(
            str(closed_roles["period_net_income"]["debit_amount"])
        )
        closed_other = Decimal(
            str(closed_roles["other_equity_movements"]["credit_amount"])
        ) - Decimal(str(closed_roles["other_equity_movements"]["debit_amount"]))
        closed_closing = Decimal(str(closed_roles["closing_equity"]["credit_amount"])) - Decimal(
            str(closed_roles["closing_equity"]["debit_amount"])
        )
        self.assertEqual(hard_status, 200)
        self.assertEqual(closed_income_status, 200)
        self.assertEqual(closed_sheet_status, 200)
        self.assertEqual(closed_equity_status, 200)
        self.assertEqual(closed_ni, Decimal(str(income["net_income_amount"])))
        self.assertEqual(closed_ni, Decimal(str(closed_income["net_income_amount"])))
        self.assertEqual(closed_other, Decimal("5000"))
        self.assertEqual(closed_sheet["net_income_amount"], "0")
        self.assertEqual(closed_closing, closed_sheet_equity)
        self.assertEqual(
            closed_closing,
            Decimal(str(closed_roles["opening_equity"]["credit_amount"]))
            - Decimal(str(closed_roles["opening_equity"]["debit_amount"]))
            + closed_ni
            + closed_other,
        )

        ytd_status, ytd = self._http_financial_statement(
            "changes_in_equity",
            statement_scope_code="year_to_date",
        )
        ytd_income_status, ytd_income = self._http_financial_statement(
            "income_statement",
            statement_scope_code="year_to_date",
        )
        ytd_sheet_status, ytd_sheet = self._http_financial_statement(
            "balance_sheet",
            statement_scope_code="year_to_date",
        )
        ytd_roles = {str(item["account_role_code"]): item for item in ytd["statement_lines"]}
        ytd_sheet_equity = sum(
            Decimal(str(item["credit_amount"])) - Decimal(str(item["debit_amount"]))
            for item in ytd_sheet["statement_lines"]
            if item["account_class_code"] == "equity"
        )
        explicit_period_status, explicit_period = self._http_financial_statement(
            "changes_in_equity",
            statement_scope_code="period",
        )
        compare_status, compare = self._http_financial_statement(
            "changes_in_equity",
            comparison_fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-06",
        )
        june_only = lookup_financial_statement(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-06",
            "changes_in_equity",
        )
        compare_roles = {
            str(item["account_role_code"]): item
            for item in compare["comparison_statement_lines"]
        }

        self.assertEqual(ytd_status, 200)
        self.assertEqual(ytd_income_status, 200)
        self.assertEqual(ytd_sheet_status, 200)
        self.assertEqual(explicit_period_status, 200)
        self.assertEqual(compare_status, 200)
        self.assertEqual(ytd["statement_scope_code"], "year_to_date")
        self.assertNotIn("statement_scope_code", explicit_period)
        self.assertEqual(explicit_period["statement_lines"], closed_equity["statement_lines"])
        self.assertEqual(
            Decimal(str(ytd_roles["period_net_income"]["credit_amount"]))
            - Decimal(str(ytd_roles["period_net_income"]["debit_amount"])),
            Decimal(str(ytd_income["net_income_amount"])),
        )
        self.assertEqual(
            Decimal(str(ytd_roles["opening_equity"]["credit_amount"]))
            - Decimal(str(ytd_roles["opening_equity"]["debit_amount"])),
            Decimal("0"),
        )
        self.assertEqual(
            Decimal(str(ytd_roles["other_equity_movements"]["credit_amount"]))
            - Decimal(str(ytd_roles["other_equity_movements"]["debit_amount"])),
            Decimal("5000"),
        )
        self.assertEqual(
            Decimal(str(ytd_roles["closing_equity"]["credit_amount"]))
            - Decimal(str(ytd_roles["closing_equity"]["debit_amount"])),
            ytd_sheet_equity + Decimal(str(ytd_sheet["net_income_amount"])),
        )
        self.assertEqual(
            compare["comparison_fiscal_period_reference"],
            "urn:cwl:accounting:fiscal_period:2026-06",
        )
        self.assertEqual(compare["comparison_statement_lines"], june_only["statement_lines"])
        self.assertEqual(
            Decimal(str(compare_roles["period_net_income"]["debit_amount"]))
            - Decimal(str(compare_roles["period_net_income"]["credit_amount"])),
            Decimal("1000"),
        )
        self.assertNotIn("comparison_fiscal_period_reference", closed_equity)

        unknown_period = self._http_financial_statement(
            "changes_in_equity",
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01",
        )
        unknown_entity = self._http_financial_statement(
            "changes_in_equity",
            legal_entity_reference="urn:cwl:legal_entity:missing",
        )
        missing_header = self._http_financial_statement(
            "changes_in_equity", tenant_header=None
        )
        cross_status, _cross = self._http_financial_statement(
            "changes_in_equity", tenant_header="urn:cwl:tenant_other"
        )
        with self.assertRaisesRegex(AccountingValidationError, "statement_type_code"):
            lookup_financial_statement(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
                "funds_flow",
            )

        self.assertEqual(unknown_period[0], 404)
        self.assertEqual(unknown_entity[0], 404)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(
            self._count_table("accounting_core.general_journal"),
            journals_before + 6,
        )
        server.shutdown()

    def test_http_reads_cash_flow_statement(self) -> None:
        """GET /financial-statements?statement_type_code=cash_flow ties IAS 7 indirect cash to BS cash."""
        self._seed_additional_period("2026-06", date(2026, 6, 1), date(2026, 6, 30))
        self._seed_additional_period("2026-09", date(2026, 9, 1), date(2026, 9, 30))
        cash_flow_roles = [
            "period_net_income",
            "operating_working_capital",
            "cash_from_operations",
            "cash_from_investing",
            "cash_from_financing",
            "net_cash_change",
            "opening_cash",
            "closing_cash",
        ]
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")

        empty_status, empty = self._http_financial_statement("cash_flow")
        empty_library = lookup_financial_statement(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
            "cash_flow",
        )
        empty_by_role = {
            str(item["account_role_code"]): item for item in empty["statement_lines"]
        }

        self.assertEqual(empty_status, 200)
        self.assertEqual(empty, empty_library)
        self.assertEqual(empty["statement_type_code"], "cash_flow")
        self.assertNotIn("statement_scope_code", empty)
        self.assertNotIn("comparison_fiscal_period_reference", empty)
        self.assertEqual(list(empty_by_role), cash_flow_roles)
        for role_code in cash_flow_roles:
            self.assertEqual(empty_by_role[role_code]["chart_account_code"], "")
            self.assertEqual(empty_by_role[role_code]["account_class_code"], "")
            self.assertEqual(Decimal(str(empty_by_role[role_code]["debit_amount"])), Decimal("0"))
            self.assertEqual(Decimal(str(empty_by_role[role_code]["credit_amount"])), Decimal("0"))
        self.assertEqual(empty["net_income_amount"], "0")

        invoice_status, _invoice = self._http_json(
            "POST", "/journal-proposals", self._billing_validated_payload()
        )
        invoice_only_status, invoice_only = self._http_financial_statement("cash_flow")
        invoice_income_status, invoice_income = self._http_financial_statement(
            "income_statement"
        )
        invoice_roles = {
            str(item["account_role_code"]): item
            for item in invoice_only["statement_lines"]
        }
        invoice_ni = Decimal(str(invoice_roles["period_net_income"]["credit_amount"])) - Decimal(
            str(invoice_roles["period_net_income"]["debit_amount"])
        )
        invoice_wc = Decimal(
            str(invoice_roles["operating_working_capital"]["credit_amount"])
        ) - Decimal(str(invoice_roles["operating_working_capital"]["debit_amount"]))
        invoice_ops = Decimal(
            str(invoice_roles["cash_from_operations"]["credit_amount"])
        ) - Decimal(str(invoice_roles["cash_from_operations"]["debit_amount"]))
        invoice_closing = Decimal(str(invoice_roles["closing_cash"]["credit_amount"])) - Decimal(
            str(invoice_roles["closing_cash"]["debit_amount"])
        )

        self.assertEqual(invoice_status, 200)
        self.assertEqual(invoice_only_status, 200)
        self.assertEqual(invoice_income_status, 200)
        self.assertEqual(invoice_ni, Decimal(str(invoice_income["net_income_amount"])))
        self.assertEqual(invoice_ni, Decimal("25000"))
        self.assertEqual(invoice_wc, Decimal("-25000"))
        self.assertEqual(invoice_ops, Decimal("0"))
        self.assertEqual(invoice_closing, Decimal("0"))
        self.assertEqual(
            Decimal(str(invoice_roles["cash_from_investing"]["credit_amount"]))
            - Decimal(str(invoice_roles["cash_from_investing"]["debit_amount"])),
            Decimal("0"),
        )
        self.assertEqual(
            Decimal(str(invoice_roles["cash_from_financing"]["credit_amount"]))
            - Decimal(str(invoice_roles["cash_from_financing"]["debit_amount"])),
            Decimal("0"),
        )

        cash_status, _cash = self._http_json(
            "POST", "/journal-proposals", self._billing_cash_payload()
        )
        income_status, income = self._http_financial_statement("income_statement")
        sheet_status, sheet = self._http_financial_statement("balance_sheet")
        cash_flow_status, cash_flow = self._http_financial_statement("cash_flow")
        library = lookup_financial_statement(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
            "cash_flow",
        )
        persist = PostgresPostingLedger(
            DATABASE_URL, self.policy.tenant_reference
        ).load_financial_statement(
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "2026-08",
            "cash_flow",
        )
        by_role = {
            str(item["account_role_code"]): item for item in cash_flow["statement_lines"]
        }
        sheet_cash = sum(
            Decimal(str(item["debit_amount"])) - Decimal(str(item["credit_amount"]))
            for item in sheet["statement_lines"]
            if item["account_role_code"] == "cash_receipt"
        )
        period_ni = Decimal(str(by_role["period_net_income"]["credit_amount"])) - Decimal(
            str(by_role["period_net_income"]["debit_amount"])
        )
        working_capital = Decimal(
            str(by_role["operating_working_capital"]["credit_amount"])
        ) - Decimal(str(by_role["operating_working_capital"]["debit_amount"]))
        operations = Decimal(str(by_role["cash_from_operations"]["credit_amount"])) - Decimal(
            str(by_role["cash_from_operations"]["debit_amount"])
        )
        investing = Decimal(str(by_role["cash_from_investing"]["credit_amount"])) - Decimal(
            str(by_role["cash_from_investing"]["debit_amount"])
        )
        financing = Decimal(str(by_role["cash_from_financing"]["credit_amount"])) - Decimal(
            str(by_role["cash_from_financing"]["debit_amount"])
        )
        net_cash = Decimal(str(by_role["net_cash_change"]["credit_amount"])) - Decimal(
            str(by_role["net_cash_change"]["debit_amount"])
        )
        opening_cash = Decimal(str(by_role["opening_cash"]["credit_amount"])) - Decimal(
            str(by_role["opening_cash"]["debit_amount"])
        )
        closing_cash = Decimal(str(by_role["closing_cash"]["credit_amount"])) - Decimal(
            str(by_role["closing_cash"]["debit_amount"])
        )

        self.assertEqual(cash_status, 200)
        self.assertEqual(income_status, 200)
        self.assertEqual(sheet_status, 200)
        self.assertEqual(cash_flow_status, 200)
        self.assertEqual(cash_flow, library)
        self.assertEqual(cash_flow, persist)
        self.assertEqual(list(by_role), cash_flow_roles)
        self.assertEqual(period_ni, Decimal(str(income["net_income_amount"])))
        self.assertEqual(period_ni, Decimal("25000"))
        self.assertEqual(working_capital, Decimal("-7000"))
        self.assertEqual(operations, Decimal("18000"))
        self.assertEqual(investing, Decimal("0"))
        self.assertEqual(financing, Decimal("0"))
        self.assertEqual(net_cash, operations + investing + financing)
        self.assertEqual(opening_cash, Decimal("0"))
        self.assertEqual(closing_cash, opening_cash + net_cash)
        self.assertEqual(closing_cash, Decimal("18000"))
        self.assertEqual(closing_cash, sheet_cash)

        soft_status, _soft = self._http_json(
            "POST", "/period-closes", self._period_close_payload(period_status_code="soft_closed")
        )
        soft_cash_status, soft_cash = self._http_financial_statement("cash_flow")
        self.assertEqual(soft_status, 200)
        self.assertEqual(soft_cash_status, 200)
        self.assertEqual(soft_cash["statement_lines"], cash_flow["statement_lines"])
        self.assertEqual(soft_cash["net_income_amount"], cash_flow["net_income_amount"])

        hard_status, _hard = self._http_json("POST", "/period-closes", self._period_close_payload())
        closed_income_status, closed_income = self._http_financial_statement("income_statement")
        closed_sheet_status, closed_sheet = self._http_financial_statement("balance_sheet")
        closed_cash_status, closed_cash = self._http_financial_statement("cash_flow")
        closed_roles = {
            str(item["account_role_code"]): item
            for item in closed_cash["statement_lines"]
        }
        closed_sheet_cash = sum(
            Decimal(str(item["debit_amount"])) - Decimal(str(item["credit_amount"]))
            for item in closed_sheet["statement_lines"]
            if item["account_role_code"] == "cash_receipt"
        )
        closed_ni = Decimal(str(closed_roles["period_net_income"]["credit_amount"])) - Decimal(
            str(closed_roles["period_net_income"]["debit_amount"])
        )
        closed_wc = Decimal(
            str(closed_roles["operating_working_capital"]["credit_amount"])
        ) - Decimal(str(closed_roles["operating_working_capital"]["debit_amount"]))
        closed_financing = Decimal(
            str(closed_roles["cash_from_financing"]["credit_amount"])
        ) - Decimal(str(closed_roles["cash_from_financing"]["debit_amount"]))
        closed_closing = Decimal(str(closed_roles["closing_cash"]["credit_amount"])) - Decimal(
            str(closed_roles["closing_cash"]["debit_amount"])
        )
        self.assertEqual(hard_status, 200)
        self.assertEqual(closed_income_status, 200)
        self.assertEqual(closed_sheet_status, 200)
        self.assertEqual(closed_cash_status, 200)
        self.assertEqual(closed_ni, Decimal(str(income["net_income_amount"])))
        self.assertEqual(closed_ni, Decimal(str(closed_income["net_income_amount"])))
        self.assertEqual(closed_wc, Decimal("-7000"))
        self.assertEqual(closed_financing, Decimal("0"))
        self.assertEqual(closed_sheet["net_income_amount"], "0")
        self.assertEqual(closed_closing, closed_sheet_cash)
        self.assertEqual(
            closed_closing,
            Decimal(str(closed_roles["opening_cash"]["credit_amount"]))
            - Decimal(str(closed_roles["opening_cash"]["debit_amount"]))
            + Decimal(str(closed_roles["net_cash_change"]["credit_amount"]))
            - Decimal(str(closed_roles["net_cash_change"]["debit_amount"])),
        )

        ytd_status, ytd = self._http_financial_statement(
            "cash_flow",
            statement_scope_code="year_to_date",
        )
        ytd_income_status, ytd_income = self._http_financial_statement(
            "income_statement",
            statement_scope_code="year_to_date",
        )
        ytd_sheet_status, ytd_sheet = self._http_financial_statement(
            "balance_sheet",
            statement_scope_code="year_to_date",
        )
        ytd_roles = {str(item["account_role_code"]): item for item in ytd["statement_lines"]}
        ytd_sheet_cash = sum(
            Decimal(str(item["debit_amount"])) - Decimal(str(item["credit_amount"]))
            for item in ytd_sheet["statement_lines"]
            if item["account_role_code"] == "cash_receipt"
        )
        explicit_period_status, explicit_period = self._http_financial_statement(
            "cash_flow",
            statement_scope_code="period",
        )
        compare_status, compare = self._http_financial_statement(
            "cash_flow",
            comparison_fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-06",
        )
        june_only = lookup_financial_statement(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-06",
            "cash_flow",
        )
        compare_roles = {
            str(item["account_role_code"]): item
            for item in compare["comparison_statement_lines"]
        }

        self.assertEqual(ytd_status, 200)
        self.assertEqual(ytd_income_status, 200)
        self.assertEqual(ytd_sheet_status, 200)
        self.assertEqual(explicit_period_status, 200)
        self.assertEqual(compare_status, 200)
        self.assertEqual(ytd["statement_scope_code"], "year_to_date")
        self.assertNotIn("statement_scope_code", explicit_period)
        self.assertEqual(explicit_period["statement_lines"], closed_cash["statement_lines"])
        self.assertEqual(
            Decimal(str(ytd_roles["period_net_income"]["credit_amount"]))
            - Decimal(str(ytd_roles["period_net_income"]["debit_amount"])),
            Decimal(str(ytd_income["net_income_amount"])),
        )
        self.assertEqual(
            Decimal(str(ytd_roles["opening_cash"]["credit_amount"]))
            - Decimal(str(ytd_roles["opening_cash"]["debit_amount"])),
            Decimal("0"),
        )
        self.assertEqual(
            Decimal(str(ytd_roles["closing_cash"]["credit_amount"]))
            - Decimal(str(ytd_roles["closing_cash"]["debit_amount"])),
            ytd_sheet_cash,
        )
        self.assertEqual(
            compare["comparison_fiscal_period_reference"],
            "urn:cwl:accounting:fiscal_period:2026-06",
        )
        self.assertEqual(compare["comparison_statement_lines"], june_only["statement_lines"])
        self.assertEqual(
            Decimal(str(compare_roles["closing_cash"]["credit_amount"]))
            - Decimal(str(compare_roles["closing_cash"]["debit_amount"])),
            Decimal("0"),
        )
        self.assertNotIn("comparison_fiscal_period_reference", closed_cash)

        september_status, september = self._http_financial_statement(
            "cash_flow",
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-09",
        )
        september_sheet_status, september_sheet = self._http_financial_statement(
            "balance_sheet",
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-09",
        )
        september_roles = {
            str(item["account_role_code"]): item
            for item in september["statement_lines"]
        }
        september_sheet_cash = sum(
            Decimal(str(item["debit_amount"])) - Decimal(str(item["credit_amount"]))
            for item in september_sheet["statement_lines"]
            if item["account_role_code"] == "cash_receipt"
        )
        self.assertEqual(september_status, 200)
        self.assertEqual(september_sheet_status, 200)
        self.assertEqual(
            Decimal(str(september_roles["opening_cash"]["credit_amount"]))
            - Decimal(str(september_roles["opening_cash"]["debit_amount"])),
            Decimal("18000"),
        )
        self.assertEqual(
            Decimal(str(september_roles["period_net_income"]["credit_amount"]))
            - Decimal(str(september_roles["period_net_income"]["debit_amount"])),
            Decimal("0"),
        )
        self.assertEqual(
            Decimal(str(september_roles["operating_working_capital"]["credit_amount"]))
            - Decimal(str(september_roles["operating_working_capital"]["debit_amount"])),
            Decimal("0"),
        )
        self.assertEqual(
            Decimal(str(september_roles["cash_from_operations"]["credit_amount"]))
            - Decimal(str(september_roles["cash_from_operations"]["debit_amount"])),
            Decimal("0"),
        )
        self.assertEqual(
            Decimal(str(september_roles["closing_cash"]["credit_amount"]))
            - Decimal(str(september_roles["closing_cash"]["debit_amount"])),
            Decimal("18000"),
        )
        self.assertEqual(september_sheet_cash, Decimal("18000"))

        unknown_period = self._http_financial_statement(
            "cash_flow",
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01",
        )
        unknown_entity = self._http_financial_statement(
            "cash_flow",
            legal_entity_reference="urn:cwl:legal_entity:missing",
        )
        missing_header = self._http_financial_statement("cash_flow", tenant_header=None)
        cross_status, _cross = self._http_financial_statement(
            "cash_flow", tenant_header="urn:cwl:tenant_other"
        )
        with self.assertRaisesRegex(AccountingValidationError, "statement_type_code"):
            lookup_financial_statement(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
                "funds_flow",
            )

        self.assertEqual(unknown_period[0], 404)
        self.assertEqual(unknown_entity[0], 404)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(
            self._count_table("accounting_core.general_journal"),
            journals_before + 3,
        )
        server.shutdown()

    def test_http_reads_account_balances(self) -> None:
        """GET /account-balances returns as-of chart balances from live books or the close snapshot."""
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")

        empty_status, empty = self._http_account_balances()
        empty_library = lookup_account_balances(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        empty_cash_status, empty_cash = self._http_account_balances(
            chart_account_code="110200"
        )

        self.assertEqual(empty_status, 200)
        self.assertEqual(empty, empty_library)
        self.assertEqual(empty["tenant_reference"], self.policy.tenant_reference)
        self.assertEqual(empty["legal_entity_reference"], self.policy.legal_entity_reference)
        self.assertEqual(empty["accounting_book_reference"], self.policy.accounting_book_reference)
        self.assertEqual(empty["book_reference"], self.policy.accounting_book_reference)
        self.assertEqual(
            empty["fiscal_period_reference"],
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        self.assertEqual(empty["account_balances"], [])
        self.assertIsNone(empty["next_cursor"])
        self.assertEqual(empty_cash_status, 200)
        self.assertEqual(
            empty_cash["account_balances"],
            [
                {
                    "chart_account_code": "110200",
                    "account_class_code": "asset",
                    "debit_amount": "0",
                    "credit_amount": "0",
                }
            ],
        )
        self.assertIsNone(empty_cash["next_cursor"])

        invoice_status, _invoice = self._http_json(
            "POST", "/journal-proposals", self._billing_validated_payload()
        )
        cash_status, _cash = self._http_json(
            "POST", "/journal-proposals", self._billing_cash_payload()
        )
        sheet_status, sheet = self._http_financial_statement("balance_sheet")
        cash_flow_status, cash_flow = self._http_financial_statement("cash_flow")
        balances_status, balances = self._http_account_balances()
        library = lookup_account_balances(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        persist = PostgresPostingLedger(
            DATABASE_URL, self.policy.tenant_reference
        ).load_account_balances(
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "2026-08",
        )
        by_code = {
            str(item["chart_account_code"]): item for item in balances["account_balances"]
        }
        sheet_cash = sum(
            Decimal(str(item["debit_amount"])) - Decimal(str(item["credit_amount"]))
            for item in sheet["statement_lines"]
            if item["account_role_code"] == "cash_receipt"
        )
        cash_flow_closing = next(
            Decimal(str(item["credit_amount"])) - Decimal(str(item["debit_amount"]))
            for item in cash_flow["statement_lines"]
            if item["account_role_code"] == "closing_cash"
        )
        cash_only_status, cash_only = self._http_account_balances(
            chart_account_code="110200"
        )
        first_page_status, first_page = self._http_account_balances(page_limit=1)
        second_page_status, second_page = self._http_account_balances(
            page_limit=1,
            cursor=str(first_page["next_cursor"]),
        )
        third_page_status, third_page = self._http_account_balances(
            page_limit=1,
            cursor=str(second_page["next_cursor"]),
        )

        self.assertEqual(invoice_status, 200)
        self.assertEqual(cash_status, 200)
        self.assertEqual(sheet_status, 200)
        self.assertEqual(cash_flow_status, 200)
        self.assertEqual(balances_status, 200)
        self.assertEqual(balances, library)
        self.assertEqual(balances, persist)
        self.assertEqual(list(by_code), ["110100", "110200", "410100"])
        self.assertEqual(by_code["110100"]["account_class_code"], "asset")
        self.assertEqual(Decimal(str(by_code["110100"]["debit_amount"])), Decimal("25000"))
        self.assertEqual(Decimal(str(by_code["110100"]["credit_amount"])), Decimal("18000"))
        self.assertEqual(
            Decimal(str(by_code["110100"]["debit_amount"]))
            - Decimal(str(by_code["110100"]["credit_amount"])),
            Decimal("7000"),
        )
        self.assertEqual(by_code["110200"]["account_class_code"], "asset")
        self.assertEqual(Decimal(str(by_code["110200"]["debit_amount"])), Decimal("18000"))
        self.assertEqual(Decimal(str(by_code["110200"]["credit_amount"])), Decimal("0"))
        self.assertEqual(by_code["410100"]["account_class_code"], "revenue")
        self.assertEqual(Decimal(str(by_code["410100"]["debit_amount"])), Decimal("0"))
        self.assertEqual(Decimal(str(by_code["410100"]["credit_amount"])), Decimal("25000"))
        self.assertEqual(
            Decimal(str(by_code["110200"]["debit_amount"]))
            - Decimal(str(by_code["110200"]["credit_amount"])),
            sheet_cash,
        )
        self.assertEqual(
            Decimal(str(by_code["110200"]["debit_amount"]))
            - Decimal(str(by_code["110200"]["credit_amount"])),
            cash_flow_closing,
        )
        self.assertEqual(cash_only_status, 200)
        self.assertEqual(cash_only["account_balances"], [by_code["110200"]])
        self.assertIsNone(cash_only["next_cursor"])
        self.assertEqual(first_page_status, 200)
        self.assertEqual(second_page_status, 200)
        self.assertEqual(third_page_status, 200)
        self.assertEqual(
            [item["chart_account_code"] for item in first_page["account_balances"]],
            ["110100"],
        )
        self.assertEqual(first_page["next_cursor"], "110100")
        self.assertEqual(
            [item["chart_account_code"] for item in second_page["account_balances"]],
            ["110200"],
        )
        self.assertEqual(second_page["next_cursor"], "110200")
        self.assertEqual(
            [item["chart_account_code"] for item in third_page["account_balances"]],
            ["410100"],
        )
        self.assertIsNone(third_page["next_cursor"])

        soft_status, _soft = self._http_json(
            "POST", "/period-closes", self._period_close_payload(period_status_code="soft_closed")
        )
        adjusting_status, _adjusting = self._http_json(
            "POST", "/journals", self._adjusting_journal_payload()
        )
        soft_balances_status, soft_balances = self._http_account_balances()
        soft_by_code = {
            str(item["chart_account_code"]): item
            for item in soft_balances["account_balances"]
        }

        self.assertEqual(soft_status, 200)
        self.assertEqual(adjusting_status, 200)
        self.assertEqual(soft_balances_status, 200)
        self.assertEqual(Decimal(str(soft_by_code["110100"]["debit_amount"])), Decimal("26000"))
        self.assertEqual(Decimal(str(soft_by_code["110100"]["credit_amount"])), Decimal("18000"))
        self.assertEqual(Decimal(str(soft_by_code["110200"]["debit_amount"])), Decimal("18000"))
        self.assertEqual(Decimal(str(soft_by_code["410100"]["credit_amount"])), Decimal("26000"))

        hard_status, _hard = self._http_json("POST", "/period-closes", self._period_close_payload())
        closed_tb_status, closed_tb = self._http_trial_balance()
        closed_status, closed = self._http_account_balances()
        closed_by_code = {
            str(item["chart_account_code"]): item for item in closed["account_balances"]
        }
        retained_status, retained = self._http_account_balances(chart_account_code="310100")

        self.assertEqual(hard_status, 200)
        self.assertEqual(closed_tb_status, 200)
        self.assertEqual(closed_status, 200)
        self.assertEqual(retained_status, 200)
        self.assertIn("310100", closed_by_code)
        self.assertEqual(closed_by_code["310100"]["account_class_code"], "equity")
        self.assertEqual(
            Decimal(str(closed_by_code["310100"]["credit_amount"])),
            Decimal(str(self._trial_balance_line(closed_tb, "310100")["credit_amount"])),
        )
        self.assertEqual(
            Decimal(str(closed_by_code["410100"]["debit_amount"]))
            - Decimal(str(closed_by_code["410100"]["credit_amount"])),
            Decimal(str(self._trial_balance_line(closed_tb, "410100")["net_balance_amount"])),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(closed_tb, "410100")["net_balance_amount"])),
            Decimal("0"),
        )
        self.assertEqual(retained["account_balances"], [closed_by_code["310100"]])
        self.assertEqual(
            {
                item["chart_account_code"]: (
                    item["debit_amount"],
                    item["credit_amount"],
                )
                for item in closed["account_balances"]
            },
            {
                str(item["chart_account_code"]): (
                    item["debit_amount"],
                    item["credit_amount"],
                )
                for item in closed_tb["lines"]
            },
        )

        missing_query = self._http_json("GET", "/account-balances", None)
        missing_book_query = self._http_json(
            "GET",
            "/account-balances?"
            + urllib.parse.urlencode(
                {
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
                }
            ),
            None,
        )
        missing_period_query = self._http_json(
            "GET",
            "/account-balances?"
            + urllib.parse.urlencode(
                {
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "book_reference": self.policy.accounting_book_reference,
                }
            ),
            None,
        )
        post_status, _post = self._http_json("POST", "/account-balances", {})
        unknown_period = self._http_account_balances(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01"
        )
        unknown_entity = self._http_account_balances(
            legal_entity_reference="urn:cwl:legal_entity:missing"
        )
        unknown_book = self._http_account_balances(
            book_reference="urn:cwl:accounting_book:missing"
        )
        unknown_account = self._http_account_balances(chart_account_code="999999")
        bad_limit = self._http_account_balances(page_limit="abc")
        high_limit = self._http_account_balances(page_limit=101)
        missing_header = self._http_account_balances(tenant_header=None)
        cross_status, _cross = self._http_account_balances(
            tenant_header="urn:cwl:tenant_other"
        )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            lookup_account_balances(
                DATABASE_URL,
                self.policy.tenant_reference,
                "",
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "book_reference"):
            lookup_account_balances(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                "",
                "urn:cwl:accounting:fiscal_period:2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "fiscal_period_reference"):
            lookup_account_balances(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "",
            )
        with self.assertRaisesRegex(AccountingValidationError, "page_limit"):
            lookup_account_balances(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
                page_limit=0,
            )

        self.assertEqual(missing_query[0], 400)
        self.assertEqual(missing_book_query[0], 400)
        self.assertEqual(missing_period_query[0], 400)
        self.assertEqual(post_status, 405)
        self.assertEqual(unknown_period[0], 404)
        self.assertEqual(unknown_entity[0], 404)
        self.assertEqual(unknown_book[0], 404)
        self.assertEqual(unknown_account[0], 404)
        self.assertEqual(bad_limit[0], 400)
        self.assertEqual(high_limit[0], 400)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(
            self._count_table("accounting_core.general_journal"),
            journals_before + 4,
        )
        server.shutdown()

    def test_http_reads_account_rollforward(self) -> None:
        """GET /account-rollforwards ties opening + period sides to closing and account-balances."""
        self._seed_additional_period("2026-06", date(2026, 6, 1), date(2026, 6, 30))
        self._seed_additional_period("2026-09", date(2026, 9, 1), date(2026, 9, 30))
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")

        empty_status, empty = self._http_account_rollforward("110100")
        empty_library = lookup_account_rollforward(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
            "110100",
        )
        self.assertEqual(empty_status, 200)
        self.assertEqual(empty, empty_library)
        self.assertEqual(empty["chart_account_code"], "110100")
        self.assertEqual(empty["account_class_code"], "asset")
        self.assertNotIn("statement_scope_code", empty)
        for key in (
            "opening_debit_amount",
            "opening_credit_amount",
            "period_debit_amount",
            "period_credit_amount",
            "closing_debit_amount",
            "closing_credit_amount",
        ):
            self.assertEqual(Decimal(str(empty[key])), Decimal("0"))

        invoice_status, _invoice = self._http_json(
            "POST", "/journal-proposals", self._billing_validated_payload()
        )
        cash_status, _cash = self._http_json(
            "POST", "/journal-proposals", self._billing_cash_payload()
        )
        ar_status, ar_roll = self._http_account_rollforward("110100")
        cash_roll_status, cash_roll = self._http_account_rollforward("110200")
        ar_balance_status, ar_balance = self._http_account_balances(
            chart_account_code="110100"
        )
        cash_balance_status, cash_balance = self._http_account_balances(
            chart_account_code="110200"
        )
        cash_flow_status, cash_flow = self._http_financial_statement("cash_flow")
        library = lookup_account_rollforward(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
            "110100",
        )
        persist = PostgresPostingLedger(
            DATABASE_URL, self.policy.tenant_reference
        ).load_account_rollforward(
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "2026-08",
            "110100",
        )
        cash_flow_closing = next(
            Decimal(str(item["credit_amount"])) - Decimal(str(item["debit_amount"]))
            for item in cash_flow["statement_lines"]
            if item["account_role_code"] == "closing_cash"
        )

        self.assertEqual(invoice_status, 200)
        self.assertEqual(cash_status, 200)
        self.assertEqual(ar_status, 200)
        self.assertEqual(cash_roll_status, 200)
        self.assertEqual(ar_balance_status, 200)
        self.assertEqual(cash_balance_status, 200)
        self.assertEqual(cash_flow_status, 200)
        self.assertEqual(ar_roll, library)
        self.assertEqual(ar_roll, persist)
        self.assertEqual(Decimal(str(ar_roll["opening_debit_amount"])), Decimal("0"))
        self.assertEqual(Decimal(str(ar_roll["opening_credit_amount"])), Decimal("0"))
        self.assertEqual(Decimal(str(ar_roll["period_debit_amount"])), Decimal("25000"))
        self.assertEqual(Decimal(str(ar_roll["period_credit_amount"])), Decimal("18000"))
        self.assertEqual(Decimal(str(ar_roll["closing_debit_amount"])), Decimal("25000"))
        self.assertEqual(Decimal(str(ar_roll["closing_credit_amount"])), Decimal("18000"))
        self.assertEqual(
            ar_roll["closing_debit_amount"],
            ar_balance["account_balances"][0]["debit_amount"],
        )
        self.assertEqual(
            ar_roll["closing_credit_amount"],
            ar_balance["account_balances"][0]["credit_amount"],
        )
        self.assertEqual(Decimal(str(cash_roll["opening_debit_amount"])), Decimal("0"))
        self.assertEqual(Decimal(str(cash_roll["period_debit_amount"])), Decimal("18000"))
        self.assertEqual(Decimal(str(cash_roll["period_credit_amount"])), Decimal("0"))
        self.assertEqual(
            cash_roll["closing_debit_amount"],
            cash_balance["account_balances"][0]["debit_amount"],
        )
        self.assertEqual(
            Decimal(str(cash_roll["closing_debit_amount"]))
            - Decimal(str(cash_roll["closing_credit_amount"])),
            cash_flow_closing,
        )

        soft_status, _soft = self._http_json(
            "POST", "/period-closes", self._period_close_payload(period_status_code="soft_closed")
        )
        adjusting_status, _adjusting = self._http_json(
            "POST", "/journals", self._adjusting_journal_payload()
        )
        soft_ar_status, soft_ar = self._http_account_rollforward("110100")
        soft_balance_status, soft_balance = self._http_account_balances(
            chart_account_code="110100"
        )
        self.assertEqual(soft_status, 200)
        self.assertEqual(adjusting_status, 200)
        self.assertEqual(soft_ar_status, 200)
        self.assertEqual(soft_balance_status, 200)
        self.assertEqual(Decimal(str(soft_ar["period_debit_amount"])), Decimal("26000"))
        self.assertEqual(Decimal(str(soft_ar["period_credit_amount"])), Decimal("18000"))
        self.assertEqual(
            soft_ar["closing_debit_amount"],
            soft_balance["account_balances"][0]["debit_amount"],
        )
        self.assertEqual(
            soft_ar["closing_credit_amount"],
            soft_balance["account_balances"][0]["credit_amount"],
        )

        hard_status, _hard = self._http_json("POST", "/period-closes", self._period_close_payload())
        closed_re_status, closed_re = self._http_account_rollforward("310100")
        closed_re_balance_status, closed_re_balance = self._http_account_balances(
            chart_account_code="310100"
        )
        closed_tb_status, closed_tb = self._http_trial_balance()
        self.assertEqual(hard_status, 200)
        self.assertEqual(closed_re_status, 200)
        self.assertEqual(closed_re_balance_status, 200)
        self.assertEqual(closed_tb_status, 200)
        self.assertEqual(closed_re["account_class_code"], "equity")
        self.assertGreater(Decimal(str(closed_re["period_credit_amount"])), Decimal("0"))
        self.assertEqual(
            Decimal(str(closed_re["opening_credit_amount"]))
            + Decimal(str(closed_re["period_credit_amount"])),
            Decimal(str(closed_re["closing_credit_amount"])),
        )
        self.assertEqual(
            closed_re["closing_debit_amount"],
            closed_re_balance["account_balances"][0]["debit_amount"],
        )
        self.assertEqual(
            closed_re["closing_credit_amount"],
            closed_re_balance["account_balances"][0]["credit_amount"],
        )
        self.assertEqual(
            Decimal(str(closed_re["closing_credit_amount"])),
            Decimal(str(self._trial_balance_line(closed_tb, "310100")["credit_amount"])),
        )

        ytd_status, ytd = self._http_account_rollforward(
            "110100", statement_scope_code="year_to_date"
        )
        explicit_period_status, explicit_period = self._http_account_rollforward(
            "110100", statement_scope_code="period"
        )
        self.assertEqual(ytd_status, 200)
        self.assertEqual(explicit_period_status, 200)
        self.assertEqual(ytd["statement_scope_code"], "year_to_date")
        self.assertNotIn("statement_scope_code", explicit_period)
        self.assertEqual(explicit_period["period_debit_amount"], soft_ar["period_debit_amount"])
        self.assertEqual(explicit_period["period_credit_amount"], soft_ar["period_credit_amount"])

        september_status, september = self._http_account_rollforward(
            "110200",
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-09",
        )
        september_balance_status, september_balance = self._http_account_balances(
            chart_account_code="110200",
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-09",
        )
        self.assertEqual(september_status, 200)
        self.assertEqual(september_balance_status, 200)
        self.assertEqual(Decimal(str(september["opening_debit_amount"])), Decimal("18000"))
        self.assertEqual(Decimal(str(september["period_debit_amount"])), Decimal("0"))
        self.assertEqual(Decimal(str(september["closing_debit_amount"])), Decimal("18000"))
        self.assertEqual(
            september["closing_debit_amount"],
            september_balance["account_balances"][0]["debit_amount"],
        )
        september_tax_status, september_tax = self._http_account_rollforward(
            "210100",
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-09",
        )
        self.assertEqual(september_tax_status, 200)
        self.assertEqual(Decimal(str(september_tax["opening_debit_amount"])), Decimal("0"))
        self.assertEqual(Decimal(str(september_tax["opening_credit_amount"])), Decimal("0"))
        self.assertEqual(Decimal(str(september_tax["closing_debit_amount"])), Decimal("0"))

        missing_query = self._http_json("GET", "/account-rollforwards", None)
        missing_chart_query = self._http_json(
            "GET",
            "/account-rollforwards?"
            + urllib.parse.urlencode(
                {
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "book_reference": self.policy.accounting_book_reference,
                    "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
                }
            ),
            None,
        )
        missing_book_query = self._http_json(
            "GET",
            "/account-rollforwards?"
            + urllib.parse.urlencode(
                {
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
                    "chart_account_code": "110100",
                }
            ),
            None,
        )
        missing_period_query = self._http_json(
            "GET",
            "/account-rollforwards?"
            + urllib.parse.urlencode(
                {
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "book_reference": self.policy.accounting_book_reference,
                    "chart_account_code": "110100",
                }
            ),
            None,
        )
        post_status, _post = self._http_json("POST", "/account-rollforwards", {})
        unknown_account = self._http_account_rollforward("999999")
        unknown_period = self._http_account_rollforward(
            "110100",
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01",
        )
        unknown_entity = self._http_account_rollforward(
            "110100", legal_entity_reference="urn:cwl:legal_entity:missing"
        )
        unknown_book = self._http_account_rollforward(
            "110100", book_reference="urn:cwl:accounting_book:missing"
        )
        bad_scope = self._http_account_rollforward(
            "110100", statement_scope_code="life_to_date"
        )
        missing_header = self._http_account_rollforward("110100", tenant_header=None)
        cross_status, _cross = self._http_account_rollforward(
            "110100", tenant_header="urn:cwl:tenant_other"
        )
        with self.assertRaisesRegex(AccountingValidationError, "chart_account_code"):
            lookup_account_rollforward(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
                "",
            )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            lookup_account_rollforward(
                DATABASE_URL,
                self.policy.tenant_reference,
                "",
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
                "110100",
            )
        with self.assertRaisesRegex(AccountingValidationError, "book_reference"):
            lookup_account_rollforward(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                "",
                "urn:cwl:accounting:fiscal_period:2026-08",
                "110100",
            )
        with self.assertRaisesRegex(AccountingValidationError, "fiscal_period_reference"):
            lookup_account_rollforward(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "",
                "110100",
            )
        with self.assertRaisesRegex(AccountingValidationError, "statement_scope_code"):
            lookup_account_rollforward(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
                "110100",
                statement_scope_code="life_to_date",
            )
        with self.assertRaisesRegex(AccountingValidationError, "statement_scope_code"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_account_rollforward(
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "2026-08",
                "110100",
                statement_scope_code="life_to_date",
            )
        with self.assertRaisesRegex(AccountingValidationError, "chart_account_code"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_account_rollforward(
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "2026-08",
                "",
            )

        self.assertEqual(missing_query[0], 400)
        self.assertEqual(missing_chart_query[0], 400)
        self.assertEqual(missing_book_query[0], 400)
        self.assertEqual(missing_period_query[0], 400)
        self.assertEqual(post_status, 405)
        self.assertEqual(unknown_account[0], 404)
        self.assertEqual(unknown_period[0], 404)
        self.assertEqual(unknown_entity[0], 404)
        self.assertEqual(unknown_book[0], 404)
        self.assertEqual(bad_scope[0], 400)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(
            self._count_table("accounting_core.general_journal"),
            journals_before + 4,
        )
        server.shutdown()

    def test_http_reads_trial_balance_basis(self) -> None:
        """GET /trial-balances optional balance_basis_code is the unadjusted / adjusted / post-close worksheet."""
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")

        empty_status, empty = self._http_trial_balance()
        empty_unadjusted_status, empty_unadjusted = self._http_trial_balance(
            balance_basis_code="unadjusted"
        )
        empty_adjusted_status, empty_adjusted = self._http_trial_balance(
            balance_basis_code="adjusted"
        )
        empty_post_close_status, empty_post_close = self._http_trial_balance(
            balance_basis_code="post_close"
        )
        with self.assertRaisesRegex(
            AccountingValidationError,
            "post_close requires a stored trial_balance_snapshot",
        ):
            self.ledger.load_period_trial_balance(
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "2026-08",
                balance_basis_code="post_close",
            )
        invalid_status, invalid_document = self._http_trial_balance(
            balance_basis_code="working"
        )
        with self.assertRaisesRegex(
            AccountingValidationError,
            "must be unadjusted, adjusted, or post_close",
        ):
            lookup_trial_balance(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
                balance_basis_code="working",
            )
        with self.assertRaisesRegex(
            AccountingValidationError,
            "must be unadjusted, adjusted, or post_close",
        ):
            self.ledger.load_period_trial_balance(
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "2026-08",
                balance_basis_code="working",
            )

        self.assertEqual(empty_status, 200)
        self.assertEqual(empty["lines"], [])
        self.assertEqual(empty["balance_source_code"], "live")
        self.assertNotIn("balance_basis_code", empty)
        self.assertEqual(empty_unadjusted_status, 200)
        self.assertEqual(empty_unadjusted["lines"], [])
        self.assertEqual(empty_unadjusted["balance_basis_code"], "unadjusted")
        self.assertEqual(empty_adjusted_status, 200)
        self.assertEqual(empty_adjusted["lines"], [])
        self.assertEqual(empty_adjusted["balance_basis_code"], "adjusted")
        self.assertEqual(empty_post_close_status, 409)
        self.assertIn(
            "post_close requires a stored trial_balance_snapshot",
            str(empty_post_close["error_message"]),
        )
        self.assertEqual(invalid_status, 400)
        self.assertIn(
            "must be unadjusted, adjusted, or post_close",
            str(invalid_document["error_message"]),
        )

        invoice_status, _invoice = self._http_json(
            "POST", "/journal-proposals", self._billing_validated_payload()
        )
        open_omit_status, open_omit = self._http_trial_balance()
        open_unadjusted_status, open_unadjusted = self._http_trial_balance(
            balance_basis_code="unadjusted"
        )
        open_adjusted_status, open_adjusted = self._http_trial_balance(
            balance_basis_code="adjusted"
        )

        self.assertEqual(invoice_status, 200)
        self.assertEqual(open_omit_status, 200)
        self.assertEqual(open_omit["period_status_code"], "open")
        self.assertEqual(open_omit["balance_source_code"], "live")
        self.assertNotIn("balance_basis_code", open_omit)
        self.assertEqual(
            Decimal(str(self._trial_balance_line(open_omit, "110100")["debit_amount"])),
            Decimal("25000"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(open_omit, "410100")["credit_amount"])),
            Decimal("25000"),
        )
        self.assertEqual(open_unadjusted_status, 200)
        self.assertEqual(open_unadjusted["balance_basis_code"], "unadjusted")
        self.assertEqual(open_unadjusted["balance_source_code"], "live")
        self.assertEqual(open_unadjusted["lines"], open_omit["lines"])
        self.assertEqual(open_adjusted_status, 200)
        self.assertEqual(open_adjusted["balance_basis_code"], "adjusted")
        self.assertEqual(open_adjusted["lines"], open_omit["lines"])

        cash_status, _cash = self._http_json(
            "POST", "/journal-proposals", self._billing_cash_payload()
        )
        soft_status, _soft = self._http_json(
            "POST",
            "/period-closes",
            self._period_close_payload(period_status_code="soft_closed"),
        )
        adjusting_status, _adjusting = self._http_json(
            "POST", "/journals", self._adjusting_journal_payload()
        )
        soft_omit_status, soft_omit = self._http_trial_balance()
        soft_adjusted_status, soft_adjusted = self._http_trial_balance(
            balance_basis_code="adjusted"
        )
        soft_unadjusted_status, soft_unadjusted = self._http_trial_balance(
            balance_basis_code="unadjusted"
        )
        soft_post_close_status, soft_post_close = self._http_trial_balance(
            balance_basis_code="post_close"
        )

        self.assertEqual(cash_status, 200)
        self.assertEqual(soft_status, 200)
        self.assertEqual(adjusting_status, 200)
        self.assertEqual(soft_omit_status, 200)
        self.assertEqual(soft_omit["period_status_code"], "soft_closed")
        self.assertEqual(soft_omit["balance_source_code"], "live")
        self.assertNotIn("balance_basis_code", soft_omit)
        self.assertEqual(
            Decimal(str(self._trial_balance_line(soft_omit, "110100")["debit_amount"])),
            Decimal("26000"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(soft_omit, "110100")["credit_amount"])),
            Decimal("18000"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(soft_omit, "110200")["debit_amount"])),
            Decimal("18000"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(soft_omit, "410100")["credit_amount"])),
            Decimal("26000"),
        )
        self.assertNotIn(
            "310100",
            {str(item["chart_account_code"]) for item in soft_omit["lines"]},
        )
        self.assertEqual(soft_adjusted_status, 200)
        self.assertEqual(soft_adjusted["balance_basis_code"], "adjusted")
        self.assertEqual(soft_adjusted["lines"], soft_omit["lines"])
        self.assertEqual(soft_unadjusted_status, 200)
        self.assertEqual(soft_unadjusted["balance_basis_code"], "unadjusted")
        self.assertEqual(soft_unadjusted["balance_source_code"], "live")
        self.assertEqual(
            Decimal(str(self._trial_balance_line(soft_unadjusted, "110100")["debit_amount"])),
            Decimal("25000"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(soft_unadjusted, "110100")["credit_amount"])),
            Decimal("18000"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(soft_unadjusted, "110200")["debit_amount"])),
            Decimal("18000"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(soft_unadjusted, "410100")["credit_amount"])),
            Decimal("25000"),
        )
        self.assertNotIn(
            "310100",
            {str(item["chart_account_code"]) for item in soft_unadjusted["lines"]},
        )
        self.assertEqual(soft_post_close_status, 409)
        self.assertIn(
            "post_close requires a stored trial_balance_snapshot",
            str(soft_post_close["error_message"]),
        )

        hard_status, _hard = self._http_json(
            "POST", "/period-closes", self._period_close_payload()
        )
        hard_omit_status, hard_omit = self._http_trial_balance()
        hard_post_close_status, hard_post_close = self._http_trial_balance(
            balance_basis_code="post_close"
        )
        hard_adjusted_status, hard_adjusted = self._http_trial_balance(
            balance_basis_code="adjusted"
        )
        hard_unadjusted_status, hard_unadjusted = self._http_trial_balance(
            balance_basis_code="unadjusted"
        )
        persist_adjusted = self.ledger.load_period_trial_balance(
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "2026-08",
            balance_basis_code="adjusted",
        )
        lookup_adjusted = lookup_trial_balance(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
            balance_basis_code="adjusted",
        )
        persist_omit = self.ledger.load_period_trial_balance(
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "2026-08",
        )
        lookup_omit = lookup_trial_balance(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
        )

        self.assertEqual(hard_status, 200)
        self.assertEqual(hard_omit_status, 200)
        self.assertEqual(hard_omit["period_status_code"], "hard_closed")
        self.assertEqual(hard_omit["balance_source_code"], "snapshot")
        self.assertNotIn("balance_basis_code", hard_omit)
        self.assertIn("snapshot_record_id", hard_omit)
        self.assertEqual(
            Decimal(str(self._trial_balance_line(hard_omit, "310100")["credit_amount"])),
            Decimal("26000"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(hard_omit, "410100")["debit_amount"])),
            Decimal("26000"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(hard_omit, "410100")["credit_amount"])),
            Decimal("26000"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(hard_omit, "110100")["debit_amount"])),
            Decimal("26000"),
        )
        self.assertEqual(hard_post_close_status, 200)
        self.assertEqual(hard_post_close["balance_basis_code"], "post_close")
        self.assertEqual(hard_post_close["balance_source_code"], "snapshot")
        self.assertEqual(hard_post_close["snapshot_record_id"], hard_omit["snapshot_record_id"])
        self.assertEqual(hard_post_close["lines"], hard_omit["lines"])
        self.assertEqual(hard_adjusted_status, 200)
        self.assertEqual(hard_adjusted["balance_basis_code"], "adjusted")
        self.assertEqual(hard_adjusted["balance_source_code"], "live")
        self.assertNotIn("snapshot_record_id", hard_adjusted)
        self.assertEqual(
            Decimal(str(self._trial_balance_line(hard_adjusted, "410100")["credit_amount"])),
            Decimal("26000"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(hard_adjusted, "110100")["debit_amount"])),
            Decimal("26000"),
        )
        self.assertNotIn(
            "310100",
            {str(item["chart_account_code"]) for item in hard_adjusted["lines"]},
        )
        self.assertEqual(hard_unadjusted_status, 200)
        self.assertEqual(hard_unadjusted["balance_basis_code"], "unadjusted")
        self.assertEqual(hard_unadjusted["balance_source_code"], "live")
        self.assertNotIn("snapshot_record_id", hard_unadjusted)
        self.assertEqual(
            Decimal(str(self._trial_balance_line(hard_unadjusted, "410100")["credit_amount"])),
            Decimal("25000"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(hard_unadjusted, "110100")["debit_amount"])),
            Decimal("25000"),
        )
        self.assertNotIn(
            "310100",
            {str(item["chart_account_code"]) for item in hard_unadjusted["lines"]},
        )
        self.assertEqual(persist_adjusted, hard_adjusted)
        self.assertEqual(lookup_adjusted, hard_adjusted)
        self.assertEqual(persist_omit, hard_omit)
        self.assertEqual(lookup_omit, hard_omit)
        self.assertEqual(
            self._count_table("accounting_core.general_journal"),
            journals_before + 4,
        )
        server.shutdown()

    def test_http_reads_financial_statement_package(self) -> None:
        """GET /financial-statement-packages returns the four IAS 1 statements for one close pack."""
        self._seed_additional_period("2026-07", date(2026, 7, 1), date(2026, 7, 31))
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")

        empty_status, empty = self._http_financial_statement_package()
        empty_library = lookup_financial_statement_package(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        empty_singles = {
            statement_type_code: self._http_financial_statement(statement_type_code)[1]
            for statement_type_code in (
                "income_statement",
                "balance_sheet",
                "changes_in_equity",
                "cash_flow",
            )
        }
        self.assertEqual(empty_status, 200)
        self.assertEqual(empty, empty_library)
        self.assertNotIn("statement_scope_code", empty)
        self.assertEqual(empty["income_statement"], empty_singles["income_statement"])
        self.assertEqual(empty["balance_sheet"], empty_singles["balance_sheet"])
        self.assertEqual(empty["changes_in_equity"], empty_singles["changes_in_equity"])
        self.assertEqual(empty["cash_flow"], empty_singles["cash_flow"])
        self._assert_financial_statement_package_tie_outs(empty)

        july = self._billing_validated_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:invoice_draft:july:"
                f"sha256:{'4' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "4" * 64,
            transaction_date="2026-07-15",
            accounting_date="2026-07-15",
            proposed_at="2026-07-15T00:00:00Z",
            source_event_references=(
                f"{self.policy.tenant_reference}:invoice_draft:july",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "10000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "0",
                    "credit_amount": "10000",
                },
            ],
        )
        july_status, _july = self._http_json("POST", "/journal-proposals", july)
        july_close_status, _july_close = self._http_json(
            "POST",
            "/period-closes",
            self._period_close_payload(
                fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07"
            ),
        )
        invoice_status, _invoice = self._http_json(
            "POST", "/journal-proposals", self._billing_validated_payload()
        )
        cash_status, _cash = self._http_json(
            "POST", "/journal-proposals", self._billing_cash_payload()
        )
        open_status, opened = self._http_financial_statement_package()
        open_library = lookup_financial_statement_package(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        open_singles = {
            statement_type_code: self._http_financial_statement(statement_type_code)[1]
            for statement_type_code in (
                "income_statement",
                "balance_sheet",
                "changes_in_equity",
                "cash_flow",
            )
        }
        self.assertEqual(july_status, 200)
        self.assertEqual(july_close_status, 200)
        self.assertEqual(invoice_status, 200)
        self.assertEqual(cash_status, 200)
        self.assertEqual(open_status, 200)
        self.assertEqual(opened, open_library)
        self.assertEqual(opened["tenant_reference"], self.policy.tenant_reference)
        self.assertEqual(opened["legal_entity_reference"], self.policy.legal_entity_reference)
        self.assertEqual(opened["accounting_book_reference"], self.policy.accounting_book_reference)
        self.assertEqual(opened["book_reference"], self.policy.accounting_book_reference)
        self.assertEqual(
            opened["fiscal_period_reference"],
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        self.assertNotIn("statement_scope_code", opened)
        self.assertEqual(opened["income_statement"], open_singles["income_statement"])
        self.assertEqual(opened["balance_sheet"], open_singles["balance_sheet"])
        self.assertEqual(opened["changes_in_equity"], open_singles["changes_in_equity"])
        self.assertEqual(opened["cash_flow"], open_singles["cash_flow"])
        self._assert_financial_statement_package_tie_outs(opened)

        hard_status, _hard = self._http_json(
            "POST", "/period-closes", self._period_close_payload()
        )
        closed_status, closed = self._http_financial_statement_package()
        closed_singles = {
            statement_type_code: self._http_financial_statement(statement_type_code)[1]
            for statement_type_code in (
                "income_statement",
                "balance_sheet",
                "changes_in_equity",
                "cash_flow",
            )
        }
        self.assertEqual(hard_status, 200)
        self.assertEqual(closed_status, 200)
        self.assertEqual(closed["income_statement"], closed_singles["income_statement"])
        self.assertEqual(closed["balance_sheet"], closed_singles["balance_sheet"])
        self.assertEqual(closed["changes_in_equity"], closed_singles["changes_in_equity"])
        self.assertEqual(closed["cash_flow"], closed_singles["cash_flow"])
        self.assertEqual(
            Decimal(str(closed["income_statement"]["net_income_amount"])),
            Decimal("25000"),
        )
        self.assertEqual(
            Decimal(str(closed["balance_sheet"]["net_income_amount"])),
            Decimal("0"),
        )
        self.assertEqual(
            Decimal(
                str(
                    next(
                        item
                        for item in closed["balance_sheet"]["statement_lines"]
                        if item["chart_account_code"] == "310100"
                    )["credit_amount"]
                )
            ),
            Decimal("35000"),
        )
        self._assert_financial_statement_package_tie_outs(closed)

        explicit_period_status, explicit_period = self._http_financial_statement_package(
            statement_scope_code="period"
        )
        ytd_status, ytd = self._http_financial_statement_package(
            statement_scope_code="year_to_date"
        )
        ytd_library = lookup_financial_statement_package(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
            statement_scope_code="year_to_date",
        )
        ytd_singles = {
            statement_type_code: self._http_financial_statement(
                statement_type_code, statement_scope_code="year_to_date"
            )[1]
            for statement_type_code in (
                "income_statement",
                "balance_sheet",
                "changes_in_equity",
                "cash_flow",
            )
        }
        compare_status, compared = self._http_financial_statement_package(
            comparison_fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07",
        )
        compare_singles = {
            statement_type_code: self._http_financial_statement(
                statement_type_code,
                comparison_fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07",
            )[1]
            for statement_type_code in (
                "income_statement",
                "balance_sheet",
                "changes_in_equity",
                "cash_flow",
            )
        }
        self.assertEqual(explicit_period_status, 200)
        self.assertNotIn("statement_scope_code", explicit_period)
        self.assertEqual(explicit_period["income_statement"], closed["income_statement"])
        self.assertEqual(ytd_status, 200)
        self.assertEqual(ytd, ytd_library)
        self.assertEqual(ytd["statement_scope_code"], "year_to_date")
        self.assertEqual(ytd["income_statement"], ytd_singles["income_statement"])
        self.assertEqual(ytd["balance_sheet"], ytd_singles["balance_sheet"])
        self.assertEqual(ytd["changes_in_equity"], ytd_singles["changes_in_equity"])
        self.assertEqual(ytd["cash_flow"], ytd_singles["cash_flow"])
        self._assert_financial_statement_package_tie_outs(ytd)
        self.assertEqual(compare_status, 200)
        self.assertNotIn("statement_scope_code", compared)
        self.assertEqual(compared["income_statement"], compare_singles["income_statement"])
        self.assertEqual(compared["balance_sheet"], compare_singles["balance_sheet"])
        self.assertEqual(compared["changes_in_equity"], compare_singles["changes_in_equity"])
        self.assertEqual(compared["cash_flow"], compare_singles["cash_flow"])
        self.assertIn("comparison_statement_lines", compared["income_statement"])
        self.assertIn("comparison_net_income_amount", compared["cash_flow"])

        missing_query = self._http_json("GET", "/financial-statement-packages", None)
        missing_book = self._http_json(
            "GET",
            "/financial-statement-packages?"
            + urllib.parse.urlencode(
                {
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
                }
            ),
            None,
        )
        post_status, _post = self._http_json("POST", "/financial-statement-packages", {})
        bad_scope = self._http_financial_statement_package(
            statement_scope_code="life_to_date"
        )
        unknown_period = self._http_financial_statement_package(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01"
        )
        unknown_entity = self._http_financial_statement_package(
            legal_entity_reference="urn:cwl:legal_entity:missing"
        )
        missing_header = self._http_financial_statement_package(tenant_header=None)
        cross_status, _cross = self._http_financial_statement_package(
            tenant_header="urn:cwl:tenant_other"
        )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            lookup_financial_statement_package(
                DATABASE_URL,
                self.policy.tenant_reference,
                "",
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "statement_scope_code"):
            lookup_financial_statement_package(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
                statement_scope_code="life_to_date",
            )

        self.assertEqual(missing_query[0], 400)
        self.assertEqual(missing_book[0], 400)
        self.assertEqual(post_status, 405)
        self.assertEqual(bad_scope[0], 400)
        self.assertEqual(unknown_period[0], 404)
        self.assertEqual(unknown_entity[0], 404)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(
            self._count_table("accounting_core.general_journal"),
            journals_before + 5,
        )
        server.shutdown()

    def test_http_reads_posted_journal_lines(self) -> None:
        """GET returns persisted journal lines and keeps original vs reversing journals distinct."""
        invoice = self._billing_validated_payload()
        posted = accept_journal_proposal(invoice, DATABASE_URL, self.policy.tenant_reference)
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")

        status, document = self._http_journal(idempotency_key=str(invoice["idempotency_key"]))
        by_reference_status, by_reference = self._http_journal(
            journal_reference=str(posted["journal_reference"])
        )
        both_status, both = self._http_journal(
            idempotency_key=str(invoice["idempotency_key"]),
            journal_reference=str(posted["journal_reference"]),
        )
        library = lookup_posted_journal(
            DATABASE_URL,
            self.policy.tenant_reference,
            idempotency_key=str(invoice["idempotency_key"]),
        )
        by_code = {
            str(item["chart_account_code"]): item for item in document["lines"]
        }

        self.assertEqual(status, 200)
        self.assertEqual(by_reference_status, 200)
        self.assertEqual(both_status, 200)
        self.assertEqual(document, library)
        self.assertEqual(document, by_reference)
        self.assertEqual(document, both)
        self.assertEqual(document["tenant_reference"], self.policy.tenant_reference)
        self.assertEqual(document["journal_reference"], posted["journal_reference"])
        self.assertEqual(document["idempotency_key"], invoice["idempotency_key"])
        self.assertEqual(document["journal_status_code"], "posted")
        self.assertIsNone(document["reversal_of_journal_reference"])
        self.assertEqual(set(by_code), {"110100", "410100"})
        self.assertEqual(by_code["110100"]["line_number"], 1)
        self.assertEqual(by_code["110100"]["account_role_code"], "accounts_receivable")
        self.assertEqual(Decimal(str(by_code["110100"]["debit_amount"])), Decimal("25000"))
        self.assertEqual(Decimal(str(by_code["110100"]["credit_amount"])), Decimal("0"))
        self.assertEqual(by_code["410100"]["line_number"], 2)
        self.assertEqual(by_code["410100"]["account_role_code"], "usage_revenue")
        self.assertEqual(Decimal(str(by_code["410100"]["debit_amount"])), Decimal("0"))
        self.assertEqual(Decimal(str(by_code["410100"]["credit_amount"])), Decimal("25000"))

        reverse_status, reversing_receipt = self._http_json(
            "POST",
            "/journal-reversals",
            {
                "tenant_reference": self.policy.tenant_reference,
                "journal_reference": posted["journal_reference"],
                "reversal_date": "2026-08-31",
                "reversal_reason_code": "billing_correction",
            },
        )
        original_after_status, original_after = self._http_journal(
            idempotency_key=str(invoice["idempotency_key"])
        )
        reversing_key = f"reversal:{posted['journal_reference']}"
        reversing_status, reversing = self._http_journal(idempotency_key=reversing_key)
        reversing_ref_status, reversing_by_ref = self._http_journal(
            journal_reference=str(reversing_receipt["journal_reference"])
        )
        reversing_lines = {
            str(item["chart_account_code"]): item for item in reversing["lines"]
        }

        self.assertEqual(reverse_status, 200)
        self.assertEqual(original_after_status, 200)
        self.assertEqual(original_after, document)
        self.assertEqual(reversing_status, 200)
        self.assertEqual(reversing_ref_status, 200)
        self.assertEqual(reversing, reversing_by_ref)
        self.assertEqual(reversing["journal_reference"], reversing_receipt["journal_reference"])
        self.assertEqual(reversing["idempotency_key"], reversing_key)
        self.assertEqual(reversing["reversal_of_journal_reference"], posted["journal_reference"])
        self.assertEqual(reversing["reversal_reason_code"], "billing_correction")
        self.assertEqual(Decimal(str(reversing_lines["110100"]["debit_amount"])), Decimal("0"))
        self.assertEqual(Decimal(str(reversing_lines["110100"]["credit_amount"])), Decimal("25000"))
        self.assertEqual(Decimal(str(reversing_lines["410100"]["debit_amount"])), Decimal("25000"))
        self.assertEqual(Decimal(str(reversing_lines["410100"]["credit_amount"])), Decimal("0"))

        post_status, _post_body = self._http_json("POST", "/journals", {})
        missing_header = self._http_journal(
            idempotency_key=str(invoice["idempotency_key"]), tenant_header=None
        )
        cross_status, _cross = self._http_journal(
            idempotency_key=str(invoice["idempotency_key"]),
            tenant_header="urn:cwl:tenant_other",
        )
        missing_query = self._http_json("GET", "/journals", None)
        unknown_key = self._http_journal(
            idempotency_key=f"{self.policy.tenant_reference}:invoice_draft:missing:sha256:{'b' * 64}:v1"
        )
        unknown_reference = self._http_journal(
            journal_reference="urn:cwl:accounting:general_journal:missing"
        )
        with self.assertRaisesRegex(AccountingValidationError, "idempotency_key"):
            lookup_posted_journal(DATABASE_URL, self.policy.tenant_reference)
        with self.assertRaisesRegex(AccountingValidationError, "idempotency_key"):
            lookup_posted_journal(
                DATABASE_URL,
                self.policy.tenant_reference,
                journal_reference=str(posted["journal_reference"]),
                idempotency_key=reversing_key,
            )
        with self.assertRaisesRegex(AccountingValidationError, "posted journal"):
            lookup_posted_journal(
                DATABASE_URL,
                self.policy.tenant_reference,
                journal_reference="urn:cwl:accounting:general_journal:missing",
            )
        with self.assertRaisesRegex(AccountingValidationError, "idempotency_key"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_posted_journal()

        self.assertEqual(post_status, 403)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(missing_query[0], 400)
        self.assertEqual(unknown_key[0], 404)
        self.assertEqual(unknown_reference[0], 404)
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before + 1)
        server.shutdown()

    def test_http_lists_period_journals_from_existing_rows(self) -> None:
        """GET lists posted and reversing journals for one period without inventing rows."""
        invoice = self._billing_validated_payload()
        cash = self._billing_cash_payload()
        taxed_credit = self._billing_taxed_credit_payload()
        september = self._september_invoice_payload()
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")

        empty_status, empty_page = self._http_period_journals()
        alias_status, alias_page = self._http_period_journals(use_book_alias=True)
        library_empty = lookup_period_journals(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
        )

        self.assertEqual(empty_status, 200)
        self.assertEqual(alias_status, 200)
        self.assertEqual(empty_page, library_empty)
        self.assertEqual(alias_page["journals"], [])
        self.assertEqual(empty_page["journals"], [])
        self.assertIsNone(empty_page["next_cursor"])
        self.assertEqual(empty_page["tenant_reference"], self.policy.tenant_reference)
        self.assertEqual(empty_page["legal_entity_reference"], self.policy.legal_entity_reference)
        self.assertEqual(empty_page["accounting_book_reference"], self.policy.accounting_book_reference)
        self.assertEqual(empty_page["book_reference"], self.policy.accounting_book_reference)
        self.assertEqual(
            empty_page["fiscal_period_reference"],
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        self.assertEqual(empty_page["period_code"], "2026-08")

        invoice_status, invoice_receipt = self._http_json("POST", "/journal-proposals", invoice)
        cash_status, _cash_receipt = self._http_json("POST", "/journal-proposals", cash)
        credit_status, _credit_receipt = self._http_json("POST", "/journal-proposals", taxed_credit)
        accept_period_open(self._period_open_payload(), DATABASE_URL, self.policy.tenant_reference)
        september_status, september_receipt = self._http_json("POST", "/journal-proposals", september)
        listed_status, listed = self._http_period_journals()
        september_list_status, september_list = self._http_period_journals(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-09"
        )
        first_page_status, first_page = self._http_period_journals(page_limit=2)
        second_page_status, second_page = self._http_period_journals(
            page_limit=2,
            cursor=str(first_page["next_cursor"]),
        )
        single_status, single = self._http_journal(idempotency_key=str(invoice["idempotency_key"]))
        reverse_status, reversing_receipt = self._http_json(
            "POST",
            "/journal-reversals",
            {
                "tenant_reference": self.policy.tenant_reference,
                "journal_reference": invoice_receipt["journal_reference"],
                "reversal_date": "2026-08-31",
                "reversal_reason_code": "billing_correction",
            },
        )
        after_reverse_status, after_reverse = self._http_period_journals()
        by_key = {str(item["idempotency_key"]): item for item in listed["journals"]}
        after_keys = {str(item["idempotency_key"]) for item in after_reverse["journals"]}

        self.assertEqual(invoice_status, 200)
        self.assertEqual(cash_status, 200)
        self.assertEqual(credit_status, 200)
        self.assertEqual(september_status, 200)
        self.assertEqual(listed_status, 200)
        self.assertEqual(september_list_status, 200)
        self.assertEqual(first_page_status, 200)
        self.assertEqual(second_page_status, 200)
        self.assertEqual(single_status, 200)
        self.assertEqual(reverse_status, 200)
        self.assertEqual(after_reverse_status, 200)
        self.assertEqual(
            {str(item["idempotency_key"]) for item in listed["journals"]},
            {
                str(invoice["idempotency_key"]),
                str(cash["idempotency_key"]),
                str(taxed_credit["idempotency_key"]),
            },
        )
        self.assertNotIn(str(september["idempotency_key"]), by_key)
        self.assertEqual(listed["next_cursor"], None)
        self.assertEqual(by_key[str(invoice["idempotency_key"])]["journal_status_code"], "posted")
        self.assertEqual(by_key[str(invoice["idempotency_key"])]["accounting_date"], "2026-08-31")
        self.assertEqual(by_key[str(invoice["idempotency_key"])]["line_count"], 2)
        self.assertEqual(by_key[str(cash["idempotency_key"])]["line_count"], 2)
        self.assertEqual(by_key[str(taxed_credit["idempotency_key"])]["line_count"], 3)
        self.assertEqual(
            by_key[str(invoice["idempotency_key"])]["journal_reference"],
            invoice_receipt["journal_reference"],
        )
        self.assertEqual(
            [str(item["idempotency_key"]) for item in september_list["journals"]],
            [str(september["idempotency_key"])],
        )
        self.assertEqual(
            september_list["journals"][0]["journal_reference"],
            september_receipt["journal_reference"],
        )
        self.assertEqual(len(first_page["journals"]), 2)
        self.assertTrue(first_page["next_cursor"])
        self.assertEqual(len(second_page["journals"]), 1)
        self.assertIsNone(second_page["next_cursor"])
        self.assertEqual(
            [item["journal_reference"] for item in first_page["journals"]]
            + [item["journal_reference"] for item in second_page["journals"]],
            [item["journal_reference"] for item in listed["journals"]],
        )
        self.assertIn("lines", single)
        self.assertEqual(len(single["lines"]), 2)
        self.assertEqual(single["idempotency_key"], invoice["idempotency_key"])
        self.assertIn(str(invoice["idempotency_key"]), after_keys)
        self.assertIn(f"reversal:{invoice_receipt['journal_reference']}", after_keys)
        reversing_item = next(
            item
            for item in after_reverse["journals"]
            if item["idempotency_key"] == f"reversal:{invoice_receipt['journal_reference']}"
        )
        self.assertEqual(
            reversing_item["journal_reference"],
            reversing_receipt["journal_reference"],
        )
        self.assertEqual(
            reversing_item["reversal_of_journal_reference"],
            invoice_receipt["journal_reference"],
        )
        self.assertEqual(reversing_item["line_count"], 2)
        self.assertEqual(len(after_reverse["journals"]), 4)

        missing_scope = self._http_json(
            "GET",
            f"/journals?legal_entity_reference={urllib.parse.quote(self.policy.legal_entity_reference)}",
            None,
        )
        unknown_entity = self._http_period_journals(
            legal_entity_reference="urn:cwl:legal_entity:missing"
        )
        unknown_book = self._http_period_journals(book_reference="urn:cwl:accounting_book:missing")
        unknown_period = self._http_period_journals(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-10"
        )
        bad_limit = self._http_period_journals(page_limit="abc")
        high_limit = self._http_period_journals(page_limit=101)
        bad_cursor = self._http_period_journals(cursor="not-a-cursor")
        cross_status, _cross = self._http_period_journals(tenant_header="urn:cwl:tenant_other")
        with self.assertRaisesRegex(AccountingValidationError, "are required"):
            lookup_period_journals(DATABASE_URL, self.policy.tenant_reference, "", "", "")
        with self.assertRaisesRegex(AccountingValidationError, "page_limit"):
            lookup_period_journals(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "2026-08",
                page_limit=0,
            )
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            lookup_period_journals(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "2026-08",
                cursor="2026-08-31",
            )
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            lookup_period_journals(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "2026-08",
                cursor="|urn:cwl:accounting:general_journal:x",
            )
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            lookup_period_journals(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "2026-08",
                cursor="2026-08-31|",
            )
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            lookup_period_journals(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "2026-08",
                cursor="not-a-date|urn:cwl:accounting:general_journal:x",
            )
        with self.assertRaisesRegex(AccountingValidationError, "are required"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_period_journals("", "", "")
        with self.assertRaisesRegex(AccountingValidationError, "fiscal_period"):
            lookup_period_journals(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:1999-01",
            )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_period_journals(
                "urn:cwl:legal_entity:missing",
                self.policy.accounting_book_reference,
                "2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "accounting_book"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_period_journals(
                self.policy.legal_entity_reference,
                "urn:cwl:accounting_book:missing",
                "2026-08",
            )

        self.assertEqual(missing_scope[0], 400)
        self.assertEqual(unknown_entity[0], 404)
        self.assertEqual(unknown_book[0], 404)
        self.assertEqual(unknown_period[0], 404)
        self.assertEqual(bad_limit[0], 400)
        self.assertEqual(high_limit[0], 400)
        self.assertEqual(bad_cursor[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before + 5)
        server.shutdown()

    def test_http_lists_period_journals_by_source(self) -> None:
        """GET /journals optional journal_source_code isolates billing, adjusting, closing, and reversal journals."""
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")

        empty_status, empty = self._http_period_journals()
        empty_billing_status, empty_billing = self._http_period_journals(
            journal_source_code="billing"
        )
        empty_adjusting_status, empty_adjusting = self._http_period_journals(
            journal_source_code="adjusting"
        )
        empty_closing_status, empty_closing = self._http_period_journals(
            journal_source_code="period_closing"
        )
        empty_reversal_status, empty_reversal = self._http_period_journals(
            journal_source_code="reversal"
        )
        invalid_status, invalid_document = self._http_period_journals(
            journal_source_code="working"
        )
        with self.assertRaisesRegex(
            AccountingValidationError,
            "must be billing, adjusting, period_closing, or reversal",
        ):
            lookup_period_journals(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
                journal_source_code="working",
            )
        with self.assertRaisesRegex(
            AccountingValidationError,
            "must be billing, adjusting, period_closing, or reversal",
        ):
            self.ledger.load_period_journals(
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "2026-08",
                journal_source_code="working",
            )

        self.assertEqual(empty_status, 200)
        self.assertEqual(empty["journals"], [])
        self.assertNotIn("journal_source_code", empty)
        self.assertEqual(empty_billing_status, 200)
        self.assertEqual(empty_billing["journals"], [])
        self.assertEqual(empty_billing["journal_source_code"], "billing")
        self.assertEqual(empty_adjusting_status, 200)
        self.assertEqual(empty_adjusting["journals"], [])
        self.assertEqual(empty_adjusting["journal_source_code"], "adjusting")
        self.assertEqual(empty_closing_status, 200)
        self.assertEqual(empty_closing["journals"], [])
        self.assertEqual(empty_closing["journal_source_code"], "period_closing")
        self.assertEqual(empty_reversal_status, 200)
        self.assertEqual(empty_reversal["journals"], [])
        self.assertEqual(empty_reversal["journal_source_code"], "reversal")
        self.assertEqual(invalid_status, 400)
        self.assertIn(
            "must be billing, adjusting, period_closing, or reversal",
            str(invalid_document["error_message"]),
        )

        invoice = self._billing_validated_payload()
        cash = self._billing_cash_payload()
        adjusting = self._adjusting_journal_payload()
        invoice_status, invoice_receipt = self._http_json(
            "POST", "/journal-proposals", invoice
        )
        cash_status, cash_receipt = self._http_json("POST", "/journal-proposals", cash)
        adjusting_status, adjusting_receipt = self._http_json(
            "POST", "/journals", adjusting
        )
        omit_status, omitted = self._http_period_journals()
        adjusting_list_status, adjusting_list = self._http_period_journals(
            journal_source_code="adjusting"
        )
        billing_list_status, billing_list = self._http_period_journals(
            journal_source_code="billing"
        )
        persist_adjusting = self.ledger.load_period_journals(
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "2026-08",
            journal_source_code="adjusting",
        )
        lookup_adjusting = lookup_period_journals(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
            journal_source_code="adjusting",
        )
        omit_keys = {str(item["idempotency_key"]) for item in omitted["journals"]}
        adjusting_keys = {
            str(item["idempotency_key"]) for item in adjusting_list["journals"]
        }
        billing_keys = {str(item["idempotency_key"]) for item in billing_list["journals"]}

        self.assertEqual(invoice_status, 200)
        self.assertEqual(cash_status, 200)
        self.assertEqual(adjusting_status, 200)
        self.assertEqual(omit_status, 200)
        self.assertNotIn("journal_source_code", omitted)
        self.assertEqual(
            omit_keys,
            {
                str(invoice["idempotency_key"]),
                str(cash["idempotency_key"]),
                str(adjusting["idempotency_key"]),
            },
        )
        self.assertEqual(adjusting_list_status, 200)
        self.assertEqual(adjusting_list["journal_source_code"], "adjusting")
        self.assertEqual(adjusting_keys, {str(adjusting["idempotency_key"])})
        self.assertEqual(
            adjusting_list["journals"][0]["journal_reference"],
            adjusting_receipt["journal_reference"],
        )
        self.assertEqual(adjusting_list["journals"][0]["line_count"], 2)
        self.assertEqual(billing_list_status, 200)
        self.assertEqual(billing_list["journal_source_code"], "billing")
        self.assertEqual(
            billing_keys,
            {str(invoice["idempotency_key"]), str(cash["idempotency_key"])},
        )
        self.assertNotIn(str(adjusting["idempotency_key"]), billing_keys)
        self.assertEqual(persist_adjusting, adjusting_list)
        self.assertEqual(lookup_adjusting, adjusting_list)

        reverse_status, reversing_receipt = self._http_json(
            "POST",
            "/journal-reversals",
            {
                "tenant_reference": self.policy.tenant_reference,
                "journal_reference": invoice_receipt["journal_reference"],
                "reversal_date": "2026-08-31",
                "reversal_reason_code": "billing_correction",
            },
        )
        reversal_list_status, reversal_list = self._http_period_journals(
            journal_source_code="reversal"
        )
        billing_after_reverse_status, billing_after_reverse = self._http_period_journals(
            journal_source_code="billing"
        )
        single_status, single = self._http_journal(
            idempotency_key=str(invoice["idempotency_key"])
        )
        single_by_reference_status, single_by_reference = self._http_journal(
            journal_reference=str(invoice_receipt["journal_reference"])
        )
        reversal_keys = {
            str(item["idempotency_key"]) for item in reversal_list["journals"]
        }
        billing_after_keys = {
            str(item["idempotency_key"]) for item in billing_after_reverse["journals"]
        }

        self.assertEqual(reverse_status, 200)
        self.assertEqual(reversal_list_status, 200)
        self.assertEqual(reversal_list["journal_source_code"], "reversal")
        self.assertEqual(
            reversal_keys,
            {f"reversal:{invoice_receipt['journal_reference']}"},
        )
        self.assertEqual(
            reversal_list["journals"][0]["journal_reference"],
            reversing_receipt["journal_reference"],
        )
        self.assertEqual(
            reversal_list["journals"][0]["reversal_of_journal_reference"],
            invoice_receipt["journal_reference"],
        )
        self.assertEqual(reversal_list["journals"][0]["line_count"], 2)
        self.assertEqual(billing_after_reverse_status, 200)
        self.assertEqual(
            billing_after_keys,
            {str(invoice["idempotency_key"]), str(cash["idempotency_key"])},
        )
        self.assertNotIn(f"reversal:{invoice_receipt['journal_reference']}", billing_after_keys)
        self.assertEqual(single_status, 200)
        self.assertEqual(single_by_reference_status, 200)
        self.assertEqual(single, single_by_reference)
        self.assertEqual(single["journal_reference"], invoice_receipt["journal_reference"])
        self.assertEqual(single["idempotency_key"], invoice["idempotency_key"])
        self.assertIn("lines", single)
        self.assertEqual(len(single["lines"]), 2)

        hard_status, _hard = self._http_json(
            "POST", "/period-closes", self._period_close_payload()
        )
        closing_list_status, closing_list = self._http_period_journals(
            journal_source_code="period_closing"
        )
        adjusting_after_close_status, adjusting_after_close = self._http_period_journals(
            journal_source_code="adjusting"
        )
        billing_after_close_status, billing_after_close = self._http_period_journals(
            journal_source_code="billing"
        )
        omit_after_close_status, omit_after_close = self._http_period_journals()
        closing_keys = {str(item["idempotency_key"]) for item in closing_list["journals"]}
        billing_close_keys = {
            str(item["idempotency_key"]) for item in billing_after_close["journals"]
        }

        self.assertEqual(hard_status, 200)
        self.assertEqual(closing_list_status, 200)
        self.assertEqual(closing_list["journal_source_code"], "period_closing")
        self.assertEqual(len(closing_list["journals"]), 1)
        self.assertEqual(
            closing_keys,
            {f"{self.policy.tenant_reference}:period_closing:2026-08"},
        )
        self.assertTrue(
            str(closing_list["journals"][0]["journal_reference"]).startswith(
                "urn:cwl:accounting:general_journal:period_closing:"
            )
        )
        self.assertEqual(adjusting_after_close_status, 200)
        self.assertEqual(
            {str(item["idempotency_key"]) for item in adjusting_after_close["journals"]},
            {str(adjusting["idempotency_key"])},
        )
        self.assertEqual(billing_after_close_status, 200)
        self.assertEqual(
            billing_close_keys,
            {str(invoice["idempotency_key"]), str(cash["idempotency_key"])},
        )
        self.assertNotIn(
            f"{self.policy.tenant_reference}:period_closing:2026-08",
            billing_close_keys,
        )
        self.assertEqual(omit_after_close_status, 200)
        self.assertNotIn("journal_source_code", omit_after_close)
        self.assertEqual(len(omit_after_close["journals"]), 5)
        unknown_period = self._http_period_journals(
            journal_source_code="billing",
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01",
        )
        missing_header = self._http_period_journals(
            journal_source_code="billing", tenant_header=None
        )
        cross_status, _cross = self._http_period_journals(
            journal_source_code="billing", tenant_header="urn:cwl:tenant_other"
        )
        self.assertEqual(unknown_period[0], 404)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(
            self._count_table("accounting_core.general_journal"),
            journals_before + 5,
        )
        server.shutdown()

    def test_http_reads_receivable_aging(self) -> None:
        """GET /receivable-agings ages entity-level AR by FIFO through period end."""
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")

        empty_status, empty = self._http_receivable_aging()
        empty_explicit_status, empty_explicit = self._http_receivable_aging(
            chart_account_code="110100"
        )
        empty_library = lookup_receivable_aging(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        empty_persist = self.ledger.load_receivable_aging(
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "2026-08",
        )
        zero_document = {
            "tenant_reference": self.policy.tenant_reference,
            "legal_entity_reference": self.policy.legal_entity_reference,
            "accounting_book_reference": self.policy.accounting_book_reference,
            "book_reference": self.policy.accounting_book_reference,
            "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
            "chart_account_code": "110100",
            "account_class_code": "asset",
            "as_of_date": "2026-08-31",
            "current_amount": "0",
            "days_31_60_amount": "0",
            "days_61_90_amount": "0",
            "days_over_90_amount": "0",
            "total_outstanding_amount": "0",
        }

        self.assertEqual(empty_status, 200)
        self.assertEqual(empty_explicit_status, 200)
        self.assertEqual(empty, zero_document)
        self.assertEqual(empty_explicit, empty)
        self.assertEqual(empty_library, empty)
        self.assertEqual(empty_persist, empty)
        self.assertNotIn("party_reference", empty)

        self._seed_additional_period("2026-06", date(2026, 6, 1), date(2026, 6, 30))
        self._seed_additional_period("2026-07", date(2026, 7, 1), date(2026, 7, 31))
        june_invoice = self._billing_validated_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:invoice_draft:june:"
                f"sha256:{'6' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "6" * 64,
            transaction_date="2026-06-01",
            accounting_date="2026-06-01",
            proposed_at="2026-06-01T00:00:00Z",
            source_event_references=(f"{self.policy.tenant_reference}:invoice_draft:june",),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "2000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "0",
                    "credit_amount": "2000",
                },
            ],
        )
        july_invoice = self._billing_validated_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:invoice_draft:july:"
                f"sha256:{'7' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "7" * 64,
            transaction_date="2026-07-01",
            accounting_date="2026-07-01",
            proposed_at="2026-07-01T00:00:00Z",
            source_event_references=(f"{self.policy.tenant_reference}:invoice_draft:july",),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "10000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "0",
                    "credit_amount": "10000",
                },
            ],
        )
        late_july_invoice = self._billing_validated_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:invoice_draft:july31:"
                f"sha256:{'8' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "8" * 64,
            transaction_date="2026-07-31",
            accounting_date="2026-07-31",
            proposed_at="2026-07-31T00:00:00Z",
            source_event_references=(f"{self.policy.tenant_reference}:invoice_draft:july31",),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "3000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "0",
                    "credit_amount": "3000",
                },
            ],
        )
        june_status, _june = self._http_json("POST", "/journal-proposals", june_invoice)
        july_status, _july = self._http_json("POST", "/journal-proposals", july_invoice)
        late_july_status, _late_july = self._http_json(
            "POST", "/journal-proposals", late_july_invoice
        )
        august_invoice = self._billing_validated_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:invoice_draft:august:"
                f"sha256:{'a' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "a" * 64,
            source_event_references=(
                f"{self.policy.tenant_reference}:invoice_draft:august",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "8000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "0",
                    "credit_amount": "8000",
                },
            ],
        )
        august_status, _august = self._http_json("POST", "/journal-proposals", august_invoice)
        aged_status, aged = self._http_receivable_aging()
        fifo_cash = self._billing_cash_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:cash_receipt:fifo:"
                f"sha256:{'d' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "d" * 64,
            source_event_references=(f"{self.policy.tenant_reference}:cash_receipt:fifo",),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "cash_receipt",
                    "debit_amount": "5000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0",
                    "credit_amount": "5000",
                },
            ],
        )
        fifo_status, _fifo = self._http_json("POST", "/journal-proposals", fifo_cash)
        fifo_aging_status, fifo_aging = self._http_receivable_aging()
        fifo_balances_status, fifo_balances = self._http_account_balances(
            chart_account_code="110100"
        )
        fifo_net = Decimal(str(fifo_balances["account_balances"][0]["debit_amount"])) - Decimal(
            str(fifo_balances["account_balances"][0]["credit_amount"])
        )

        self.assertEqual(june_status, 200)
        self.assertEqual(july_status, 200)
        self.assertEqual(late_july_status, 200)
        self.assertEqual(august_status, 200)
        self.assertEqual(aged_status, 200)
        self.assertEqual(aged["current_amount"], "8000")
        self.assertEqual(aged["days_31_60_amount"], "3000")
        self.assertEqual(aged["days_61_90_amount"], "10000")
        self.assertEqual(aged["days_over_90_amount"], "2000")
        self.assertEqual(aged["total_outstanding_amount"], "23000")
        self.assertEqual(fifo_status, 200)
        self.assertEqual(fifo_aging_status, 200)
        self.assertEqual(fifo_balances_status, 200)
        self.assertEqual(fifo_aging["days_over_90_amount"], "0")
        self.assertEqual(fifo_aging["days_61_90_amount"], "7000")
        self.assertEqual(fifo_aging["days_31_60_amount"], "3000")
        self.assertEqual(fifo_aging["current_amount"], "8000")
        self.assertEqual(fifo_aging["total_outstanding_amount"], "18000")
        self.assertEqual(Decimal(str(fifo_aging["total_outstanding_amount"])), fifo_net)

        credit = self._billing_credit_payload()
        credit_status, _credit = self._http_json("POST", "/journal-proposals", credit)
        credit_aging_status, credit_aging = self._http_receivable_aging()
        credit_balances_status, credit_balances = self._http_account_balances(
            chart_account_code="110100"
        )
        credit_net = Decimal(str(credit_balances["account_balances"][0]["debit_amount"])) - Decimal(
            str(credit_balances["account_balances"][0]["credit_amount"])
        )

        self.assertEqual(credit_status, 200)
        self.assertEqual(credit_aging_status, 200)
        self.assertEqual(credit_balances_status, 200)
        self.assertEqual(credit_aging["days_61_90_amount"], "3000")
        self.assertEqual(credit_aging["days_31_60_amount"], "3000")
        self.assertEqual(credit_aging["current_amount"], "8000")
        self.assertEqual(credit_aging["days_over_90_amount"], "0")
        self.assertEqual(credit_aging["total_outstanding_amount"], "14000")
        self.assertEqual(Decimal(str(credit_aging["total_outstanding_amount"])), credit_net)

        settle = self._billing_cash_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:cash_receipt:settle:"
                f"sha256:{'e' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "e" * 64,
            source_event_references=(f"{self.policy.tenant_reference}:cash_receipt:settle",),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "cash_receipt",
                    "debit_amount": "14000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0",
                    "credit_amount": "14000",
                },
            ],
        )
        settle_status, _settle = self._http_json("POST", "/journal-proposals", settle)
        settled_status, settled = self._http_receivable_aging()

        self.assertEqual(settle_status, 200)
        self.assertEqual(settled_status, 200)
        self.assertEqual(settled["current_amount"], "0")
        self.assertEqual(settled["days_31_60_amount"], "0")
        self.assertEqual(settled["days_61_90_amount"], "0")
        self.assertEqual(settled["days_over_90_amount"], "0")
        self.assertEqual(settled["total_outstanding_amount"], "0")

        invoice = self._billing_validated_payload()
        cash = self._billing_cash_payload()
        invoice_status, _invoice = self._http_json("POST", "/journal-proposals", invoice)
        cash_status, _cash = self._http_json("POST", "/journal-proposals", cash)
        current_status, current = self._http_receivable_aging()
        current_balances_status, current_balances = self._http_account_balances(
            chart_account_code="110100"
        )
        current_net = Decimal(str(current_balances["account_balances"][0]["debit_amount"])) - Decimal(
            str(current_balances["account_balances"][0]["credit_amount"])
        )

        self.assertEqual(invoice_status, 200)
        self.assertEqual(cash_status, 200)
        self.assertEqual(current_status, 200)
        self.assertEqual(current_balances_status, 200)
        self.assertEqual(current["current_amount"], "7000")
        self.assertEqual(current["days_31_60_amount"], "0")
        self.assertEqual(current["days_61_90_amount"], "0")
        self.assertEqual(current["days_over_90_amount"], "0")
        self.assertEqual(current["total_outstanding_amount"], "7000")
        self.assertEqual(Decimal(str(current["total_outstanding_amount"])), current_net)
        self.assertEqual(
            Decimal(str(current["current_amount"]))
            + Decimal(str(current["days_31_60_amount"]))
            + Decimal(str(current["days_61_90_amount"]))
            + Decimal(str(current["days_over_90_amount"])),
            Decimal(str(current["total_outstanding_amount"])),
        )

        adjusting_status, _adjusting = self._http_json(
            "POST", "/journals", self._adjusting_journal_payload()
        )
        hard_status, _hard = self._http_json("POST", "/period-closes", self._period_close_payload())
        closed_status, closed = self._http_receivable_aging()
        closed_balances_status, closed_balances = self._http_account_balances(
            chart_account_code="110100"
        )
        closed_net = Decimal(str(closed_balances["account_balances"][0]["debit_amount"])) - Decimal(
            str(closed_balances["account_balances"][0]["credit_amount"])
        )

        self.assertEqual(adjusting_status, 200)
        self.assertEqual(hard_status, 200)
        self.assertEqual(closed_status, 200)
        self.assertEqual(closed_balances_status, 200)
        self.assertEqual(self._count_closing_journals(), 1)
        self.assertEqual(closed["current_amount"], "8000")
        self.assertEqual(closed["days_31_60_amount"], "0")
        self.assertEqual(closed["days_61_90_amount"], "0")
        self.assertEqual(closed["days_over_90_amount"], "0")
        self.assertEqual(closed["total_outstanding_amount"], "8000")
        self.assertEqual(Decimal(str(closed["total_outstanding_amount"])), closed_net)
        self.assertEqual(closed["as_of_date"], "2026-08-31")

        missing_query = self._http_json("GET", "/receivable-agings", None)
        post_status, _post = self._http_json("POST", "/receivable-agings", {})
        cash_chart = self._http_receivable_aging(chart_account_code="110200")
        revenue_chart = self._http_receivable_aging(chart_account_code="410100")
        unknown_account = self._http_receivable_aging(chart_account_code="999999")
        unknown_period = self._http_receivable_aging(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01"
        )
        missing_header = self._http_receivable_aging(tenant_header=None)
        cross_status, _cross = self._http_receivable_aging(tenant_header="urn:cwl:tenant_other")
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            lookup_receivable_aging(
                DATABASE_URL,
                self.policy.tenant_reference,
                "",
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            lookup_receivable_aging(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                "",
                "urn:cwl:accounting:fiscal_period:2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            lookup_receivable_aging(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "",
            )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            self.ledger.load_receivable_aging(
                "",
                self.policy.accounting_book_reference,
                "2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            self.ledger.load_receivable_aging(
                self.policy.legal_entity_reference,
                "",
                "2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            self.ledger.load_receivable_aging(
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "",
            )
        with self.assertRaisesRegex(AccountingValidationError, "accounts_receivable"):
            lookup_receivable_aging(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
                chart_account_code="110200",
            )
        with self.assertRaisesRegex(AccountingValidationError, "accounts_receivable"):
            self.ledger.load_receivable_aging(
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "2026-08",
                chart_account_code="110200",
            )

        self.assertEqual(missing_query[0], 400)
        self.assertEqual(post_status, 405)
        self.assertEqual(cash_chart[0], 422)
        self.assertIn("accounts_receivable", str(cash_chart[1]["error_message"]))
        self.assertEqual(revenue_chart[0], 422)
        self.assertEqual(unknown_account[0], 404)
        self.assertEqual(unknown_period[0], 404)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(
            self._count_table("accounting_core.general_journal"),
            journals_before + 11,
        )
        server.shutdown()

    def test_http_reads_payable_aging(self) -> None:
        """GET /payable-agings ages entity-level tax payable by FIFO through period end."""
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")

        empty_status, empty = self._http_payable_aging()
        empty_explicit_status, empty_explicit = self._http_payable_aging(
            chart_account_code="210100"
        )
        empty_library = lookup_payable_aging(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        empty_persist = self.ledger.load_payable_aging(
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "2026-08",
        )
        zero_document = {
            "tenant_reference": self.policy.tenant_reference,
            "legal_entity_reference": self.policy.legal_entity_reference,
            "accounting_book_reference": self.policy.accounting_book_reference,
            "book_reference": self.policy.accounting_book_reference,
            "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
            "chart_account_code": "210100",
            "account_class_code": "liability",
            "as_of_date": "2026-08-31",
            "current_amount": "0",
            "days_31_60_amount": "0",
            "days_61_90_amount": "0",
            "days_over_90_amount": "0",
            "total_outstanding_amount": "0",
        }

        self.assertEqual(empty_status, 200)
        self.assertEqual(empty_explicit_status, 200)
        self.assertEqual(empty, zero_document)
        self.assertEqual(empty_explicit, empty)
        self.assertEqual(empty_library, empty)
        self.assertEqual(empty_persist, empty)
        self.assertNotIn("party_reference", empty)

        untaxed = self._billing_validated_payload()
        untaxed_status, _untaxed = self._http_json("POST", "/journal-proposals", untaxed)
        untaxed_aging_status, untaxed_aging = self._http_payable_aging()

        self.assertEqual(untaxed_status, 200)
        self.assertEqual(untaxed_aging_status, 200)
        self.assertEqual(untaxed_aging, zero_document)

        self._seed_additional_period("2026-06", date(2026, 6, 1), date(2026, 6, 30))
        june_taxed = self._billing_taxed_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:invoice_draft:june_tax:"
                f"sha256:{'6' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "6" * 64,
            transaction_date="2026-06-01",
            accounting_date="2026-06-01",
            proposed_at="2026-06-01T00:00:00Z",
            source_event_references=(
                f"{self.policy.tenant_reference}:invoice_draft:june_tax",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "27500",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "0",
                    "credit_amount": "25000",
                },
                {
                    "line_number": 3,
                    "account_role_code": "tax_payable",
                    "debit_amount": "0",
                    "credit_amount": "2500",
                },
            ],
        )
        taxed = self._billing_taxed_payload()
        june_status, _june = self._http_json("POST", "/journal-proposals", june_taxed)
        taxed_status, _taxed = self._http_json("POST", "/journal-proposals", taxed)
        taxed_aging_status, taxed_aging = self._http_payable_aging()
        taxed_balances_status, taxed_balances = self._http_account_balances(
            chart_account_code="210100"
        )
        taxed_net = Decimal(str(taxed_balances["account_balances"][0]["credit_amount"])) - Decimal(
            str(taxed_balances["account_balances"][0]["debit_amount"])
        )

        self.assertEqual(june_status, 200)
        self.assertEqual(taxed_status, 200)
        self.assertEqual(taxed_aging_status, 200)
        self.assertEqual(taxed_balances_status, 200)
        self.assertEqual(taxed_aging["current_amount"], "2500")
        self.assertEqual(taxed_aging["days_31_60_amount"], "0")
        self.assertEqual(taxed_aging["days_61_90_amount"], "0")
        self.assertEqual(taxed_aging["days_over_90_amount"], "2500")
        self.assertEqual(taxed_aging["total_outstanding_amount"], "5000")
        self.assertEqual(Decimal(str(taxed_aging["total_outstanding_amount"])), taxed_net)
        self.assertEqual(taxed_aging["account_class_code"], "liability")
        self.assertEqual(taxed_aging["chart_account_code"], "210100")
        self.assertEqual(taxed_aging["as_of_date"], "2026-08-31")

        partial = self._billing_taxed_credit_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:credit_adjustment:partial_tax:"
                f"sha256:{'9' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "9" * 64,
            source_event_references=(
                f"{self.policy.tenant_reference}:credit_adjustment:partial_tax",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "10000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "tax_payable",
                    "debit_amount": "1000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 3,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0",
                    "credit_amount": "11000",
                },
            ],
        )
        partial_status, _partial = self._http_json("POST", "/journal-proposals", partial)
        partial_aging_status, partial_aging = self._http_payable_aging()
        partial_balances_status, partial_balances = self._http_account_balances(
            chart_account_code="210100"
        )
        partial_net = Decimal(
            str(partial_balances["account_balances"][0]["credit_amount"])
        ) - Decimal(str(partial_balances["account_balances"][0]["debit_amount"]))

        self.assertEqual(partial_status, 200)
        self.assertEqual(partial_aging_status, 200)
        self.assertEqual(partial_balances_status, 200)
        self.assertEqual(partial_aging["days_over_90_amount"], "1500")
        self.assertEqual(partial_aging["current_amount"], "2500")
        self.assertEqual(partial_aging["total_outstanding_amount"], "4000")
        self.assertEqual(Decimal(str(partial_aging["total_outstanding_amount"])), partial_net)

        remaining = self._billing_taxed_credit_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:credit_adjustment:remaining_tax:"
                f"sha256:{'e' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "e" * 64,
            source_event_references=(
                f"{self.policy.tenant_reference}:credit_adjustment:remaining_tax",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "40000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "tax_payable",
                    "debit_amount": "4000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 3,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0",
                    "credit_amount": "44000",
                },
            ],
        )
        remaining_status, _remaining = self._http_json("POST", "/journal-proposals", remaining)
        settled_status, settled = self._http_payable_aging()

        self.assertEqual(remaining_status, 200)
        self.assertEqual(settled_status, 200)
        self.assertEqual(settled, zero_document)

        reopen = self._billing_taxed_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:invoice_draft:close_tax:"
                f"sha256:{'a' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "a" * 64,
            source_event_references=(
                f"{self.policy.tenant_reference}:invoice_draft:close_tax",
            ),
        )
        reopen_status, _reopen = self._http_json("POST", "/journal-proposals", reopen)

        self.assertEqual(reopen_status, 200)

        adjusting_status, _adjusting = self._http_json(
            "POST",
            "/journals",
            self._adjusting_journal_payload(
                idempotency_key=f"{self.policy.tenant_reference}:adjusting_journal:tax_accrual:v1",
                journal_description="Accrue additional tax payable",
                journal_lines=[
                    {
                        "chart_account_code": "410100",
                        "debit_credit_code": "debit",
                        "amount": "500",
                        "currency_code": "KRW",
                    },
                    {
                        "chart_account_code": "210100",
                        "debit_credit_code": "credit",
                        "amount": "500",
                        "currency_code": "KRW",
                    },
                ],
            ),
        )
        hard_status, _hard = self._http_json("POST", "/period-closes", self._period_close_payload())
        closed_status, closed = self._http_payable_aging()
        closed_balances_status, closed_balances = self._http_account_balances(
            chart_account_code="210100"
        )
        closed_net = Decimal(
            str(closed_balances["account_balances"][0]["credit_amount"])
        ) - Decimal(str(closed_balances["account_balances"][0]["debit_amount"]))

        self.assertEqual(adjusting_status, 200)
        self.assertEqual(hard_status, 200)
        self.assertEqual(closed_status, 200)
        self.assertEqual(closed_balances_status, 200)
        self.assertEqual(self._count_closing_journals(), 1)
        self.assertEqual(closed["current_amount"], "3000")
        self.assertEqual(closed["days_over_90_amount"], "0")
        self.assertEqual(closed["total_outstanding_amount"], "3000")
        self.assertEqual(Decimal(str(closed["total_outstanding_amount"])), closed_net)
        self.assertEqual(closed["as_of_date"], "2026-08-31")
        package_status, package = self._http_period_close_package()
        standalone_status, standalone = self._http_payable_aging()
        self.assertEqual(package_status, 200)
        self.assertEqual(standalone_status, 200)
        self.assertEqual(package["payable_aging"], standalone)
        self.assertEqual(package["payable_aging"]["as_of_date"], package["receivable_aging"]["as_of_date"])
        self.assertEqual(package["payable_aging"]["total_outstanding_amount"], "3000")

        missing_query = self._http_json("GET", "/payable-agings", None)
        post_status, _post = self._http_json("POST", "/payable-agings", {})
        ar_chart = self._http_payable_aging(chart_account_code="110100")
        cash_chart = self._http_payable_aging(chart_account_code="110200")
        revenue_chart = self._http_payable_aging(chart_account_code="410100")
        unknown_account = self._http_payable_aging(chart_account_code="999999")
        unknown_period = self._http_payable_aging(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01"
        )
        missing_header = self._http_payable_aging(tenant_header=None)
        cross_status, _cross = self._http_payable_aging(tenant_header="urn:cwl:tenant_other")
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            lookup_payable_aging(
                DATABASE_URL,
                self.policy.tenant_reference,
                "",
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            lookup_payable_aging(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                "",
                "urn:cwl:accounting:fiscal_period:2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            lookup_payable_aging(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "",
            )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            self.ledger.load_payable_aging(
                "",
                self.policy.accounting_book_reference,
                "2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            self.ledger.load_payable_aging(
                self.policy.legal_entity_reference,
                "",
                "2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            self.ledger.load_payable_aging(
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "",
            )
        with self.assertRaisesRegex(AccountingValidationError, "tax_payable"):
            lookup_payable_aging(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
                chart_account_code="110100",
            )
        with self.assertRaisesRegex(AccountingValidationError, "tax_payable"):
            self.ledger.load_payable_aging(
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "2026-08",
                chart_account_code="110100",
            )

        self.assertEqual(missing_query[0], 400)
        self.assertEqual(post_status, 405)
        self.assertEqual(ar_chart[0], 422)
        self.assertIn("tax_payable", str(ar_chart[1]["error_message"]))
        self.assertEqual(cash_chart[0], 422)
        self.assertEqual(revenue_chart[0], 422)
        self.assertEqual(unknown_account[0], 404)
        self.assertEqual(unknown_period[0], 404)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(
            self._count_table("accounting_core.general_journal"),
            journals_before + 8,
        )
        server.shutdown()

    def test_http_reads_period_close_package(self) -> None:
        """GET /period-close-packages composes the existing close-binder reads."""
        self._seed_additional_period("2026-07", date(2026, 7, 1), date(2026, 7, 31))
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")

        july = self._billing_validated_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=(
                f"{self.policy.tenant_reference}:invoice_draft:july-binder:"
                f"sha256:{'7' * 64}:v1"
            ),
            source_payload_hash="sha256:" + "7" * 64,
            transaction_date="2026-07-15",
            accounting_date="2026-07-15",
            proposed_at="2026-07-15T00:00:00Z",
            source_event_references=(
                f"{self.policy.tenant_reference}:invoice_draft:july-binder",
            ),
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "10000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "0",
                    "credit_amount": "10000",
                },
            ],
        )
        july_status, _july = self._http_json("POST", "/journal-proposals", july)
        july_close_status, _july_close = self._http_json(
            "POST",
            "/period-closes",
            self._period_close_payload(
                fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07"
            ),
        )
        invoice_status, _invoice = self._http_json(
            "POST", "/journal-proposals", self._billing_validated_payload()
        )
        cash_status, _cash = self._http_json(
            "POST", "/journal-proposals", self._billing_cash_payload()
        )
        taxed_status, _taxed = self._http_json(
            "POST", "/journal-proposals", self._billing_taxed_payload()
        )
        open_status, opened = self._http_period_close_package()
        open_library = lookup_period_close_package(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        fiscal_status, fiscal_period = self._http_fiscal_period()
        trial_status, trial_balance = self._http_trial_balance()
        package_status, statement_package = self._http_financial_statement_package()
        aging_status, receivable_aging = self._http_receivable_aging()
        payable_status, payable_aging = self._http_payable_aging()
        balance_status, account_balances = self._http_account_balances(
            chart_account_code="110100"
        )
        payable_balance_status, payable_balances = self._http_account_balances(
            chart_account_code="210100"
        )
        self.assertEqual(july_status, 200)
        self.assertEqual(july_close_status, 200)
        self.assertEqual(invoice_status, 200)
        self.assertEqual(cash_status, 200)
        self.assertEqual(taxed_status, 200)
        self.assertEqual(open_status, 200)
        self.assertEqual(opened, open_library)
        self.assertEqual(opened["tenant_reference"], self.policy.tenant_reference)
        self.assertEqual(opened["legal_entity_reference"], self.policy.legal_entity_reference)
        self.assertEqual(opened["accounting_book_reference"], self.policy.accounting_book_reference)
        self.assertEqual(opened["book_reference"], self.policy.accounting_book_reference)
        self.assertEqual(
            opened["fiscal_period_reference"],
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        self.assertNotIn("statement_scope_code", opened)
        self.assertNotIn("comparison_fiscal_period_reference", opened)
        self.assertEqual(fiscal_status, 200)
        self.assertEqual(opened["fiscal_period"], fiscal_period)
        self.assertEqual(opened["fiscal_period"]["period_status_code"], "open")
        self.assertEqual(trial_status, 200)
        self.assertEqual(opened["trial_balance"], trial_balance)
        self.assertNotIn("balance_basis_code", opened["trial_balance"])
        self.assertEqual(package_status, 200)
        self.assertEqual(opened["financial_statement_package"], statement_package)
        self.assertEqual(aging_status, 200)
        self.assertEqual(opened["receivable_aging"], receivable_aging)
        self.assertEqual(payable_status, 200)
        leftover_status, leftover = self._http_unapplied_cash_rollforward()
        leftover_balance_status, leftover_balances = self._http_account_balances(
            chart_account_code="210200"
        )
        leftover_net = Decimal(
            str(leftover_balances["account_balances"][0]["credit_amount"])
        ) - Decimal(str(leftover_balances["account_balances"][0]["debit_amount"]))
        self.assertEqual(opened["payable_aging"], payable_aging)
        self.assertEqual(opened["payable_aging"]["as_of_date"], opened["receivable_aging"]["as_of_date"])
        self.assertEqual(opened["payable_aging"]["chart_account_code"], "210100")
        self.assertEqual(leftover_status, 200)
        self.assertEqual(leftover_balance_status, 200)
        self.assertEqual(opened["unapplied_cash_rollforward"], leftover)
        self.assertEqual(opened["unapplied_cash_rollforward"]["chart_account_code"], "210200")
        self.assertEqual(opened["unapplied_cash_rollforward"]["account_role_code"], "unapplied_cash")
        self.assertEqual(opened["unapplied_cash_rollforward"]["closing_amount"], "0")
        self.assertEqual(
            Decimal(str(opened["unapplied_cash_rollforward"]["closing_amount"])),
            leftover_net,
        )
        self.assertEqual(set(opened), {
            "tenant_reference",
            "legal_entity_reference",
            "accounting_book_reference",
            "book_reference",
            "fiscal_period_reference",
            "fiscal_period",
            "trial_balance",
            "financial_statement_package",
            "receivable_aging",
            "payable_aging",
            "unapplied_cash_rollforward",
            "period_close",
        })
        self.assertIsNone(opened["period_close"])
        self.assertEqual(balance_status, 200)
        self.assertEqual(payable_balance_status, 200)
        aging_total = Decimal(str(opened["receivable_aging"]["total_outstanding_amount"]))
        self.assertEqual(aging_total, Decimal(str(receivable_aging["total_outstanding_amount"])))
        self.assertEqual(aging_total, self._trial_balance_account_net(trial_balance, "110100"))
        self.assertEqual(aging_total, self._account_balance_net(account_balances, "110100"))
        payable_total = Decimal(str(opened["payable_aging"]["total_outstanding_amount"]))
        self.assertEqual(payable_total, Decimal(str(payable_aging["total_outstanding_amount"])))
        self.assertEqual(
            payable_total,
            Decimal(str(payable_balances["account_balances"][0]["credit_amount"]))
            - Decimal(str(payable_balances["account_balances"][0]["debit_amount"])),
        )

        soft_status, _soft = self._http_json(
            "POST",
            "/period-closes",
            self._period_close_payload(period_status_code="soft_closed"),
        )
        soft_package_status, soft_package = self._http_period_close_package()
        soft_fiscal_status, soft_fiscal = self._http_fiscal_period()
        self.assertEqual(soft_status, 200)
        self.assertEqual(soft_package_status, 200)
        self.assertEqual(soft_fiscal_status, 200)
        self.assertEqual(soft_package["fiscal_period"], soft_fiscal)
        self.assertEqual(soft_package["fiscal_period"]["period_status_code"], "soft_closed")
        self.assertIsNone(soft_package["period_close"])
        self.assertEqual(soft_package["trial_balance"], self._http_trial_balance()[1])
        self.assertEqual(
            soft_package["financial_statement_package"],
            self._http_financial_statement_package()[1],
        )
        self.assertEqual(soft_package["receivable_aging"], self._http_receivable_aging()[1])
        self.assertEqual(soft_package["payable_aging"], self._http_payable_aging()[1])
        self.assertEqual(
            soft_package["payable_aging"]["as_of_date"],
            soft_package["receivable_aging"]["as_of_date"],
        )

        hard_status, _hard = self._http_json("POST", "/period-closes", self._period_close_payload())
        hard_package_status, hard_package = self._http_period_close_package()
        hard_library = lookup_period_close_package(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        hard_fiscal_status, hard_fiscal = self._http_fiscal_period()
        hard_trial_status, hard_trial = self._http_trial_balance()
        hard_package_statements = self._http_financial_statement_package()[1]
        hard_aging_status, hard_aging = self._http_receivable_aging()
        hard_payable_status, hard_payable = self._http_payable_aging()
        hard_balance_status, hard_balances = self._http_account_balances(
            chart_account_code="110100"
        )
        closes_status, closes = self._http_period_closes(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-08"
        )
        self.assertEqual(hard_status, 200)
        self.assertEqual(hard_package_status, 200)
        self.assertEqual(hard_package, hard_library)
        self.assertEqual(hard_fiscal_status, 200)
        self.assertEqual(hard_package["fiscal_period"], hard_fiscal)
        self.assertEqual(hard_package["fiscal_period"]["period_status_code"], "hard_closed")
        self.assertEqual(hard_trial_status, 200)
        self.assertEqual(hard_package["trial_balance"], hard_trial)
        self.assertEqual(hard_package["trial_balance"]["balance_source_code"], "snapshot")
        self.assertIn("snapshot_record_id", hard_package["trial_balance"])
        self.assertEqual(hard_package["financial_statement_package"], hard_package_statements)
        self.assertEqual(hard_aging_status, 200)
        self.assertEqual(hard_package["receivable_aging"], hard_aging)
        self.assertEqual(hard_payable_status, 200)
        self.assertEqual(hard_package["payable_aging"], hard_payable)
        self.assertEqual(
            hard_package["payable_aging"]["as_of_date"],
            hard_package["receivable_aging"]["as_of_date"],
        )
        self.assertEqual(hard_balance_status, 200)
        hard_aging_total = Decimal(
            str(hard_package["receivable_aging"]["total_outstanding_amount"])
        )
        self.assertEqual(hard_aging_total, Decimal(str(hard_aging["total_outstanding_amount"])))
        self.assertEqual(hard_aging_total, self._trial_balance_account_net(hard_trial, "110100"))
        self.assertEqual(hard_aging_total, self._account_balance_net(hard_balances, "110100"))
        self.assertEqual(
            Decimal(str(hard_package["payable_aging"]["total_outstanding_amount"])),
            Decimal(str(hard_payable["total_outstanding_amount"])),
        )
        self.assertEqual(closes_status, 200)
        self.assertIsNotNone(hard_package["period_close"])
        self.assertEqual(hard_package["period_close"], closes["period_closes"][-1])

        explicit_period_status, explicit_period = self._http_period_close_package(
            statement_scope_code="period"
        )
        ytd_status, ytd = self._http_period_close_package(statement_scope_code="year_to_date")
        ytd_library = lookup_period_close_package(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
            statement_scope_code="year_to_date",
        )
        ytd_statements = self._http_financial_statement_package(
            statement_scope_code="year_to_date"
        )[1]
        compare_status, compared = self._http_period_close_package(
            comparison_fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07",
        )
        compare_statements = self._http_financial_statement_package(
            comparison_fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-07",
        )[1]
        self.assertEqual(explicit_period_status, 200)
        self.assertNotIn("statement_scope_code", explicit_period)
        self.assertEqual(
            explicit_period["financial_statement_package"],
            hard_package["financial_statement_package"],
        )
        self.assertEqual(ytd_status, 200)
        self.assertEqual(ytd, ytd_library)
        self.assertNotIn("statement_scope_code", ytd)
        self.assertEqual(ytd["financial_statement_package"], ytd_statements)
        self.assertEqual(ytd["financial_statement_package"]["statement_scope_code"], "year_to_date")
        self.assertEqual(ytd["trial_balance"], hard_package["trial_balance"])
        self.assertEqual(ytd["receivable_aging"], hard_package["receivable_aging"])
        self.assertEqual(ytd["payable_aging"], hard_package["payable_aging"])
        self.assertEqual(ytd["period_close"], hard_package["period_close"])
        self.assertEqual(compare_status, 200)
        self.assertNotIn("comparison_fiscal_period_reference", compared)
        self.assertEqual(compared["financial_statement_package"], compare_statements)
        self.assertIn(
            "comparison_statement_lines",
            compared["financial_statement_package"]["income_statement"],
        )
        self.assertEqual(compared["trial_balance"], hard_package["trial_balance"])
        self.assertEqual(compared["receivable_aging"], hard_package["receivable_aging"])
        self.assertEqual(compared["payable_aging"], hard_package["payable_aging"])

        missing_query = self._http_json("GET", "/period-close-packages", None)
        missing_book = self._http_json(
            "GET",
            "/period-close-packages?"
            + urllib.parse.urlencode(
                {
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
                }
            ),
            None,
        )
        post_status, _post = self._http_json("POST", "/period-close-packages", {})
        bad_scope = self._http_period_close_package(statement_scope_code="life_to_date")
        unknown_period = self._http_period_close_package(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01"
        )
        unknown_entity = self._http_period_close_package(
            legal_entity_reference="urn:cwl:legal_entity:missing"
        )
        unknown_book = self._http_period_close_package(
            book_reference="urn:cwl:accounting:book:missing"
        )
        missing_header = self._http_period_close_package(tenant_header=None)
        cross_status, _cross = self._http_period_close_package(
            tenant_header="urn:cwl:tenant_other"
        )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            lookup_period_close_package(
                DATABASE_URL,
                self.policy.tenant_reference,
                "",
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "book_reference"):
            lookup_period_close_package(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                "",
                "urn:cwl:accounting:fiscal_period:2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "fiscal_period_reference"):
            lookup_period_close_package(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "",
            )
        with self.assertRaisesRegex(AccountingValidationError, "statement_scope_code"):
            lookup_period_close_package(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
                statement_scope_code="life_to_date",
            )

        self.assertEqual(missing_query[0], 400)
        self.assertEqual(missing_book[0], 400)
        self.assertEqual(post_status, 405)
        self.assertEqual(bad_scope[0], 400)
        self.assertEqual(unknown_period[0], 404)
        self.assertEqual(unknown_entity[0], 404)
        self.assertEqual(unknown_book[0], 404)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(
            self._count_table("accounting_core.general_journal"),
            journals_before + 6,
        )
        server.shutdown()

    def test_period_close_package_resists_interleaved_post(self) -> None:
        """One close-package read cannot tear aging from trial balance after a concurrent post."""
        opening_receipt = self.ledger.post_proposal(
            ingest_journal_proposal(self._billing_validated_payload())
        )
        completed = {"count": 0}
        original_session = PostgresPostingLedger._session

        @contextmanager
        def interleaved_session(ledger: PostgresPostingLedger) -> object:
            with original_session(ledger) as connection:
                yield connection
            completed["count"] += 1
            if completed["count"] != 2:
                return
            accept_journal_proposal(
                self._billing_taxed_payload(),
                DATABASE_URL,
                self.policy.tenant_reference,
            )

        self._start_http_server()
        with mock.patch.object(PostgresPostingLedger, "_session", interleaved_session):
            assembled = lookup_period_close_package(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
            )
        later_status, later = self._http_period_close_package()

        opening_accounts_receivable = Decimal("25000")
        combined_accounts_receivable = Decimal("52500")
        combined_tax_payable = Decimal("2500")
        self.assertEqual(opening_receipt.posting_status_code, "posted")
        self.assertTrue(opening_receipt.journal_reference.startswith("urn:cwl:"))
        self._assert_period_close_package_worksheets_agree(assembled)
        self.assertEqual(
            Decimal(str(assembled["receivable_aging"]["total_outstanding_amount"])),
            opening_accounts_receivable,
        )
        self.assertEqual(
            Decimal(str(assembled["payable_aging"]["total_outstanding_amount"])),
            Decimal("0"),
        )
        self.assertEqual(
            Decimal(str(assembled["unapplied_cash_rollforward"]["closing_amount"])),
            Decimal("0"),
        )
        self.assertEqual(later_status, 200)
        self._assert_period_close_package_worksheets_agree(later)
        if completed["count"] >= 2:
            self.assertEqual(
                Decimal(str(later["receivable_aging"]["total_outstanding_amount"])),
                combined_accounts_receivable,
            )
            self.assertEqual(
                Decimal(str(later["payable_aging"]["total_outstanding_amount"])),
                combined_tax_payable,
            )
        else:
            self.assertEqual(
                Decimal(str(later["receivable_aging"]["total_outstanding_amount"])),
                opening_accounts_receivable,
            )

    def test_http_reads_and_publishes_outbox_events(self) -> None:
        """GET lists unpublished outbox rows; POST publish marks one row idempotently."""
        invoice = self._billing_validated_payload()
        cash = self._billing_cash_payload()
        server = self._start_http_server()
        outbox_before = self._count_table("accounting_integration.outbox_event")

        empty_status, empty_page = self._http_outbox_events("posting_receipt")
        library_empty = lookup_outbox_events(
            DATABASE_URL, self.policy.tenant_reference, "posting_receipt"
        )
        self.assertEqual(empty_status, 200)
        self.assertEqual(empty_page, library_empty)
        self.assertEqual(empty_page["outbox_events"], [])
        self.assertIsNone(empty_page["next_cursor"])
        self.assertEqual(empty_page["event_type_code"], "posting_receipt")
        self.assertEqual(empty_page["tenant_reference"], self.policy.tenant_reference)

        post_status, _receipt = self._http_json("POST", "/journal-proposals", invoice)
        cash_status, _cash_receipt = self._http_json("POST", "/journal-proposals", cash)
        lookup_status, lookup = self._http_lookup(str(invoice["idempotency_key"]))
        listed_status, listed = self._http_outbox_events("posting_receipt")
        first_page_status, first_page = self._http_outbox_events("posting_receipt", page_limit=1)
        second_page_status, second_page = self._http_outbox_events(
            "posting_receipt",
            page_limit=1,
            cursor=str(first_page["next_cursor"]),
        )

        self.assertEqual(post_status, 200)
        self.assertEqual(cash_status, 200)
        self.assertEqual(lookup_status, 200)
        self.assertEqual(listed_status, 200)
        self.assertEqual(first_page_status, 200)
        self.assertEqual(second_page_status, 200)
        self.assertEqual(len(listed["outbox_events"]), 2)
        self.assertIsNone(listed["next_cursor"])
        self.assertEqual(lookup["idempotency_key"], invoice["idempotency_key"])
        self.assertTrue(lookup["receipt_id"])
        self.assertEqual(
            {item["event_type_code"] for item in listed["outbox_events"]},
            {"posting_receipt"},
        )
        self.assertTrue(
            all(
                str(item["payload_reference"]).startswith("urn:cwl:accounting:posting_receipt:")
                for item in listed["outbox_events"]
            )
        )
        self.assertTrue(all(str(item["payload_hash"]).startswith("sha256:") for item in listed["outbox_events"]))
        self.assertTrue(all(item["aggregate_reference"] for item in listed["outbox_events"]))
        self.assertTrue(all(item["created_at"] for item in listed["outbox_events"]))
        self.assertEqual(len(first_page["outbox_events"]), 1)
        self.assertTrue(first_page["next_cursor"])
        self.assertEqual(len(second_page["outbox_events"]), 1)
        self.assertIsNone(second_page["next_cursor"])
        self.assertEqual(
            [item["outbox_event_id"] for item in first_page["outbox_events"]]
            + [item["outbox_event_id"] for item in second_page["outbox_events"]],
            [item["outbox_event_id"] for item in listed["outbox_events"]],
        )

        reverse_status, reversing_receipt = self._http_json(
            "POST",
            "/journal-reversals",
            {
                "tenant_reference": self.policy.tenant_reference,
                "idempotency_key": invoice["idempotency_key"],
                "reversal_date": "2026-08-31",
                "reversal_reason_code": "billing_correction",
            },
        )
        close_status, _close = self._http_json(
            "POST",
            "/period-closes",
            self._period_close_payload(),
        )
        reversal_list_status, reversal_list = self._http_outbox_events("journal_reversal")
        close_list_status, close_list = self._http_outbox_events("period_close")
        still_posted_status, still_posted = self._http_outbox_events("posting_receipt")

        self.assertEqual(reverse_status, 200)
        self.assertEqual(close_status, 200)
        self.assertEqual(reversal_list_status, 200)
        self.assertEqual(close_list_status, 200)
        self.assertEqual(still_posted_status, 200)
        self.assertEqual(len(reversal_list["outbox_events"]), 1)
        self.assertEqual(reversal_list["outbox_events"][0]["event_type_code"], "journal_reversal")
        self.assertEqual(
            reversal_list["outbox_events"][0]["aggregate_reference"],
            reversing_receipt["journal_reference"],
        )
        self.assertEqual(len(close_list["outbox_events"]), 1)
        self.assertEqual(close_list["outbox_events"][0]["event_type_code"], "period_close")
        self.assertEqual(len(still_posted["outbox_events"]), 2)

        target = listed["outbox_events"][0]
        publish_status, published = self._http_publish_outbox(str(target["outbox_event_id"]))
        replay_status, replayed = self._http_publish_outbox(str(target["outbox_event_id"]))
        after_publish_status, after_publish = self._http_outbox_events("posting_receipt")
        library_published = publish_outbox_event(
            DATABASE_URL,
            self.policy.tenant_reference,
            str(target["outbox_event_id"]),
        )

        self.assertEqual(publish_status, 200)
        self.assertEqual(replay_status, 200)
        self.assertEqual(after_publish_status, 200)
        self.assertEqual(published, replayed)
        self.assertEqual(published, library_published)
        self.assertEqual(published["outbox_event_id"], target["outbox_event_id"])
        self.assertEqual(published["event_type_code"], "posting_receipt")
        self.assertTrue(published["published_at"])
        self.assertEqual(len(after_publish["outbox_events"]), 1)
        self.assertNotIn(
            target["outbox_event_id"],
            {item["outbox_event_id"] for item in after_publish["outbox_events"]},
        )

        missing_type = self._http_json("GET", "/outbox-events", None)
        unknown_type = self._http_outbox_events("not_an_event")
        bad_limit = self._http_outbox_events("posting_receipt", page_limit="abc")
        high_limit = self._http_outbox_events("posting_receipt", page_limit=101)
        bad_cursor = self._http_outbox_events("posting_receipt", cursor="not-a-cursor")
        post_list = self._http_json("POST", "/outbox-events", {})
        get_publish = self._http_json(
            "GET",
            f"/outbox-events/{target['outbox_event_id']}/publish",
            None,
        )
        unknown_publish = self._http_publish_outbox(str(uuid.uuid4()))
        missing_header = self._http_outbox_events("posting_receipt", tenant_header=None)
        cross_get = self._http_outbox_events(
            "posting_receipt", tenant_header="urn:cwl:tenant_other"
        )
        cross_publish = self._http_publish_outbox(
            str(target["outbox_event_id"]),
            tenant_header="urn:cwl:tenant_other",
        )
        with self.assertRaisesRegex(AccountingValidationError, "event_type_code"):
            lookup_outbox_events(DATABASE_URL, self.policy.tenant_reference, "")
        with self.assertRaisesRegex(AccountingValidationError, "event_type_code"):
            lookup_outbox_events(DATABASE_URL, self.policy.tenant_reference, "not_an_event")
        with self.assertRaisesRegex(AccountingValidationError, "page_limit"):
            lookup_outbox_events(
                DATABASE_URL, self.policy.tenant_reference, "posting_receipt", page_limit=0
            )
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            lookup_outbox_events(
                DATABASE_URL, self.policy.tenant_reference, "posting_receipt", cursor="2026-08-31"
            )
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            lookup_outbox_events(
                DATABASE_URL,
                self.policy.tenant_reference,
                "posting_receipt",
                cursor="|01900000-0000-7000-8000-000000000001",
            )
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            lookup_outbox_events(
                DATABASE_URL,
                self.policy.tenant_reference,
                "posting_receipt",
                cursor="2026-08-31T00:00:00Z|",
            )
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            lookup_outbox_events(
                DATABASE_URL,
                self.policy.tenant_reference,
                "posting_receipt",
                cursor="not-a-time|01900000-0000-7000-8000-000000000001",
            )
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            lookup_outbox_events(
                DATABASE_URL,
                self.policy.tenant_reference,
                "posting_receipt",
                cursor="2026-08-31T00:00:00Z|not-a-uuid",
            )
        with self.assertRaisesRegex(AccountingValidationError, "outbox_event_id"):
            publish_outbox_event(DATABASE_URL, self.policy.tenant_reference, "not-a-uuid")
        with self.assertRaisesRegex(AccountingValidationError, "outbox event"):
            publish_outbox_event(DATABASE_URL, self.policy.tenant_reference, str(uuid.uuid4()))
        with self.assertRaisesRegex(AccountingValidationError, "outbox_event_id"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).publish_outbox_event("")

        self.assertEqual(missing_type[0], 400)
        self.assertEqual(unknown_type[0], 400)
        self.assertEqual(bad_limit[0], 400)
        self.assertEqual(high_limit[0], 400)
        self.assertEqual(bad_cursor[0], 400)
        self.assertEqual(post_list[0], 405)
        self.assertEqual(get_publish[0], 405)
        self.assertEqual(unknown_publish[0], 404)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_get[0], 403)
        self.assertEqual(cross_publish[0], 403)
        self.assertEqual(self._count_table("accounting_integration.outbox_event"), outbox_before + 4)
        self.assertEqual(self._count_outbox("posting_receipt"), 2)
        self.assertEqual(self._count_outbox("journal_reversal"), 1)
        self.assertEqual(self._count_outbox("period_close"), 1)
        server.shutdown()

    def test_http_reads_audit_event_history_without_publishing(self) -> None:
        """GET /audit-events lists published and unpublished outbox rows and never publishes."""
        invoice = self._billing_validated_payload()
        server = self._start_http_server()
        outbox_before = self._count_table("accounting_integration.outbox_event")

        empty_status, empty_page = self._http_audit_events()
        empty_library = lookup_audit_events(DATABASE_URL, self.policy.tenant_reference)
        self.assertEqual(empty_status, 200)
        self.assertEqual(empty_page, empty_library)
        self.assertEqual(empty_page["audit_events"], [])
        self.assertIsNone(empty_page["next_cursor"])
        self.assertEqual(empty_page["tenant_reference"], self.policy.tenant_reference)
        self.assertNotIn("event_type_code", empty_page)

        post_status, _receipt = self._http_json("POST", "/journal-proposals", invoice)
        close_status, _close = self._http_json(
            "POST", "/period-closes", self._period_close_payload()
        )
        history_status, history = self._http_audit_events()
        library = lookup_audit_events(DATABASE_URL, self.policy.tenant_reference)
        persist = PostgresPostingLedger(
            DATABASE_URL, self.policy.tenant_reference
        ).load_audit_events()
        typed_status, typed = self._http_audit_events(event_type_code="period_close")
        first_page_status, first_page = self._http_audit_events(page_limit=1)
        second_page_status, second_page = self._http_audit_events(
            page_limit=1,
            cursor=str(first_page["next_cursor"]),
        )
        drain_status, drain = self._http_outbox_events("posting_receipt")
        by_type = {str(item["event_type_code"]) for item in history["audit_events"]}
        receipt_event = next(
            item
            for item in history["audit_events"]
            if item["event_type_code"] == "posting_receipt"
        )

        self.assertEqual(post_status, 200)
        self.assertEqual(close_status, 200)
        self.assertEqual(history_status, 200)
        self.assertEqual(history, library)
        self.assertEqual(history, persist)
        self.assertEqual(by_type, {"posting_receipt", "period_close"})
        self.assertEqual(len(history["audit_events"]), 2)
        self.assertTrue(all("published_at" in item for item in history["audit_events"]))
        self.assertTrue(all(item["published_at"] is None for item in history["audit_events"]))
        self.assertTrue(
            str(receipt_event["payload_reference"]).startswith(
                "urn:cwl:accounting:posting_receipt:"
            )
        )
        self.assertEqual(typed_status, 200)
        self.assertEqual(typed["event_type_code"], "period_close")
        self.assertEqual(len(typed["audit_events"]), 1)
        self.assertEqual(typed["audit_events"][0]["event_type_code"], "period_close")
        self.assertEqual(first_page_status, 200)
        self.assertEqual(second_page_status, 200)
        self.assertEqual(len(first_page["audit_events"]), 1)
        self.assertTrue(first_page["next_cursor"])
        self.assertEqual(len(second_page["audit_events"]), 1)
        self.assertIsNone(second_page["next_cursor"])
        self.assertEqual(
            [item["outbox_event_id"] for item in first_page["audit_events"]]
            + [item["outbox_event_id"] for item in second_page["audit_events"]],
            [item["outbox_event_id"] for item in history["audit_events"]],
        )
        typed_paged_status, typed_paged = self._http_audit_events(
            event_type_code="period_close",
            page_limit=1,
            cursor=str(first_page["next_cursor"]),
        )
        self.assertEqual(typed_paged_status, 200)
        self.assertTrue(
            all(item["event_type_code"] == "period_close" for item in typed_paged["audit_events"])
        )
        self.assertEqual(drain_status, 200)
        self.assertEqual(len(drain["outbox_events"]), 1)

        publish_status, published = self._http_publish_outbox(
            str(receipt_event["outbox_event_id"])
        )
        after_audit_status, after_audit = self._http_audit_events()
        after_drain_status, after_drain = self._http_outbox_events("posting_receipt")
        after_receipt = next(
            item
            for item in after_audit["audit_events"]
            if item["outbox_event_id"] == receipt_event["outbox_event_id"]
        )
        self.assertEqual(publish_status, 200)
        self.assertEqual(after_audit_status, 200)
        self.assertEqual(after_drain_status, 200)
        self.assertEqual(len(after_audit["audit_events"]), 2)
        self.assertTrue(after_receipt["published_at"])
        self.assertEqual(after_receipt["published_at"], published["published_at"])
        self.assertEqual(after_drain["outbox_events"], [])
        self.assertEqual(self._count_table("accounting_integration.outbox_event"), outbox_before + 2)

        post_status_code, _post_body = self._http_json("POST", "/audit-events", {})
        missing_header = self._http_audit_events(tenant_header=None)
        cross_status, _cross = self._http_audit_events(tenant_header="urn:cwl:tenant_other")
        unknown_type = self._http_audit_events(event_type_code="not_an_event")
        bad_limit = self._http_audit_events(page_limit="abc")
        high_limit = self._http_audit_events(page_limit=101)
        bad_cursor = self._http_audit_events(cursor="not-a-cursor")
        with self.assertRaisesRegex(AccountingValidationError, "event_type_code"):
            lookup_audit_events(
                DATABASE_URL, self.policy.tenant_reference, event_type_code="not_an_event"
            )
        with self.assertRaisesRegex(AccountingValidationError, "page_limit"):
            lookup_audit_events(
                DATABASE_URL, self.policy.tenant_reference, page_limit=0
            )

        self.assertEqual(post_status_code, 405)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(unknown_type[0], 400)
        self.assertEqual(bad_limit[0], 400)
        self.assertEqual(high_limit[0], 400)
        self.assertEqual(bad_cursor[0], 400)
        self.assertEqual(self._count_table("accounting_integration.outbox_event"), outbox_before + 2)
        server.shutdown()

    def test_http_opens_fiscal_period_and_accepts_later_posts(self) -> None:
        """HTTP opens the next fiscal period, replays the open, and still closes over HTTP."""
        server = self._start_http_server()
        periods_before = self._count_table("accounting_core.fiscal_period")
        open_body = self._period_open_payload()
        september = self._september_invoice_payload()

        seeded_status, seeded = self._http_fiscal_period()
        open_status, opened = self._http_json("POST", "/fiscal-periods", open_body)
        replay_status, replayed = self._http_json("POST", "/fiscal-periods", open_body)
        get_status, document = self._http_fiscal_period(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-09"
        )
        library = lookup_fiscal_period(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            "urn:cwl:accounting:fiscal_period:2026-09",
        )
        library_open = accept_period_open(open_body, DATABASE_URL, self.policy.tenant_reference)
        post_status, posted = self._http_json("POST", "/journal-proposals", september)
        close_status, closed = self._http_json(
            "POST",
            "/period-closes",
            self._period_close_payload(
                fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-09"
            ),
        )

        self.assertEqual(seeded_status, 200)
        self.assertEqual(seeded["period_code"], "2026-08")
        self.assertEqual(seeded["period_status_code"], "open")
        self.assertEqual(seeded["period_start_date"], "2026-08-01")
        self.assertEqual(seeded["period_end_date"], "2026-08-31")
        self.assertEqual(open_status, 200)
        self.assertFalse(opened["replayed"])
        self.assertEqual(opened["period_code"], "2026-09")
        self.assertEqual(opened["period_status_code"], "open")
        self.assertEqual(opened["period_start_date"], "2026-09-01")
        self.assertEqual(opened["period_end_date"], "2026-09-30")
        self.assertEqual(replay_status, 200)
        self.assertTrue(replayed["replayed"])
        self.assertEqual(replayed["period_code"], opened["period_code"])
        self.assertEqual(self._count_table("accounting_core.fiscal_period"), periods_before + 1)
        self.assertEqual(get_status, 200)
        self.assertEqual(document, library)
        self.assertEqual(document["period_status_code"], "open")
        self.assertEqual(document["period_start_date"], "2026-09-01")
        self.assertEqual(document["period_end_date"], "2026-09-30")
        self.assertTrue(library_open["replayed"])
        self.assertEqual(post_status, 200)
        self.assertEqual(posted["posting_status_code"], "posted")
        self.assertEqual(close_status, 200)
        self.assertEqual(closed["period_code"], "2026-09")
        self.assertEqual(closed["period_status_code"], "hard_closed")
        self.assertEqual(self._period_status("2026-09"), "hard_closed")

        august_close = self._http_json("POST", "/period-closes", self._period_close_payload())
        hard_open = self._http_json(
            "POST",
            "/fiscal-periods",
            self._period_open_payload(
                fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-08"
            ),
        )
        soft_body = self._period_open_payload(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-10",
            period_start_date="2026-10-01",
            period_end_date="2026-10-31",
        )
        soft_open = self._http_json("POST", "/fiscal-periods", soft_body)
        soft_close = accept_period_close(
            self._period_close_payload(
                fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-10",
                period_status_code="soft_closed",
            ),
            DATABASE_URL,
            self.policy.tenant_reference,
        )
        soft_reopen = self._http_json("POST", "/fiscal-periods", soft_body)
        missing_header = self._http_json(
            "POST", "/fiscal-periods", open_body, tenant_header=None
        )
        missing_get_header = self._http_fiscal_period(tenant_header=None)
        bad_json = self._http_raw(
            "POST", "/fiscal-periods", b"{", self.policy.tenant_reference
        )
        cross_open = self._http_json(
            "POST",
            "/fiscal-periods",
            open_body,
            tenant_header="urn:cwl:tenant_other",
        )
        cross_get = self._http_fiscal_period(tenant_header="urn:cwl:tenant_other")
        missing_query = self._http_json("GET", "/fiscal-periods", None)
        missing_period_query = self._http_json(
            "GET",
            "/fiscal-periods?"
            + urllib.parse.urlencode(
                {"legal_entity_reference": self.policy.legal_entity_reference}
            ),
            None,
        )
        body_mismatch = self._http_json(
            "POST",
            "/fiscal-periods",
            {**open_body, "tenant_reference": "urn:cwl:tenant_other"},
        )
        unknown_entity = self._http_fiscal_period(
            legal_entity_reference="urn:cwl:legal_entity:missing"
        )
        unknown_period = self._http_fiscal_period(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01"
        )
        missing_dates = self._http_json(
            "POST",
            "/fiscal-periods",
            {
                "tenant_reference": self.policy.tenant_reference,
                "legal_entity_reference": self.policy.legal_entity_reference,
                "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-11",
            },
        )
        with self.assertRaisesRegex(AccountingValidationError, "JSON object"):
            accept_period_open(["not-an-object"], DATABASE_URL, self.policy.tenant_reference)
        with self.assertRaisesRegex(AccountingValidationError, "bound tenant"):
            accept_period_open(
                {**open_body, "tenant_reference": "urn:cwl:tenant_other"},
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "fiscal_period_reference"):
            accept_period_open(
                {"tenant_reference": self.policy.tenant_reference},
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "period_start_date"):
            accept_period_open(
                {
                    "tenant_reference": self.policy.tenant_reference,
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-11",
                    "period_start_date": "01-11-2026",
                    "period_end_date": "2026-11-30",
                },
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "period_end_date"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).open_fiscal_period(
                self.policy.legal_entity_reference,
                "2026-11",
                date(2026, 11, 30),
                date(2026, 11, 1),
            )
        with self.assertRaisesRegex(AccountingValidationError, "fiscal_period_reference"):
            lookup_fiscal_period(DATABASE_URL, self.policy.tenant_reference, "", "")
        with self.assertRaisesRegex(AccountingValidationError, "period_end_date"):
            accept_period_open(
                {
                    "tenant_reference": self.policy.tenant_reference,
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-11",
                    "period_start_date": "2026-11-01",
                    "period_end_date": "30-11-2026",
                },
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "period_start_date"):
            accept_period_open(
                {
                    "tenant_reference": self.policy.tenant_reference,
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "period_code": "2026-11",
                    "period_end_date": "2026-11-30",
                },
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "fiscal_period"):
            lookup_fiscal_period(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                "urn:cwl:accounting:fiscal_period:1999-01",
            )
        with self.assertRaisesRegex(AccountingValidationError, "fiscal_period_reference"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_fiscal_period("", "")
        with self.assertRaisesRegex(AccountingValidationError, "fiscal_period_reference"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).open_fiscal_period("", "")
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).open_fiscal_period(
                "urn:cwl:legal_entity:missing",
                "2026-12",
                date(2026, 12, 1),
                date(2026, 12, 31),
            )
        bare_tenant, bare_entity = self._seed_tenant_without_calendar()
        with self.assertRaisesRegex(AccountingValidationError, "fiscal_calendar"):
            PostgresPostingLedger(DATABASE_URL, bare_tenant).open_fiscal_period(
                bare_entity,
                "2026-12",
                date(2026, 12, 1),
                date(2026, 12, 31),
            )

        self.assertEqual(august_close[0], 200)
        self.assertEqual(hard_open[0], 422)
        self.assertIn("hard_closed", str(hard_open[1]))
        self.assertEqual(soft_open[0], 200)
        self.assertEqual(soft_close["period_status_code"], "soft_closed")
        self.assertEqual(soft_reopen[0], 422)
        self.assertIn("soft_closed", str(soft_reopen[1]))
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(missing_get_header[0], 400)
        self.assertEqual(bad_json[0], 400)
        self.assertEqual(cross_open[0], 403)
        self.assertEqual(cross_get[0], 403)
        self.assertEqual(missing_query[0], 400)
        self.assertEqual(missing_period_query[0], 200)
        self.assertEqual(missing_period_query[1]["fiscal_periods"][0]["period_code"], "2026-08")
        self.assertEqual(body_mismatch[0], 403)
        self.assertEqual(unknown_entity[0], 404)
        self.assertEqual(unknown_period[0], 404)
        self.assertEqual(missing_dates[0], 422)
        self.assertEqual(self._count_table("accounting_core.fiscal_period"), periods_before + 2)
        server.shutdown()

    def test_http_lists_fiscal_periods_from_existing_rows(self) -> None:
        """GET lists existing fiscal_period rows for one tenant entity without inventing a table."""
        server = self._start_http_server()
        periods_before = self._count_table("accounting_core.fiscal_period")
        bare_tenant, bare_entity = self._seed_tenant_without_calendar()

        empty_library = lookup_fiscal_periods(DATABASE_URL, bare_tenant, bare_entity)
        listed_before_status, listed_before = self._http_fiscal_periods()
        library_before = lookup_fiscal_periods(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
        )
        single_before_status, single_before = self._http_fiscal_period()

        self.assertEqual(empty_library["fiscal_periods"], [])
        self.assertIsNone(empty_library["next_cursor"])
        self.assertEqual(empty_library["legal_entity_reference"], bare_entity)
        self.assertEqual(listed_before_status, 200)
        self.assertEqual(listed_before, library_before)
        self.assertEqual(len(listed_before["fiscal_periods"]), 1)
        self.assertEqual(listed_before["fiscal_periods"][0]["period_code"], "2026-08")
        self.assertEqual(listed_before["fiscal_periods"][0]["period_status_code"], "open")
        self.assertEqual(
            listed_before["fiscal_periods"][0]["fiscal_period_reference"],
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        self.assertEqual(listed_before["fiscal_periods"][0]["period_start_date"], "2026-08-01")
        self.assertEqual(listed_before["fiscal_periods"][0]["period_end_date"], "2026-08-31")
        self.assertIsNone(listed_before["next_cursor"])
        self.assertEqual(single_before_status, 200)
        self.assertEqual(single_before["period_code"], "2026-08")
        self.assertNotIn("fiscal_periods", single_before)

        open_status, opened = self._http_json("POST", "/fiscal-periods", self._period_open_payload())
        close_status, _closed = self._http_json("POST", "/period-closes", self._period_close_payload())
        listed_status, listed = self._http_fiscal_periods()
        first_page_status, first_page = self._http_fiscal_periods(page_limit=1)
        second_page_status, second_page = self._http_fiscal_periods(
            page_limit=1,
            cursor=str(first_page["next_cursor"]),
        )
        single_after_status, single_after = self._http_fiscal_period(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-08"
        )
        single_open_status, single_open = self._http_fiscal_period(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-09"
        )
        by_code = {str(item["period_code"]): item for item in listed["fiscal_periods"]}

        self.assertEqual(open_status, 200)
        self.assertEqual(opened["period_code"], "2026-09")
        self.assertEqual(opened["period_status_code"], "open")
        self.assertEqual(close_status, 200)
        self.assertEqual(listed_status, 200)
        self.assertEqual(first_page_status, 200)
        self.assertEqual(second_page_status, 200)
        self.assertEqual(single_after_status, 200)
        self.assertEqual(single_open_status, 200)
        self.assertEqual(len(listed["fiscal_periods"]), 2)
        self.assertIsNone(listed["next_cursor"])
        self.assertEqual(listed["tenant_reference"], self.policy.tenant_reference)
        self.assertEqual(listed["legal_entity_reference"], self.policy.legal_entity_reference)
        self.assertEqual(by_code["2026-08"]["period_status_code"], "hard_closed")
        self.assertEqual(by_code["2026-09"]["period_status_code"], "open")
        self.assertEqual(by_code["2026-09"]["period_start_date"], "2026-09-01")
        self.assertEqual(by_code["2026-09"]["period_end_date"], "2026-09-30")
        self.assertEqual(len(first_page["fiscal_periods"]), 1)
        self.assertTrue(first_page["next_cursor"])
        self.assertEqual(len(second_page["fiscal_periods"]), 1)
        self.assertIsNone(second_page["next_cursor"])
        self.assertEqual(
            [item["period_code"] for item in first_page["fiscal_periods"]]
            + [item["period_code"] for item in second_page["fiscal_periods"]],
            [item["period_code"] for item in listed["fiscal_periods"]],
        )
        self.assertEqual(single_after["period_status_code"], "hard_closed")
        self.assertEqual(single_open["period_status_code"], "open")
        self.assertEqual(single_open["period_code"], "2026-09")

        missing_entity_query = self._http_json("GET", "/fiscal-periods", None)
        period_only = self._http_json(
            "GET",
            "/fiscal-periods?"
            + urllib.parse.urlencode(
                {"fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08"}
            ),
            None,
        )
        unknown_entity = self._http_fiscal_periods(
            legal_entity_reference="urn:cwl:legal_entity:missing"
        )
        bad_limit = self._http_fiscal_periods(page_limit="abc")
        high_limit = self._http_fiscal_periods(page_limit=101)
        bad_cursor = self._http_fiscal_periods(cursor="not-a-cursor")
        missing_header = self._http_fiscal_periods(tenant_header=None)
        cross_status, _cross = self._http_fiscal_periods(tenant_header="urn:cwl:tenant_other")
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            lookup_fiscal_periods(DATABASE_URL, self.policy.tenant_reference, "")
        with self.assertRaisesRegex(AccountingValidationError, "page_limit"):
            lookup_fiscal_periods(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                page_limit=0,
            )
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            lookup_fiscal_periods(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                cursor="2026-08-01",
            )
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            lookup_fiscal_periods(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                cursor="|2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            lookup_fiscal_periods(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                cursor="2026-08-01|",
            )
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            lookup_fiscal_periods(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                cursor="not-a-date|2026-08",
            )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity"):
            lookup_fiscal_periods(
                DATABASE_URL,
                self.policy.tenant_reference,
                "urn:cwl:legal_entity:missing",
            )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_fiscal_periods("")

        self.assertEqual(missing_entity_query[0], 400)
        self.assertEqual(period_only[0], 400)
        self.assertEqual(unknown_entity[0], 404)
        self.assertEqual(bad_limit[0], 400)
        self.assertEqual(high_limit[0], 400)
        self.assertEqual(bad_cursor[0], 400)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(self._count_table("accounting_core.fiscal_period"), periods_before + 1)
        server.shutdown()

    def test_http_reads_account_ledger_from_existing_lines(self) -> None:
        """GET lists posted journal lines for one statutory chart account without SQL."""
        invoice = self._billing_validated_payload()
        cash = self._billing_cash_payload()
        credit = self._billing_credit_payload()
        taxed = self._billing_taxed_payload()
        taxed_credit = self._billing_taxed_credit_payload()
        september = self._september_invoice_payload()
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")

        empty_status, empty_page = self._http_account_ledger("110200")
        library_empty = lookup_account_ledger(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            "110200",
        )
        self.assertEqual(empty_status, 200)
        self.assertEqual(empty_page, library_empty)
        self.assertEqual(empty_page["ledger_lines"], [])
        self.assertIsNone(empty_page["next_cursor"])
        self.assertIsNone(empty_page["fiscal_period_reference"])
        self.assertEqual(empty_page["chart_account_code"], "110200")
        self.assertEqual(Decimal(str(empty_page["period_debit_total"])), Decimal("0"))
        self.assertEqual(Decimal(str(empty_page["period_credit_total"])), Decimal("0"))

        invoice_status, invoice_receipt = self._http_json("POST", "/journal-proposals", invoice)
        cash_status, cash_receipt = self._http_json("POST", "/journal-proposals", cash)
        credit_status, credit_receipt = self._http_json("POST", "/journal-proposals", credit)
        journal_status, journal = self._http_journal(
            idempotency_key=str(invoice["idempotency_key"])
        )
        ar_status, ar_ledger = self._http_account_ledger("110100")
        ar_line = next(
            item
            for item in journal["lines"]
            if item["chart_account_code"] == "110100"
        )
        ar_by_journal = {
            str(item["journal_reference"]): item for item in ar_ledger["ledger_lines"]
        }

        self.assertEqual(invoice_status, 200)
        self.assertEqual(cash_status, 200)
        self.assertEqual(credit_status, 200)
        self.assertEqual(journal_status, 200)
        self.assertEqual(ar_status, 200)
        self.assertEqual(len(ar_ledger["ledger_lines"]), 3)
        self.assertEqual(ar_ledger["chart_account_code"], "110100")
        self.assertEqual(
            {item["journal_reference"] for item in ar_ledger["ledger_lines"]},
            {
                invoice_receipt["journal_reference"],
                cash_receipt["journal_reference"],
                credit_receipt["journal_reference"],
            },
        )
        self.assertEqual(ar_by_journal[invoice_receipt["journal_reference"]]["line_number"], ar_line["line_number"])
        self.assertEqual(
            ar_by_journal[invoice_receipt["journal_reference"]]["account_role_code"],
            ar_line["account_role_code"],
        )
        self.assertEqual(
            ar_by_journal[invoice_receipt["journal_reference"]]["debit_amount"],
            ar_line["debit_amount"],
        )
        self.assertEqual(
            ar_by_journal[invoice_receipt["journal_reference"]]["credit_amount"],
            ar_line["credit_amount"],
        )
        self.assertTrue(ar_by_journal[invoice_receipt["journal_reference"]]["posted_at"])
        self.assertEqual(Decimal(str(ar_ledger["period_debit_total"])), Decimal("25000"))
        self.assertEqual(Decimal(str(ar_ledger["period_credit_total"])), Decimal("22000"))

        taxed_status, taxed_receipt = self._http_json("POST", "/journal-proposals", taxed)
        taxed_credit_status, taxed_credit_receipt = self._http_json(
            "POST", "/journal-proposals", taxed_credit
        )
        tax_status, tax_ledger = self._http_account_ledger("210100")
        tax_by_journal = {
            str(item["journal_reference"]): item for item in tax_ledger["ledger_lines"]
        }

        self.assertEqual(taxed_status, 200)
        self.assertEqual(taxed_credit_status, 200)
        self.assertEqual(tax_status, 200)
        self.assertEqual(len(tax_ledger["ledger_lines"]), 2)
        self.assertEqual(
            tax_by_journal[taxed_receipt["journal_reference"]]["account_role_code"],
            "tax_payable",
        )
        self.assertEqual(
            Decimal(str(tax_by_journal[taxed_receipt["journal_reference"]]["credit_amount"])),
            Decimal("2500"),
        )
        self.assertEqual(
            Decimal(str(tax_by_journal[taxed_credit_receipt["journal_reference"]]["debit_amount"])),
            Decimal("2500"),
        )
        self.assertEqual(Decimal(str(tax_ledger["period_debit_total"])), Decimal("2500"))
        self.assertEqual(Decimal(str(tax_ledger["period_credit_total"])), Decimal("2500"))

        accept_period_open(self._period_open_payload(), DATABASE_URL, self.policy.tenant_reference)
        september_status, september_receipt = self._http_json(
            "POST", "/journal-proposals", september
        )
        august_status, august_ledger = self._http_account_ledger(
            "110100",
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-08",
        )
        bare_period = lookup_account_ledger(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            "110100",
            fiscal_period_reference="2026-08",
        )
        first_page_status, first_page = self._http_account_ledger("110100", page_limit=2)
        second_page_status, second_page = self._http_account_ledger(
            "110100",
            page_limit=2,
            cursor=str(first_page["next_cursor"]),
        )
        third_page_status, third_page = self._http_account_ledger(
            "110100",
            page_limit=2,
            cursor=str(second_page["next_cursor"]),
        )
        august_refs = {item["journal_reference"] for item in august_ledger["ledger_lines"]}

        self.assertEqual(september_status, 200)
        self.assertEqual(august_status, 200)
        self.assertEqual(first_page_status, 200)
        self.assertEqual(second_page_status, 200)
        self.assertEqual(third_page_status, 200)
        self.assertEqual(august_ledger["fiscal_period_reference"], "urn:cwl:accounting:fiscal_period:2026-08")
        self.assertEqual(bare_period["fiscal_period_reference"], "urn:cwl:accounting:fiscal_period:2026-08")
        self.assertEqual(len(bare_period["ledger_lines"]), 5)
        self.assertNotIn(september_receipt["journal_reference"], august_refs)
        self.assertEqual(len(august_ledger["ledger_lines"]), 5)
        self.assertEqual(Decimal(str(august_ledger["period_debit_total"])), Decimal("52500"))
        self.assertEqual(Decimal(str(august_ledger["period_credit_total"])), Decimal("49500"))
        self.assertEqual(Decimal(str(first_page["period_debit_total"])), Decimal("77500"))
        self.assertEqual(Decimal(str(first_page["period_credit_total"])), Decimal("49500"))
        self.assertEqual(len(first_page["ledger_lines"]), 2)
        self.assertTrue(first_page["next_cursor"])
        self.assertEqual(len(second_page["ledger_lines"]), 2)
        self.assertTrue(second_page["next_cursor"])
        self.assertEqual(len(third_page["ledger_lines"]), 2)
        self.assertIsNone(third_page["next_cursor"])
        paged_refs = (
            [item["journal_reference"] for item in first_page["ledger_lines"]]
            + [item["journal_reference"] for item in second_page["ledger_lines"]]
            + [item["journal_reference"] for item in third_page["ledger_lines"]]
        )
        self.assertEqual(len(paged_refs), 6)
        self.assertIn(september_receipt["journal_reference"], paged_refs)
        self.assertEqual(
            [
                (item["posted_at"], item["journal_reference"], item["line_number"])
                for item in first_page["ledger_lines"]
            ]
            + [
                (item["posted_at"], item["journal_reference"], item["line_number"])
                for item in second_page["ledger_lines"]
            ]
            + [
                (item["posted_at"], item["journal_reference"], item["line_number"])
                for item in third_page["ledger_lines"]
            ],
            sorted(
                [
                    (item["posted_at"], item["journal_reference"], item["line_number"])
                    for item in first_page["ledger_lines"]
                    + second_page["ledger_lines"]
                    + third_page["ledger_lines"]
                ]
            ),
        )

        missing_query = self._http_json("GET", "/account-ledgers", None)
        missing_account = self._http_json(
            "GET",
            "/account-ledgers?"
            + urllib.parse.urlencode(
                {"legal_entity_reference": self.policy.legal_entity_reference}
            ),
            None,
        )
        post_ledger = self._http_json("POST", "/account-ledgers", {})
        unknown_entity = self._http_account_ledger(
            "110100", legal_entity_reference="urn:cwl:legal_entity:missing"
        )
        unknown_account = self._http_account_ledger("999999")
        unknown_period = self._http_account_ledger(
            "110100",
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01",
        )
        bad_limit = self._http_account_ledger("110100", page_limit="abc")
        high_limit = self._http_account_ledger("110100", page_limit=101)
        bad_cursor = self._http_account_ledger("110100", cursor="not-a-cursor")
        missing_header = self._http_account_ledger("110100", tenant_header=None)
        cross_status, _cross = self._http_account_ledger(
            "110100", tenant_header="urn:cwl:tenant_other"
        )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            lookup_account_ledger(DATABASE_URL, self.policy.tenant_reference, "", "110100")
        with self.assertRaisesRegex(AccountingValidationError, "chart_account_code"):
            lookup_account_ledger(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                "",
            )
        with self.assertRaisesRegex(AccountingValidationError, "page_limit"):
            lookup_account_ledger(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                "110100",
                page_limit=0,
            )
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            lookup_account_ledger(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                "110100",
                cursor="2026-08-31T00:00:00Z",
            )
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            lookup_account_ledger(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                "110100",
                cursor="|urn:cwl:accounting:general_journal:x|1",
            )
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            lookup_account_ledger(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                "110100",
                cursor="2026-08-31T00:00:00Z||1",
            )
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            lookup_account_ledger(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                "110100",
                cursor="2026-08-31T00:00:00Z|urn:cwl:accounting:general_journal:x|",
            )
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            lookup_account_ledger(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                "110100",
                cursor="not-a-time|urn:cwl:accounting:general_journal:x|1",
            )
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            lookup_account_ledger(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                "110100",
                cursor="2026-08-31T00:00:00Z|urn:cwl:accounting:general_journal:x|x",
            )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity"):
            lookup_account_ledger(
                DATABASE_URL,
                self.policy.tenant_reference,
                "urn:cwl:legal_entity:missing",
                "110100",
            )
        with self.assertRaisesRegex(AccountingValidationError, "chart_account"):
            lookup_account_ledger(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                "999999",
            )
        with self.assertRaisesRegex(AccountingValidationError, "fiscal_period"):
            lookup_account_ledger(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                "110100",
                fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01",
            )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_account_ledger("", "110100")
        with self.assertRaisesRegex(AccountingValidationError, "chart_account_code"):
            PostgresPostingLedger(
                DATABASE_URL, self.policy.tenant_reference
            ).load_account_ledger(self.policy.legal_entity_reference, "")

        self.assertEqual(missing_query[0], 400)
        self.assertEqual(missing_account[0], 400)
        self.assertEqual(post_ledger[0], 405)
        self.assertEqual(unknown_entity[0], 404)
        self.assertEqual(unknown_account[0], 404)
        self.assertEqual(unknown_period[0], 404)
        self.assertEqual(bad_limit[0], 400)
        self.assertEqual(high_limit[0], 400)
        self.assertEqual(bad_cursor[0], 400)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before + 6)
        server.shutdown()

    def test_http_closes_period_and_reads_trial_balance(self) -> None:
        """HTTP closes a posted period, replays the close, and reads snapshot or live TB."""
        payload = self._billing_validated_payload()
        accept_journal_proposal(payload, DATABASE_URL, self.policy.tenant_reference)
        server = self._start_http_server()
        close_body = self._period_close_payload()

        health_status, health_body = self._http_json(
            "GET", "/healthz", None, tenant_header=None
        )
        live_status, live_balance = self._http_trial_balance()
        close_status, close_receipt = self._http_json("POST", "/period-closes", close_body)
        replay_status, replay_receipt = self._http_json("POST", "/period-closes", close_body)
        snapshot_status, snapshot_balance = self._http_trial_balance()
        library_close = accept_period_close(
            close_body, DATABASE_URL, self.policy.tenant_reference
        )
        library_balance = lookup_trial_balance(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "urn:cwl:accounting:fiscal_period:2026-08",
        )

        self.assertEqual(health_status, 200)
        self.assertEqual(health_body, {"status": "ok"})
        self.assertEqual(live_status, 200)
        self.assertEqual(live_balance["balance_source_code"], "live")
        self.assertEqual(live_balance["period_status_code"], "open")
        self.assertEqual(
            Decimal(str(self._trial_balance_line(live_balance, "110100")["debit_amount"])),
            Decimal("25000"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(live_balance, "410100")["credit_amount"])),
            Decimal("25000"),
        )
        self.assertEqual(close_status, 200)
        self.assertEqual(close_receipt["period_code"], "2026-08")
        self.assertEqual(close_receipt["period_status_code"], "hard_closed")
        self.assertFalse(close_receipt["replayed"])
        self.assertEqual(close_receipt["source_journal_count"], 2)
        self.assertEqual(self._period_status("2026-08"), "hard_closed")
        self.assertEqual(self._count_table("accounting_reporting.trial_balance_snapshot"), 1)
        self.assertEqual(replay_status, 200)
        self.assertTrue(replay_receipt["replayed"])
        self.assertEqual(replay_receipt["snapshot_record_id"], close_receipt["snapshot_record_id"])
        self.assertEqual(replay_receipt["source_payload_hash"], close_receipt["source_payload_hash"])
        self.assertEqual(self._count_table("accounting_reporting.trial_balance_snapshot"), 1)
        self.assertEqual(snapshot_status, 200)
        self.assertEqual(snapshot_balance["balance_source_code"], "snapshot")
        self.assertEqual(snapshot_balance["period_status_code"], "hard_closed")
        self.assertEqual(snapshot_balance["snapshot_record_id"], close_receipt["snapshot_record_id"])
        self.assertEqual(
            Decimal(str(self._trial_balance_line(snapshot_balance, "110100")["debit_amount"])),
            Decimal("25000"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(snapshot_balance, "410100")["credit_amount"])),
            Decimal("25000"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(snapshot_balance, "410100")["debit_amount"])),
            Decimal("25000"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(snapshot_balance, "410100")["net_balance_amount"])),
            Decimal("0"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(snapshot_balance, "310100")["credit_amount"])),
            Decimal("25000"),
        )
        self.assertEqual(self._count_closing_journals(), 1)
        self.assertEqual(library_close["snapshot_record_id"], close_receipt["snapshot_record_id"])
        self.assertTrue(library_close["replayed"])
        self.assertEqual(library_balance, snapshot_balance)
        aliased_close = accept_period_close(
            {
                "tenant_reference": self.policy.tenant_reference,
                "legal_entity_reference": self.policy.legal_entity_reference,
                "accounting_book_reference": self.policy.accounting_book_reference,
                "period_code": "2026-08",
                "snapshot_currency_code": "KRW",
            },
            DATABASE_URL,
            self.policy.tenant_reference,
        )
        self.assertTrue(aliased_close["replayed"])
        self.assertEqual(aliased_close["snapshot_record_id"], close_receipt["snapshot_record_id"])

        snapshots_before = self._count_table("accounting_reporting.trial_balance_snapshot")
        journals_before = self._count_table("accounting_core.general_journal")
        cross_close_status, cross_close_body = self._http_json(
            "POST",
            "/period-closes",
            close_body,
            tenant_header="urn:cwl:tenant_other",
        )
        body_mismatch_status, _body_mismatch = self._http_json(
            "POST",
            "/period-closes",
            self._period_close_payload(tenant_reference="urn:cwl:tenant_other"),
        )
        missing_close_header = self._http_json(
            "POST", "/period-closes", close_body, tenant_header=None
        )
        unknown_close_status, unknown_close_body = self._http_json(
            "POST",
            "/period-closes",
            self._period_close_payload(
                fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-10"
            ),
        )
        cross_tb_status, cross_tb_body = self._http_trial_balance(
            tenant_header="urn:cwl:tenant_other"
        )
        missing_tb_header = self._http_trial_balance(tenant_header=None)
        missing_tb_query = self._http_json("GET", "/trial-balances", None)
        unknown_tb_status, unknown_tb_body = self._http_trial_balance(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-10"
        )
        missing_book_tb_status, missing_book_tb_body = self._http_trial_balance(
            book_reference="urn:cwl:accounting_book:missing"
        )
        with self.assertRaisesRegex(AccountingValidationError, "JSON object"):
            accept_period_close(["not-an-object"], DATABASE_URL, self.policy.tenant_reference)
        with self.assertRaisesRegex(AccountingValidationError, "bound tenant"):
            accept_period_close(
                self._period_close_payload(tenant_reference="urn:cwl:tenant_other"),
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "Supply the book reporting currency"):
            accept_period_close(
                {
                    "tenant_reference": self.policy.tenant_reference,
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "book_reference": self.policy.accounting_book_reference,
                    "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
                },
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        bad_close_json = self._http_raw(
            "POST", "/period-closes", b"{", self.policy.tenant_reference
        )
        incomplete_close_status, _incomplete_close = self._http_json(
            "POST",
            "/period-closes",
            {"tenant_reference": self.policy.tenant_reference},
        )
        legal_only_tb = self._http_json(
            "GET",
            "/trial-balances?"
            + urllib.parse.urlencode(
                {"legal_entity_reference": self.policy.legal_entity_reference}
            ),
            None,
        )
        legal_and_book_tb = self._http_json(
            "GET",
            "/trial-balances?"
            + urllib.parse.urlencode(
                {
                    "legal_entity_reference": self.policy.legal_entity_reference,
                    "book_reference": self.policy.accounting_book_reference,
                }
            ),
            None,
        )
        with self.assertRaisesRegex(AccountingValidationError, "Supply those close command fields"):
            accept_period_close(
                {
                    "tenant_reference": self.policy.tenant_reference,
                    "snapshot_currency_code": "KRW",
                },
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "Supply those trial-balance fields"):
            lookup_trial_balance(DATABASE_URL, self.policy.tenant_reference, "", "", "")
        with self.assertRaisesRegex(AccountingValidationError, "Supply the fiscal period code"):
            self.ledger.load_period_trial_balance(
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "   ",
            )
        self.assertEqual(
            self._count_table("accounting_reporting.trial_balance_snapshot"), snapshots_before
        )
        self._delete_snapshots()
        with self.assertRaisesRegex(AccountingValidationError, "Restore the trial_balance_snapshot"):
            lookup_trial_balance(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                self.policy.accounting_book_reference,
                "urn:cwl:accounting:fiscal_period:2026-08",
            )

        self.assertEqual(cross_close_status, 403)
        self.assertIn("Send the close to that tenant's endpoint", str(cross_close_body["error_message"]))
        self.assertEqual(body_mismatch_status, 403)
        self.assertEqual(missing_close_header[0], 400)
        self.assertEqual(unknown_close_status, 422)
        self.assertIn("Create the fiscal_period row", str(unknown_close_body["error_message"]))
        self.assertEqual(cross_tb_status, 403)
        self.assertIn("Send the trial-balance read to that tenant's endpoint", str(cross_tb_body["error_message"]))
        self.assertEqual(missing_tb_header[0], 400)
        self.assertEqual(missing_tb_query[0], 400)
        self.assertEqual(unknown_tb_status, 404)
        self.assertIn("Create the fiscal_period row", str(unknown_tb_body["error_message"]))
        self.assertEqual(missing_book_tb_status, 404)
        self.assertIn("Create the accounting_book row", str(missing_book_tb_body["error_message"]))
        self.assertEqual(bad_close_json[0], 400)
        self.assertEqual(incomplete_close_status, 422)
        self.assertEqual(legal_only_tb[0], 400)
        self.assertEqual(legal_and_book_tb[0], 400)
        self.assertEqual(self._count_table("accounting_reporting.trial_balance_snapshot"), 0)
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before)
        self.assertEqual(self._period_status("2026-08"), "hard_closed")
        server.shutdown()

    def test_http_soft_closes_then_hard_closes_period(self) -> None:
        """POST /period-closes soft-closes, then hard-closes, without a second close route."""
        invoice = self._billing_validated_payload()
        later_invoice = self._billing_validated_payload(
            proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf699",
            source_payload_hash="sha256:" + "c" * 64,
            idempotency_key=(
                f"{self.policy.tenant_reference}:invoice_draft:"
                "019d7b92-1aa0-7a7f-b61c-962c0f4bf699:"
                f"sha256:{'c' * 64}:v1"
            ),
        )
        server = self._start_http_server()
        post_status, posted = self._http_json("POST", "/journal-proposals", invoice)
        soft_body = self._period_close_payload(period_status_code="soft_closed")
        soft_status, soft_receipt = self._http_json("POST", "/period-closes", soft_body)
        replay_status, replay_receipt = self._http_json("POST", "/period-closes", soft_body)
        later_status, later_body = self._http_json("POST", "/journal-proposals", later_invoice)
        reverse_status, reversing = self._http_json(
            "POST",
            "/journal-reversals",
            {
                "tenant_reference": self.policy.tenant_reference,
                "journal_reference": posted["journal_reference"],
                "reversal_date": "2026-08-31",
                "reversal_reason_code": "billing_correction",
            },
        )
        live_status, live_balance = self._http_trial_balance()
        listed_soft_status, listed_soft = self._http_fiscal_periods()
        hard_status, hard_receipt = self._http_json(
            "POST", "/period-closes", self._period_close_payload()
        )
        snapshot_status, snapshot_balance = self._http_trial_balance()
        listed_hard_status, listed_hard = self._http_fiscal_periods()
        snapshots_after_hard = self._count_table("accounting_reporting.trial_balance_snapshot")
        outbox_after_hard = self._count_outbox("period_close")
        journals_after_hard = self._count_table("accounting_core.general_journal")
        closed_reverse_status, _closed_reverse = self._http_json(
            "POST",
            "/journal-reversals",
            {
                "tenant_reference": self.policy.tenant_reference,
                "journal_reference": posted["journal_reference"],
                "reversal_date": "2026-08-31",
                "reversal_reason_code": "billing_correction",
            },
        )
        unhard_status, unhard_body = self._http_json("POST", "/period-closes", soft_body)
        cross_status, _cross = self._http_json(
            "POST",
            "/period-closes",
            soft_body,
            tenant_header="urn:cwl:tenant_other",
        )
        by_code_soft = {
            str(item["period_code"]): item for item in listed_soft["fiscal_periods"]
        }
        by_code_hard = {
            str(item["period_code"]): item for item in listed_hard["fiscal_periods"]
        }

        self.assertEqual(post_status, 200)
        self.assertEqual(soft_status, 200)
        self.assertEqual(soft_receipt["period_status_code"], "soft_closed")
        self.assertEqual(soft_receipt["snapshot_record_id"], "")
        self.assertFalse(soft_receipt["replayed"])
        self.assertEqual(replay_status, 200)
        self.assertTrue(replay_receipt["replayed"])
        self.assertEqual(replay_receipt["period_status_code"], "soft_closed")
        self.assertEqual(replay_receipt["snapshot_record_id"], "")
        self.assertEqual(
            replay_receipt["snapshot_generated_at"], soft_receipt["snapshot_generated_at"]
        )
        self.assertEqual(later_status, 422)
        self.assertIn("open period", str(later_body["error_message"]))
        self.assertEqual(reverse_status, 200)
        self.assertEqual(
            reversing["journal_reference"], f"{posted['journal_reference']}:reversal"
        )
        self.assertEqual(live_status, 200)
        self.assertEqual(live_balance["balance_source_code"], "live")
        self.assertEqual(live_balance["period_status_code"], "soft_closed")
        self.assertEqual(
            Decimal(str(self._trial_balance_line(live_balance, "110100")["net_balance_amount"])),
            Decimal("0"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(live_balance, "410100")["net_balance_amount"])),
            Decimal("0"),
        )
        self.assertEqual(listed_soft_status, 200)
        self.assertEqual(by_code_soft["2026-08"]["period_status_code"], "soft_closed")
        self.assertEqual(hard_status, 200)
        self.assertEqual(hard_receipt["period_status_code"], "hard_closed")
        self.assertTrue(hard_receipt["snapshot_record_id"])
        self.assertFalse(hard_receipt["replayed"])
        self.assertEqual(hard_receipt["source_journal_count"], 2)
        self.assertEqual(self._count_closing_journals(), 0)
        self.assertEqual(snapshots_after_hard, 1)
        self.assertEqual(snapshot_status, 200)
        self.assertEqual(snapshot_balance["balance_source_code"], "snapshot")
        self.assertEqual(snapshot_balance["period_status_code"], "hard_closed")
        self.assertEqual(listed_hard_status, 200)
        self.assertEqual(by_code_hard["2026-08"]["period_status_code"], "hard_closed")
        self.assertEqual(closed_reverse_status, 422)
        self.assertEqual(unhard_status, 422)
        self.assertIn("cannot be soft-closed", str(unhard_body["error_message"]))
        self.assertEqual(cross_status, 403)
        self.assertEqual(
            self._count_table("accounting_reporting.trial_balance_snapshot"),
            snapshots_after_hard,
        )
        self.assertEqual(self._count_outbox("period_close"), outbox_after_hard)
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_after_hard)
        server.shutdown()

    def test_pulls_validated_billing_proposals_and_posts(self) -> None:
        """AIS pulls Billing #15 validated pages, posts them, and ignores other statuses."""
        invoice = self._billing_validated_payload()
        cash = self._billing_cash_payload()
        draft = self._billing_validated_payload(
            proposal_id=str(uuid.uuid4()),
            proposal_status="draft",
            idempotency_key=f"{self.policy.tenant_reference}:invoice_draft:draft:sha256:{'f' * 64}:v1",
            source_payload_hash="sha256:" + "f" * 64,
            proposed_at="2026-08-30T00:00:00Z",
        )
        exported = self._billing_validated_payload(
            proposal_id=str(uuid.uuid4()),
            proposal_status="exported",
            idempotency_key=f"{self.policy.tenant_reference}:invoice_draft:exported:sha256:{'e' * 64}:v1",
            source_payload_hash="sha256:" + "e" * 64,
            proposed_at="2026-08-30T12:00:00Z",
        )
        rejected = self._billing_validated_payload(
            proposal_id=str(uuid.uuid4()),
            proposal_status="rejected",
            idempotency_key=f"{self.policy.tenant_reference}:invoice_draft:rejected:sha256:{'d' * 64}:v1",
            source_payload_hash="sha256:" + "d" * 64,
            proposed_at="2026-08-30T18:00:00Z",
        )
        unmapped = self._billing_validated_payload(
            proposal_id=str(uuid.uuid4()),
            idempotency_key=f"{self.policy.tenant_reference}:invoice_draft:tax:sha256:{'c' * 64}:v1",
            source_payload_hash="sha256:" + "c" * 64,
            proposed_at="2026-08-31T12:00:00Z",
            lines=[
                {
                    "line_number": 1,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "1000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "contract_liability",
                    "debit_amount": "0",
                    "credit_amount": "1000",
                },
            ],
        )
        billing_url = self._start_fake_billing(
            [cash, draft, unmapped, exported, invoice, rejected]
        )
        billing = self._last_fake_billing
        ais_server = self._start_http_server()

        page = pull_validated_journal_proposals(
            billing_url,
            self.policy.tenant_reference,
            proposed_after="2026-08-01T00:00:00Z",
            page_limit=10,
        )
        default_page = pull_validated_journal_proposals(
            billing_url, self.policy.tenant_reference
        )
        inclusive_page = pull_validated_journal_proposals(
            billing_url,
            self.policy.tenant_reference,
            proposed_after=str(invoice["proposed_at"]),
        )
        receipts = accept_pulled_proposals(
            billing_url,
            DATABASE_URL,
            self.policy.tenant_reference,
            proposed_after="2026-08-01T00:00:00Z",
            page_limit=1,
        )
        replayed = accept_pulled_proposals(
            billing_url, DATABASE_URL, self.policy.tenant_reference
        )
        invoice_lookup = lookup_published_receipt(
            DATABASE_URL, self.policy.tenant_reference, str(invoice["idempotency_key"])
        )
        cash_lookup = lookup_published_receipt(
            DATABASE_URL, self.policy.tenant_reference, str(cash["idempotency_key"])
        )
        lookup_status, http_lookup = self._http_lookup(str(invoice["idempotency_key"]))
        pulled_invoice = pull_journal_proposal(
            billing_url, self.policy.tenant_reference, str(invoice["proposal_id"])
        )
        http_status, http_body = self._http_json(
            "POST",
            "/billing-proposal-pulls",
            {"tenant_reference": self.policy.tenant_reference, "billing_base_url": billing_url},
        )

        self.assertEqual(
            [item["proposal_id"] for item in page.journal_proposals],
            [invoice["proposal_id"], cash["proposal_id"], unmapped["proposal_id"]],
        )
        self.assertIsNone(page.next_cursor)
        self.assertEqual(set(billing.last_list_body), {"journal_proposals", "next_cursor"})
        self.assertNotIn("items", billing.last_list_body)
        self.assertNotIn("cursor", billing.last_list_body)
        self.assertEqual(billing.list_queries[0].get("page_limit"), ["10"])
        self.assertEqual(billing.list_queries[0].get("proposal_status"), ["validated"])
        self.assertEqual(billing.list_queries[1].get("page_limit"), ["50"])
        paged_queries = [query for query in billing.list_queries if query.get("page_limit") == ["1"]]
        self.assertGreaterEqual(len(paged_queries), 2)
        self.assertNotIn("cursor", paged_queries[0])
        self.assertIn("cursor", paged_queries[1])
        self.assertEqual(
            paged_queries[1]["cursor"][0],
            f"{draft['proposed_at']}|{draft['proposal_id']}",
        )
        self.assertEqual(
            [item["proposal_id"] for item in default_page.journal_proposals],
            [invoice["proposal_id"], cash["proposal_id"], unmapped["proposal_id"]],
        )
        self.assertEqual(
            [item["proposal_id"] for item in inclusive_page.journal_proposals],
            [invoice["proposal_id"], cash["proposal_id"], unmapped["proposal_id"]],
        )
        self.assertNotIn("journal_proposals", pulled_invoice)
        self.assertNotIn("next_cursor", pulled_invoice)
        self.assertNotIn("items", pulled_invoice)
        self.assertNotIn("cursor", pulled_invoice)
        self.assertEqual(len(receipts["posting_receipts"]), 2)
        self.assertEqual(receipts["posting_receipts"][0], invoice_lookup)
        self.assertEqual(receipts["posting_receipts"][1], cash_lookup)
        self.assertEqual(len(receipts["rejected_proposals"]), 1)
        self.assertEqual(
            receipts["rejected_proposals"][0]["proposal_id"], unmapped["proposal_id"]
        )
        self.assertEqual(
            receipts["rejected_proposals"][0]["rejection_reason_code"],
            "unknown_account_role",
        )
        self.assertEqual(replayed, receipts)
        self.assertEqual(lookup_status, 200)
        self.assertEqual(http_lookup, invoice_lookup)
        self.assertEqual(pulled_invoice["proposal_id"], invoice["proposal_id"])
        self.assertEqual(http_status, 200)
        self.assertEqual(http_body["posting_receipts"], replayed["posting_receipts"])
        self.assertEqual(http_body["rejected_proposals"], replayed["rejected_proposals"])
        self.assertEqual(self._count_table("accounting_core.general_journal"), 2)
        self.assertEqual(self._posted_chart_accounts(), {"110100", "410100", "110200"})

        journals_before = self._count_table("accounting_core.general_journal")
        with self.assertRaisesRegex(AccountingValidationError, "Do not retry as another tenant"):
            pull_journal_proposal(
                billing_url, self.policy.tenant_reference, str(uuid.uuid4())
            )
        with self.assertRaisesRegex(AccountingValidationError, "Set BILLING_BASE_URL"):
            pull_validated_journal_proposals("", self.policy.tenant_reference)
        with self.assertRaisesRegex(AccountingValidationError, "Set BILLING_BASE_URL"):
            accept_pulled_proposals("", DATABASE_URL, self.policy.tenant_reference)
        with self.assertRaisesRegex(AccountingValidationError, "Ask Billing to correct"):
            pull_validated_journal_proposals(
                self._start_fake_billing([], list_status=422),
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "Retry the Billing pull"):
            pull_validated_journal_proposals(
                self._start_fake_billing([], list_status=500),
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "JSON object"):
            accept_billing_proposal_pull(
                ["not-an-object"], DATABASE_URL, self.policy.tenant_reference
            )
        with self.assertRaisesRegex(AccountingValidationError, "bound tenant"):
            accept_billing_proposal_pull(
                {"tenant_reference": "urn:cwl:tenant_other", "billing_base_url": billing_url},
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        cross_status, _cross = self._http_json(
            "POST",
            "/billing-proposal-pulls",
            {"tenant_reference": self.policy.tenant_reference, "billing_base_url": billing_url},
            tenant_header="urn:cwl:tenant_other",
        )
        body_mismatch_status, _body_mismatch = self._http_json(
            "POST",
            "/billing-proposal-pulls",
            {"tenant_reference": "urn:cwl:tenant_other", "billing_base_url": billing_url},
        )
        get_pull_status, _get_pull = self._http_json("GET", "/billing-proposal-pulls", None)
        bad_pull_json = self._http_raw(
            "POST", "/billing-proposal-pulls", b"{", self.policy.tenant_reference
        )
        with mock.patch.dict(os.environ, {"BILLING_BASE_URL": ""}, clear=False):
            missing_url_status, _missing_url = self._http_json(
                "POST",
                "/billing-proposal-pulls",
                {"tenant_reference": self.policy.tenant_reference},
            )
        with mock.patch.dict(os.environ, {"BILLING_BASE_URL": billing_url}, clear=False):
            from_env = accept_billing_proposal_pull(
                {"tenant_reference": self.policy.tenant_reference},
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        empty_page = accept_billing_proposal_pull(
            {
                "tenant_reference": self.policy.tenant_reference,
                "billing_base_url": billing_url,
                "proposed_after": "2026-08-01T00:00:00Z",
                "cursor": f"{unmapped['proposed_at']}|{unmapped['proposal_id']}",
                "page_limit": 2,
            },
            DATABASE_URL,
            self.policy.tenant_reference,
        )
        mixed_page = pull_validated_journal_proposals(
            self._start_fake_billing(
                [],
                list_raw=json.dumps(
                    {
                        "journal_proposals": [1, {"proposal_status": "validated", "proposal_id": "x"}],
                        "next_cursor": "",
                    }
                ).encode("utf-8"),
            ),
            self.policy.tenant_reference,
        )
        with self.assertRaisesRegex(AccountingValidationError, "page_limit"):
            accept_billing_proposal_pull(
                {
                    "tenant_reference": self.policy.tenant_reference,
                    "billing_base_url": billing_url,
                    "page_limit": "nope",
                },
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "page_limit"):
            pull_validated_journal_proposals(
                billing_url, self.policy.tenant_reference, page_limit=0
            )
        with self.assertRaisesRegex(AccountingValidationError, "page_limit"):
            pull_validated_journal_proposals(
                billing_url, self.policy.tenant_reference, page_limit=101
            )
        with self.assertRaisesRegex(AccountingValidationError, "items or cursor"):
            pull_validated_journal_proposals(
                self._start_fake_billing(
                    [],
                    list_raw=b'{"items":[],"journal_proposals":[],"next_cursor":null}',
                ),
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "items or cursor"):
            pull_validated_journal_proposals(
                self._start_fake_billing(
                    [],
                    list_raw=b'{"cursor":"x","journal_proposals":[],"next_cursor":null}',
                ),
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "proposal_id"):
            pull_journal_proposal(billing_url, self.policy.tenant_reference, "")
        with self.assertRaisesRegex(AccountingValidationError, "must be a CWL URN"):
            pull_validated_journal_proposals(billing_url, "not-a-urn")
        with self.assertRaisesRegex(AccountingValidationError, "not validated"):
            pull_journal_proposal(
                billing_url, self.policy.tenant_reference, str(draft["proposal_id"])
            )
        with self.assertRaisesRegex(AccountingValidationError, "Ask Billing to correct"):
            pull_journal_proposal(
                self._start_fake_billing([invoice], get_status=422),
                self.policy.tenant_reference,
                str(invoice["proposal_id"]),
            )
        with self.assertRaisesRegex(AccountingValidationError, "Retry the Billing pull"):
            pull_journal_proposal(
                self._start_fake_billing([invoice], get_status=500),
                self.policy.tenant_reference,
                str(invoice["proposal_id"]),
            )
        with self.assertRaisesRegex(AccountingValidationError, "HTTP 401"):
            pull_validated_journal_proposals(
                self._start_fake_billing([], list_status=401),
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "Retry the Billing pull"):
            pull_validated_journal_proposals(
                "http://127.0.0.1:1", self.policy.tenant_reference
            )
        with self.assertRaisesRegex(AccountingValidationError, "Retry the Billing pull"):
            pull_validated_journal_proposals(
                "https://127.0.0.1:1", self.policy.tenant_reference
            )
        with self.assertRaisesRegex(AccountingValidationError, "Retry the Billing pull"):
            pull_validated_journal_proposals(
                "https://127.0.0.1", self.policy.tenant_reference
            )
        https_origin = urllib.parse.urlparse(self._start_fake_billing([]))
        tls_context = mock.Mock()
        tls_context.wrap_socket.side_effect = lambda sock, server_hostname=None: sock
        with mock.patch(
            "accounting_information_platform.billing_pull.ssl.create_default_context",
            return_value=tls_context,
        ):
            https_page = pull_validated_journal_proposals(
                f"https://{https_origin.hostname}:{https_origin.port}",
                self.policy.tenant_reference,
            )
        tls_context.wrap_socket.assert_called_once()
        self.assertEqual(
            tls_context.wrap_socket.call_args.kwargs["server_hostname"],
            https_origin.hostname,
        )
        self.assertEqual(https_page.journal_proposals, ())
        with self.assertRaisesRegex(AccountingValidationError, "http or https origin"):
            pull_validated_journal_proposals(
                "file:///tmp/billing", self.policy.tenant_reference
            )
        with self.assertRaisesRegex(AccountingValidationError, "http or https origin"):
            pull_validated_journal_proposals(
                "http:///v1/journal-proposals", self.policy.tenant_reference
            )
        with self.assertRaisesRegex(AccountingValidationError, "non-JSON"):
            pull_validated_journal_proposals(
                self._start_fake_billing([], list_raw=b"not-json"),
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "non-JSON"):
            pull_validated_journal_proposals(
                self._start_fake_billing([], list_raw=b"\xff\xfe"),
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "JSON object"):
            pull_validated_journal_proposals(
                self._start_fake_billing([], list_raw=b"[1]"),
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "journal_proposals"):
            pull_validated_journal_proposals(
                self._start_fake_billing([], list_raw=b'{"next_cursor":null}'),
                self.policy.tenant_reference,
            )
        self.assertEqual(cross_status, 403)
        self.assertEqual(body_mismatch_status, 403)
        self.assertEqual(get_pull_status, 405)
        self.assertEqual(bad_pull_json[0], 400)
        self.assertEqual(missing_url_status, 422)
        self.assertEqual(from_env["posting_receipts"], replayed["posting_receipts"])
        self.assertEqual(from_env["rejected_proposals"], replayed["rejected_proposals"])
        self.assertEqual(empty_page["posting_receipts"], [])
        self.assertEqual(empty_page["rejected_proposals"], [])
        self.assertEqual(billing.last_list_body["journal_proposals"], [])
        self.assertIsNone(billing.last_list_body["next_cursor"])
        self.assertEqual(set(billing.last_list_body), {"journal_proposals", "next_cursor"})
        self.assertEqual(len(mixed_page.journal_proposals), 1)
        self.assertIsNone(mixed_page.next_cursor)
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before)
        ais_server.shutdown()

    def test_http_reverses_posted_journal_and_preserves_original_receipt(self) -> None:
        """HTTP reverse appends equal-and-opposite lines and keeps the original receipt."""
        invoice = self._billing_validated_payload()
        posted = accept_journal_proposal(invoice, DATABASE_URL, self.policy.tenant_reference)
        original_lookup = lookup_published_receipt(
            DATABASE_URL, self.policy.tenant_reference, str(invoice["idempotency_key"])
        )
        server = self._start_http_server()
        reverse_body = {
            "tenant_reference": self.policy.tenant_reference,
            "journal_reference": posted["journal_reference"],
            "reversal_date": "2026-08-31",
            "reversal_reason_code": "billing_correction",
        }

        status, reversing = self._http_json("POST", "/journal-reversals", reverse_body)
        replay_status, replayed = self._http_json("POST", "/journal-reversals", reverse_body)
        key_status, key_reversing = self._http_json(
            "POST",
            "/journal-reversals",
            {
                "tenant_reference": self.policy.tenant_reference,
                "idempotency_key": invoice["idempotency_key"],
                "reversal_date": "2026-08-31",
                "reversal_reason_code": "billing_correction",
            },
        )
        both_status, both_reversing = self._http_json(
            "POST",
            "/journal-reversals",
            {
                "tenant_reference": self.policy.tenant_reference,
                "journal_reference": posted["journal_reference"],
                "idempotency_key": invoice["idempotency_key"],
                "reversal_date": "2026-08-31",
                "reversal_reason_code": "billing_correction",
            },
        )
        original_status, original_after = self._http_lookup(str(invoice["idempotency_key"]))
        reversing_key = f"reversal:{posted['journal_reference']}"
        reversing_status, reversing_lookup = self._http_lookup(reversing_key)
        library_reversing = lookup_published_receipt(
            DATABASE_URL, self.policy.tenant_reference, reversing_key
        )
        live_status, live_balance = self._http_trial_balance()

        self.assertEqual(status, 200)
        self.assertEqual(replay_status, 200)
        self.assertEqual(key_status, 200)
        self.assertEqual(both_status, 200)
        self.assertEqual(reversing, replayed)
        self.assertEqual(reversing, key_reversing)
        self.assertEqual(reversing, both_reversing)
        self.assertEqual(reversing, reversing_lookup)
        self.assertEqual(reversing, library_reversing)
        self.assertEqual(reversing["posting_status_code"], "posted")
        self.assertEqual(reversing["idempotency_key"], reversing_key)
        self.assertEqual(reversing["journal_reference"], f"{posted['journal_reference']}:reversal")
        self.assertEqual(original_status, 200)
        self.assertEqual(original_after, original_lookup)
        self.assertEqual(original_after, posted)
        self.assertEqual(reversing_status, 200)
        self.assertEqual(self._count_table("accounting_core.general_journal"), 2)
        self.assertEqual(self._count_table("accounting_core.journal_reversal"), 1)
        self.assertEqual(self._original_journal_status(str(posted["journal_reference"])), "posted")
        self.assertEqual(live_status, 200)
        self.assertEqual(
            Decimal(str(self._trial_balance_line(live_balance, "110100")["net_balance_amount"])),
            Decimal("0"),
        )
        self.assertEqual(
            Decimal(str(self._trial_balance_line(live_balance, "410100")["net_balance_amount"])),
            Decimal("0"),
        )

        journals_before = self._count_table("accounting_core.general_journal")
        get_status, _get_body = self._http_json("GET", "/journal-reversals", None)
        missing_header = self._http_json(
            "POST", "/journal-reversals", reverse_body, tenant_header=None
        )
        cross_status, _cross = self._http_json(
            "POST",
            "/journal-reversals",
            reverse_body,
            tenant_header="urn:cwl:tenant_other",
        )
        body_mismatch = self._http_json(
            "POST",
            "/journal-reversals",
            {**reverse_body, "tenant_reference": "urn:cwl:tenant_other"},
        )
        bad_json = self._http_raw(
            "POST", "/journal-reversals", b"{", self.policy.tenant_reference
        )
        unknown_journal = self._http_json(
            "POST",
            "/journal-reversals",
            {
                "tenant_reference": self.policy.tenant_reference,
                "journal_reference": "urn:cwl:accounting:general_journal:missing",
                "reversal_date": "2026-08-31",
                "reversal_reason_code": "billing_correction",
            },
        )
        unknown_key = self._http_json(
            "POST",
            "/journal-reversals",
            {
                "tenant_reference": self.policy.tenant_reference,
                "idempotency_key": f"{self.policy.tenant_reference}:invoice_draft:missing:sha256:{'b' * 64}:v1",
                "reversal_date": "2026-08-31",
                "reversal_reason_code": "billing_correction",
            },
        )
        with self.assertRaisesRegex(AccountingValidationError, "JSON object"):
            accept_journal_reversal(["not-an-object"], DATABASE_URL, self.policy.tenant_reference)
        with self.assertRaisesRegex(AccountingValidationError, "bound tenant"):
            accept_journal_reversal(
                {**reverse_body, "tenant_reference": "urn:cwl:tenant_other"},
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "journal_reference"):
            accept_journal_reversal(
                {
                    "tenant_reference": self.policy.tenant_reference,
                    "reversal_date": "2026-08-31",
                    "reversal_reason_code": "billing_correction",
                },
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        cash = self._billing_cash_payload()
        accept_journal_proposal(cash, DATABASE_URL, self.policy.tenant_reference)
        with self.assertRaisesRegex(AccountingValidationError, "do not match"):
            accept_journal_reversal(
                {
                    "tenant_reference": self.policy.tenant_reference,
                    "journal_reference": posted["journal_reference"],
                    "idempotency_key": cash["idempotency_key"],
                    "reversal_date": "2026-08-31",
                    "reversal_reason_code": "billing_correction",
                },
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "reversal_date"):
            accept_journal_reversal(
                {
                    "tenant_reference": self.policy.tenant_reference,
                    "journal_reference": posted["journal_reference"],
                    "reversal_date": "31-08-2026",
                    "reversal_reason_code": "billing_correction",
                },
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "reversal_reason_code"):
            accept_journal_reversal(
                {
                    "tenant_reference": self.policy.tenant_reference,
                    "journal_reference": posted["journal_reference"],
                    "reversal_date": "2026-08-31",
                },
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        close_status, _close = self._http_json(
            "POST", "/period-closes", self._period_close_payload()
        )
        closed_reverse = self._http_json(
            "POST",
            "/journal-reversals",
            {
                "tenant_reference": self.policy.tenant_reference,
                "idempotency_key": cash["idempotency_key"],
                "reversal_date": "2026-08-31",
                "reversal_reason_code": "billing_correction",
            },
        )

        self.assertEqual(get_status, 400)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(body_mismatch[0], 403)
        self.assertEqual(bad_json[0], 400)
        self.assertEqual(unknown_journal[0], 422)
        self.assertEqual(unknown_key[0], 422)
        self.assertEqual(close_status, 200)
        self.assertEqual(closed_reverse[0], 422)
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before + 1)
        server.shutdown()

    def test_http_lists_journal_reversals_without_sql(self) -> None:
        """GET /journal-reversals lists stored reversal lineage; POST remains the command."""
        self._seed_additional_period("2026-09", date(2026, 9, 1), date(2026, 9, 30))
        invoice = self._billing_validated_payload()
        cash = self._billing_cash_payload()
        server = self._start_http_server()
        reversals_before = self._count_table("accounting_core.journal_reversal")

        empty_status, empty_page = self._http_journal_reversals()
        empty_library = lookup_journal_reversals(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
        )
        self.assertEqual(empty_status, 200)
        self.assertEqual(empty_page, empty_library)
        self.assertEqual(empty_page["journal_reversals"], [])
        self.assertIsNone(empty_page["next_cursor"])
        self.assertEqual(empty_page["tenant_reference"], self.policy.tenant_reference)
        self.assertEqual(
            empty_page["legal_entity_reference"], self.policy.legal_entity_reference
        )
        self.assertNotIn("original_journal_reference", empty_page)
        self.assertNotIn("fiscal_period_reference", empty_page)

        post_status, posted = self._http_json("POST", "/journal-proposals", invoice)
        cash_status, cash_posted = self._http_json("POST", "/journal-proposals", cash)
        reverse_body = {
            "tenant_reference": self.policy.tenant_reference,
            "journal_reference": posted["journal_reference"],
            "reversal_date": "2026-08-31",
            "reversal_reason_code": "billing_correction",
        }
        reverse_status, reversing = self._http_json(
            "POST", "/journal-reversals", reverse_body
        )
        cash_reverse_status, cash_reversing = self._http_json(
            "POST",
            "/journal-reversals",
            {
                "tenant_reference": self.policy.tenant_reference,
                "journal_reference": cash_posted["journal_reference"],
                "reversal_date": "2026-08-31",
                "reversal_reason_code": "billing_correction",
            },
        )
        listed_status, listed = self._http_journal_reversals()
        library = lookup_journal_reversals(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
        )
        persist = PostgresPostingLedger(
            DATABASE_URL, self.policy.tenant_reference
        ).load_journal_reversals(self.policy.legal_entity_reference)
        filtered_status, filtered = self._http_journal_reversals(
            original_journal_reference=str(posted["journal_reference"])
        )
        period_status, period_page = self._http_journal_reversals(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-08"
        )
        empty_period_status, empty_period = self._http_journal_reversals(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-09"
        )
        first_page_status, first_page = self._http_journal_reversals(page_limit=1)
        second_page_status, second_page = self._http_journal_reversals(
            page_limit=1,
            cursor=str(first_page["next_cursor"]),
        )
        by_original = {
            str(item["original_journal_reference"]): item
            for item in listed["journal_reversals"]
        }

        self.assertEqual(post_status, 200)
        self.assertEqual(cash_status, 200)
        self.assertEqual(reverse_status, 200)
        self.assertEqual(cash_reverse_status, 200)
        self.assertEqual(listed_status, 200)
        self.assertEqual(listed, library)
        self.assertEqual(listed, persist)
        self.assertEqual(len(listed["journal_reversals"]), 2)
        self.assertEqual(
            by_original[str(posted["journal_reference"])]["reversal_journal_reference"],
            reversing["journal_reference"],
        )
        self.assertEqual(
            by_original[str(posted["journal_reference"])]["reversal_date"],
            "2026-08-31",
        )
        self.assertEqual(
            by_original[str(posted["journal_reference"])]["reversal_reason_code"],
            "billing_correction",
        )
        self.assertTrue(by_original[str(posted["journal_reference"])]["posted_at"])
        self.assertEqual(
            by_original[str(cash_posted["journal_reference"])]["reversal_journal_reference"],
            cash_reversing["journal_reference"],
        )
        self.assertEqual(filtered_status, 200)
        self.assertEqual(filtered["original_journal_reference"], posted["journal_reference"])
        self.assertEqual(len(filtered["journal_reversals"]), 1)
        self.assertEqual(
            filtered["journal_reversals"][0]["original_journal_reference"],
            posted["journal_reference"],
        )
        self.assertEqual(period_status, 200)
        self.assertEqual(
            period_page["fiscal_period_reference"],
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        self.assertEqual(len(period_page["journal_reversals"]), 2)
        self.assertEqual(empty_period_status, 200)
        self.assertEqual(empty_period["journal_reversals"], [])
        self.assertEqual(first_page_status, 200)
        self.assertEqual(second_page_status, 200)
        self.assertEqual(len(first_page["journal_reversals"]), 1)
        self.assertTrue(first_page["next_cursor"])
        self.assertEqual(len(second_page["journal_reversals"]), 1)
        self.assertIsNone(second_page["next_cursor"])
        self.assertEqual(
            [item["reversal_journal_reference"] for item in first_page["journal_reversals"]]
            + [item["reversal_journal_reference"] for item in second_page["journal_reversals"]],
            [item["reversal_journal_reference"] for item in listed["journal_reversals"]],
        )
        replay_status, replayed = self._http_json("POST", "/journal-reversals", reverse_body)
        after_replay_status, after_replay = self._http_journal_reversals()
        self.assertEqual(replay_status, 200)
        self.assertEqual(replayed, reversing)
        self.assertEqual(after_replay_status, 200)
        self.assertEqual(len(after_replay["journal_reversals"]), 2)

        missing_entity = self._http_json("GET", "/journal-reversals", None)
        missing_header = self._http_journal_reversals(tenant_header=None)
        cross_status, _cross = self._http_journal_reversals(
            tenant_header="urn:cwl:tenant_other"
        )
        unknown_entity = self._http_journal_reversals(
            legal_entity_reference="urn:cwl:legal_entity:missing"
        )
        unknown_period = self._http_journal_reversals(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01"
        )
        unknown_original = self._http_journal_reversals(
            original_journal_reference="urn:cwl:accounting:general_journal:missing"
        )
        bad_limit = self._http_journal_reversals(page_limit="abc")
        high_limit = self._http_journal_reversals(page_limit=101)
        bad_cursor = self._http_journal_reversals(cursor="not-a-cursor")
        empty_cursor = self._http_journal_reversals(cursor="|missing")
        bad_time_cursor = self._http_journal_reversals(cursor="not-a-time|journal")
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            lookup_journal_reversals(DATABASE_URL, self.policy.tenant_reference, "")
        with self.assertRaisesRegex(AccountingValidationError, "page_limit"):
            lookup_journal_reversals(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                page_limit=0,
            )

        self.assertEqual(missing_entity[0], 400)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(unknown_entity[0], 404)
        self.assertEqual(unknown_period[0], 404)
        self.assertEqual(unknown_original[0], 200)
        self.assertEqual(unknown_original[1]["journal_reversals"], [])
        self.assertEqual(bad_limit[0], 400)
        self.assertEqual(high_limit[0], 400)
        self.assertEqual(bad_cursor[0], 400)
        self.assertEqual(empty_cursor[0], 400)
        self.assertEqual(bad_time_cursor[0], 400)
        self.assertEqual(
            self._count_table("accounting_core.journal_reversal"), reversals_before + 2
        )
        server.shutdown()

    def test_http_lists_period_closes_without_sql(self) -> None:
        """GET /period-closes lists durable hard-close receipts; POST remains the command."""
        self._seed_additional_period("2026-09", date(2026, 9, 1), date(2026, 9, 30))
        invoice = self._billing_validated_payload()
        september = self._billing_validated_payload(
            proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf699",
            source_payload_hash="sha256:" + "c" * 64,
            idempotency_key=(
                f"{self.policy.tenant_reference}:invoice_draft:"
                "019d7b92-1aa0-7a7f-b61c-962c0f4bf699:"
                f"sha256:{'c' * 64}:v1"
            ),
            transaction_date="2026-09-15",
            accounting_date="2026-09-15",
            proposed_at="2026-09-15T00:00:00Z",
        )
        server = self._start_http_server()
        snapshots_before = self._count_table("accounting_reporting.trial_balance_snapshot")

        empty_status, empty_page = self._http_period_closes()
        empty_library = lookup_period_closes(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
        )
        self.assertEqual(empty_status, 200)
        self.assertEqual(empty_page, empty_library)
        self.assertEqual(empty_page["period_closes"], [])
        self.assertIsNone(empty_page["next_cursor"])
        self.assertEqual(empty_page["tenant_reference"], self.policy.tenant_reference)
        self.assertEqual(
            empty_page["legal_entity_reference"], self.policy.legal_entity_reference
        )
        self.assertNotIn("fiscal_period_reference", empty_page)
        self.assertNotIn("period_status_code", empty_page)

        post_status, _posted = self._http_json("POST", "/journal-proposals", invoice)
        soft_body = self._period_close_payload(period_status_code="soft_closed")
        soft_status, soft_receipt = self._http_json("POST", "/period-closes", soft_body)
        soft_list_status, soft_list = self._http_period_closes()
        self.assertEqual(post_status, 200)
        self.assertEqual(soft_status, 200)
        self.assertEqual(soft_receipt["period_status_code"], "soft_closed")
        self.assertEqual(soft_receipt["snapshot_record_id"], "")
        self.assertEqual(soft_list_status, 200)
        self.assertEqual(soft_list["period_closes"], [])
        self.assertEqual(
            self._count_table("accounting_reporting.trial_balance_snapshot"), snapshots_before
        )

        hard_body = self._period_close_payload()
        hard_status, hard_receipt = self._http_json("POST", "/period-closes", hard_body)
        replay_status, replay_receipt = self._http_json("POST", "/period-closes", hard_body)
        listed_status, listed = self._http_period_closes()
        library = lookup_period_closes(
            DATABASE_URL,
            self.policy.tenant_reference,
            self.policy.legal_entity_reference,
        )
        persist = PostgresPostingLedger(
            DATABASE_URL, self.policy.tenant_reference
        ).load_period_closes(self.policy.legal_entity_reference)
        self.assertEqual(hard_status, 200)
        self.assertEqual(replay_status, 200)
        self.assertFalse(hard_receipt["replayed"])
        self.assertTrue(replay_receipt["replayed"])
        self.assertEqual(replay_receipt["snapshot_record_id"], hard_receipt["snapshot_record_id"])
        self.assertEqual(listed_status, 200)
        self.assertEqual(listed, library)
        self.assertEqual(listed, persist)
        self.assertEqual(len(listed["period_closes"]), 1)
        self.assertEqual(listed["period_closes"][0], hard_receipt)
        self.assertEqual(
            self._count_table("accounting_reporting.trial_balance_snapshot"), snapshots_before + 1
        )

        september_status, _september_posted = self._http_json(
            "POST", "/journal-proposals", september
        )
        september_close_status, september_receipt = self._http_json(
            "POST",
            "/period-closes",
            self._period_close_payload(
                fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-09"
            ),
        )
        both_status, both = self._http_period_closes()
        filtered_status, filtered = self._http_period_closes(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:2026-08"
        )
        hard_filter_status, hard_filter = self._http_period_closes(
            period_status_code="hard_closed"
        )
        soft_filter_status, soft_filter = self._http_period_closes(
            period_status_code="soft_closed"
        )
        first_page_status, first_page = self._http_period_closes(page_limit=1)
        second_page_status, second_page = self._http_period_closes(
            page_limit=1,
            cursor=str(first_page["next_cursor"]),
        )
        by_period = {
            str(item["period_code"]): item for item in both["period_closes"]
        }

        self.assertEqual(september_status, 200)
        self.assertEqual(september_close_status, 200)
        self.assertEqual(both_status, 200)
        self.assertEqual(len(both["period_closes"]), 2)
        self.assertEqual(by_period["2026-08"], hard_receipt)
        self.assertEqual(by_period["2026-09"], september_receipt)
        self.assertEqual(filtered_status, 200)
        self.assertEqual(
            filtered["fiscal_period_reference"],
            "urn:cwl:accounting:fiscal_period:2026-08",
        )
        self.assertEqual(len(filtered["period_closes"]), 1)
        self.assertEqual(filtered["period_closes"][0], hard_receipt)
        self.assertEqual(hard_filter_status, 200)
        self.assertEqual(hard_filter["period_status_code"], "hard_closed")
        self.assertEqual(len(hard_filter["period_closes"]), 2)
        self.assertEqual(soft_filter_status, 200)
        self.assertEqual(soft_filter["period_status_code"], "soft_closed")
        self.assertEqual(soft_filter["period_closes"], [])
        self.assertEqual(first_page_status, 200)
        self.assertEqual(second_page_status, 200)
        self.assertEqual(len(first_page["period_closes"]), 1)
        self.assertTrue(first_page["next_cursor"])
        self.assertEqual(len(second_page["period_closes"]), 1)
        self.assertIsNone(second_page["next_cursor"])
        self.assertEqual(
            [item["snapshot_record_id"] for item in first_page["period_closes"]]
            + [item["snapshot_record_id"] for item in second_page["period_closes"]],
            [item["snapshot_record_id"] for item in both["period_closes"]],
        )

        missing_entity = self._http_json("GET", "/period-closes", None)
        missing_header = self._http_period_closes(tenant_header=None)
        cross_status, _cross = self._http_period_closes(tenant_header="urn:cwl:tenant_other")
        unknown_entity = self._http_period_closes(
            legal_entity_reference="urn:cwl:legal_entity:missing"
        )
        unknown_period = self._http_period_closes(
            fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01"
        )
        bad_limit = self._http_period_closes(page_limit="abc")
        high_limit = self._http_period_closes(page_limit=101)
        bad_status = self._http_period_closes(period_status_code="open")
        bad_cursor = self._http_period_closes(cursor="not-a-cursor")
        empty_cursor = self._http_period_closes(cursor="|missing")
        bad_time_cursor = self._http_period_closes(cursor="not-a-time|journal")
        bad_id_cursor = self._http_period_closes(
            cursor="2026-08-31T00:00:00Z|not-a-uuid"
        )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            lookup_period_closes(DATABASE_URL, self.policy.tenant_reference, "")
        with self.assertRaisesRegex(AccountingValidationError, "page_limit"):
            lookup_period_closes(
                DATABASE_URL,
                self.policy.tenant_reference,
                self.policy.legal_entity_reference,
                page_limit=0,
            )

        self.assertEqual(missing_entity[0], 400)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(unknown_entity[0], 404)
        self.assertEqual(unknown_period[0], 404)
        self.assertEqual(bad_limit[0], 400)
        self.assertEqual(high_limit[0], 400)
        self.assertEqual(bad_status[0], 400)
        self.assertEqual(bad_cursor[0], 400)
        self.assertEqual(empty_cursor[0], 400)
        self.assertEqual(bad_time_cursor[0], 400)
        self.assertEqual(bad_id_cursor[0], 400)
        self.assertEqual(
            self._count_table("accounting_reporting.trial_balance_snapshot"),
            snapshots_before + 2,
        )
        server.shutdown()

    def test_http_posts_ais_adjusting_journal_without_billing_role(self) -> None:
        """POST /journals posts an AIS-owned adjusting journal; Billing ingest stays on /journal-proposals."""
        body = self._adjusting_journal_payload()
        server = self._start_http_server()
        journals_before = self._count_table("accounting_core.general_journal")
        receipts_before = self._count_table("accounting_integration.posting_receipt")

        open_status, posted = self._http_json("POST", "/journals", body)
        replay_status, replayed = self._http_json("POST", "/journals", body)
        library = accept_adjusting_journal(
            body, DATABASE_URL, self.policy.tenant_reference
        )
        inquiry_status, inquiry = self._http_journal(
            idempotency_key=str(body["idempotency_key"])
        )
        listed_status, listed = self._http_period_journals()
        by_code = {str(item["chart_account_code"]): item for item in inquiry["lines"]}

        self.assertEqual(open_status, 200)
        self.assertEqual(replay_status, 200)
        self.assertEqual(posted, replayed)
        self.assertEqual(posted, library)
        self.assertEqual(posted["posting_status_code"], "posted")
        self.assertEqual(posted["idempotency_key"], body["idempotency_key"])
        self.assertEqual(posted["line_count"], 2)
        self.assertEqual(inquiry_status, 200)
        self.assertEqual(inquiry["journal_reference"], posted["journal_reference"])
        self.assertEqual(inquiry["idempotency_key"], body["idempotency_key"])
        self.assertEqual(inquiry["accounting_date"], "2026-08-31")
        self.assertEqual(Decimal(str(by_code["110100"]["debit_amount"])), Decimal("1000"))
        self.assertEqual(Decimal(str(by_code["110100"]["credit_amount"])), Decimal("0"))
        self.assertEqual(Decimal(str(by_code["410100"]["debit_amount"])), Decimal("0"))
        self.assertEqual(Decimal(str(by_code["410100"]["credit_amount"])), Decimal("1000"))
        self.assertEqual(listed_status, 200)
        self.assertIn(
            body["idempotency_key"],
            {str(item["idempotency_key"]) for item in listed["journals"]},
        )
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before + 1)
        self.assertEqual(
            self._count_table("accounting_integration.posting_receipt"), receipts_before + 1
        )
        aliased = accept_adjusting_journal(
            {
                "tenant_reference": self.policy.tenant_reference,
                "legal_entity_reference": self.policy.legal_entity_reference,
                "accounting_book_reference": self.policy.accounting_book_reference,
                "period_code": "2026-08",
                "journal_date": "2026-08-31",
                "idempotency_key": f"{self.policy.tenant_reference}:adjusting_journal:alias:v1",
                "journal_description": "Period code alias",
                "journal_lines": body["journal_lines"],
            },
            DATABASE_URL,
            self.policy.tenant_reference,
        )
        self.assertEqual(aliased["posting_status_code"], "posted")
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before + 2)
        empty_book = self._seed_book_without_chart_accounts()
        with self.assertRaisesRegex(AccountingValidationError, "account_role_mapping"):
            accept_adjusting_journal(
                self._adjusting_journal_payload(
                    accounting_book_reference=empty_book,
                    idempotency_key=f"{self.policy.tenant_reference}:adjusting_journal:empty-book:v1",
                ),
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        unknown_chart = self._http_json(
            "POST",
            "/journals",
            self._adjusting_journal_payload(
                idempotency_key=f"{self.policy.tenant_reference}:adjusting_journal:missing-account:v1",
                journal_lines=[
                    {
                        "chart_account_code": "999999",
                        "debit_credit_code": "debit",
                        "amount": "1000",
                        "currency_code": "KRW",
                    },
                    {
                        "chart_account_code": "410100",
                        "debit_credit_code": "credit",
                        "amount": "1000",
                        "currency_code": "KRW",
                    },
                ],
            ),
        )
        self.assertEqual(unknown_chart[0], 422)

        soft_status, _soft = self._http_json(
            "POST", "/period-closes", self._period_close_payload(period_status_code="soft_closed")
        )
        soft_body = self._adjusting_journal_payload(
            idempotency_key=f"{self.policy.tenant_reference}:adjusting_journal:soft:v1",
            journal_description="Soft-close adjusting accrual",
        )
        soft_adjust_status, soft_adjust = self._http_json("POST", "/journals", soft_body)
        self.assertEqual(soft_status, 200)
        self.assertEqual(soft_adjust_status, 200)
        self.assertEqual(soft_adjust["posting_status_code"], "posted")
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before + 3)

        hard_status, _hard = self._http_json("POST", "/period-closes", self._period_close_payload())
        closed_body = self._adjusting_journal_payload(
            idempotency_key=f"{self.policy.tenant_reference}:adjusting_journal:closed:v1",
            journal_description="Rejected after hard-close",
        )
        closed_status, closed_body_doc = self._http_json("POST", "/journals", closed_body)
        replay_after_close_status, replay_after_close = self._http_json("POST", "/journals", body)
        self.assertEqual(hard_status, 200)
        self.assertEqual(closed_status, 409)
        self.assertIn("hard_closed", str(closed_body_doc["error_message"]))
        self.assertEqual(replay_after_close_status, 200)
        self.assertEqual(replay_after_close, posted)
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before + 4)

        unbalanced = self._http_json(
            "POST",
            "/journals",
            self._adjusting_journal_payload(
                idempotency_key=f"{self.policy.tenant_reference}:adjusting_journal:unbalanced:v1",
                journal_lines=[
                    {
                        "chart_account_code": "110100",
                        "debit_credit_code": "debit",
                        "amount": "1000",
                        "currency_code": "KRW",
                    },
                    {
                        "chart_account_code": "410100",
                        "debit_credit_code": "credit",
                        "amount": "900",
                        "currency_code": "KRW",
                    },
                ],
            ),
        )
        outside_period = self._http_json(
            "POST",
            "/journals",
            self._adjusting_journal_payload(
                journal_date="2026-09-15",
                idempotency_key=f"{self.policy.tenant_reference}:adjusting_journal:outside:v1",
            ),
        )
        before_period = self._http_json(
            "POST",
            "/journals",
            self._adjusting_journal_payload(
                journal_date="2026-07-15",
                idempotency_key=f"{self.policy.tenant_reference}:adjusting_journal:before:v1",
            ),
        )
        missing_header = self._http_json("POST", "/journals", body, tenant_header=None)
        bad_json = self._http_raw("POST", "/journals", b"{", self.policy.tenant_reference)
        cross_status, _cross = self._http_json(
            "POST", "/journals", body, tenant_header="urn:cwl:tenant_other"
        )
        body_mismatch = self._http_json(
            "POST",
            "/journals",
            self._adjusting_journal_payload(tenant_reference="urn:cwl:tenant_other"),
        )
        unknown_entity = self._http_json(
            "POST",
            "/journals",
            self._adjusting_journal_payload(
                legal_entity_reference="urn:cwl:legal_entity:missing",
                idempotency_key=f"{self.policy.tenant_reference}:adjusting_journal:missing-entity:v1",
            ),
        )
        unknown_book = self._http_json(
            "POST",
            "/journals",
            self._adjusting_journal_payload(
                accounting_book_reference="urn:cwl:accounting_book:missing",
                idempotency_key=f"{self.policy.tenant_reference}:adjusting_journal:missing-book:v1",
            ),
        )
        unknown_period = self._http_json(
            "POST",
            "/journals",
            self._adjusting_journal_payload(
                fiscal_period_reference="urn:cwl:accounting:fiscal_period:1999-01",
                idempotency_key=f"{self.policy.tenant_reference}:adjusting_journal:missing-period:v1",
            ),
        )
        bad_side = self._http_json(
            "POST",
            "/journals",
            self._adjusting_journal_payload(
                idempotency_key=f"{self.policy.tenant_reference}:adjusting_journal:side:v1",
                journal_lines=[
                    {
                        "chart_account_code": "110100",
                        "debit_credit_code": "both",
                        "amount": "1000",
                        "currency_code": "KRW",
                    },
                    {
                        "chart_account_code": "410100",
                        "debit_credit_code": "credit",
                        "amount": "1000",
                        "currency_code": "KRW",
                    },
                ],
            ),
        )
        bad_amount = self._http_json(
            "POST",
            "/journals",
            self._adjusting_journal_payload(
                idempotency_key=f"{self.policy.tenant_reference}:adjusting_journal:amount:v1",
                journal_lines=[
                    {
                        "chart_account_code": "110100",
                        "debit_credit_code": "debit",
                        "amount": "1,000",
                        "currency_code": "KRW",
                    },
                    {
                        "chart_account_code": "410100",
                        "debit_credit_code": "credit",
                        "amount": "1000",
                        "currency_code": "KRW",
                    },
                ],
            ),
        )
        mixed_currency = self._http_json(
            "POST",
            "/journals",
            self._adjusting_journal_payload(
                idempotency_key=f"{self.policy.tenant_reference}:adjusting_journal:fx:v1",
                journal_lines=[
                    {
                        "chart_account_code": "110100",
                        "debit_credit_code": "debit",
                        "amount": "1000",
                        "currency_code": "USD",
                    },
                    {
                        "chart_account_code": "410100",
                        "debit_credit_code": "credit",
                        "amount": "1000",
                        "currency_code": "KRW",
                    },
                ],
            ),
        )
        wrong_book_currency = self._http_json(
            "POST",
            "/journals",
            self._adjusting_journal_payload(
                idempotency_key=f"{self.policy.tenant_reference}:adjusting_journal:usd:v1",
                journal_lines=[
                    {
                        "chart_account_code": "110100",
                        "debit_credit_code": "debit",
                        "amount": "1000",
                        "currency_code": "USD",
                    },
                    {
                        "chart_account_code": "410100",
                        "debit_credit_code": "credit",
                        "amount": "1000",
                        "currency_code": "USD",
                    },
                ],
            ),
        )
        conflict = self._http_json(
            "POST",
            "/journals",
            self._adjusting_journal_payload(
                journal_description="Different payload on the same key",
            ),
        )
        with self.assertRaisesRegex(AccountingValidationError, "JSON object"):
            accept_adjusting_journal(["not-an-object"], DATABASE_URL, self.policy.tenant_reference)
        with self.assertRaisesRegex(AccountingValidationError, "bound tenant"):
            accept_adjusting_journal(
                self._adjusting_journal_payload(tenant_reference="urn:cwl:tenant_other"),
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            accept_adjusting_journal(
                {
                    "tenant_reference": self.policy.tenant_reference,
                    "journal_date": "2026-08-31",
                    "idempotency_key": "missing-scope",
                    "journal_description": "Missing scope",
                    "journal_lines": body["journal_lines"],
                },
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            accept_adjusting_journal(
                self._adjusting_journal_payload(accounting_book_reference=""),
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "legal_entity_reference"):
            accept_adjusting_journal(
                self._adjusting_journal_payload(
                    fiscal_period_reference="",
                    period_code="",
                    idempotency_key=f"{self.policy.tenant_reference}:adjusting_journal:missing-period-code:v1",
                ),
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "idempotency_key"):
            accept_adjusting_journal(
                self._adjusting_journal_payload(idempotency_key=""),
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "journal_description"):
            accept_adjusting_journal(
                self._adjusting_journal_payload(journal_description=""),
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "journal_date"):
            accept_adjusting_journal(
                self._adjusting_journal_payload(journal_date="31-08-2026"),
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "two lines"):
            accept_adjusting_journal(
                self._adjusting_journal_payload(journal_lines=[body["journal_lines"][0]]),
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "journal_lines"):
            accept_adjusting_journal(
                self._adjusting_journal_payload(journal_lines="not-a-list"),
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "journal line"):
            accept_adjusting_journal(
                self._adjusting_journal_payload(
                    journal_lines=["not-an-object", body["journal_lines"][1]]
                ),
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "chart_account_code"):
            accept_adjusting_journal(
                self._adjusting_journal_payload(
                    journal_lines=[
                        {
                            "debit_credit_code": "debit",
                            "amount": "1000",
                            "currency_code": "KRW",
                        },
                        body["journal_lines"][1],
                    ]
                ),
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(IdempotencyConflictError, "idempotency key"):
            accept_adjusting_journal(
                self._adjusting_journal_payload(
                    journal_description="Different payload on the same key",
                ),
                DATABASE_URL,
                self.policy.tenant_reference,
            )

        self.assertEqual(unbalanced[0], 422)
        self.assertEqual(outside_period[0], 422)
        self.assertEqual(before_period[0], 422)
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(bad_json[0], 400)
        self.assertEqual(cross_status, 403)
        self.assertEqual(body_mismatch[0], 403)
        self.assertEqual(unknown_entity[0], 404)
        self.assertEqual(unknown_book[0], 404)
        self.assertEqual(unknown_period[0], 404)
        self.assertEqual(bad_side[0], 422)
        self.assertEqual(bad_amount[0], 422)
        self.assertEqual(mixed_currency[0], 422)
        self.assertEqual(wrong_book_currency[0], 422)
        self.assertEqual(conflict[0], 409)
        self.assertEqual(self._count_table("accounting_core.general_journal"), journals_before + 4)
        server.shutdown()

    def test_accept_and_http_guard_cross_tenant_and_operator_failures(self) -> None:
        """The tenant header is purpose-limited and cross-tenant posts write zero rows."""
        payload = self._billing_validated_payload()
        with self.assertRaisesRegex(AccountingValidationError, "JSON object"):
            accept_journal_proposal(["not-an-object"], DATABASE_URL, self.policy.tenant_reference)
        with self.assertRaisesRegex(AccountingValidationError, "bound tenant"):
            accept_journal_proposal(
                self._billing_validated_payload(tenant_reference="urn:cwl:tenant_other"),
                DATABASE_URL,
                self.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "posting receipt is missing"):
            self.ledger.load_published_receipt(ingest_journal_proposal(payload))

        server = self._start_http_server()
        missing_header = self._http_json(
            "POST", "/journal-proposals", payload, tenant_header=None
        )
        wrong_header = self._http_json(
            "POST",
            "/journal-proposals",
            payload,
            tenant_header="urn:cwl:tenant_other",
        )
        payload_mismatch = self._http_json(
            "POST",
            "/journal-proposals",
            self._billing_validated_payload(tenant_reference="urn:cwl:tenant_other"),
        )
        bad_json = self._http_raw("POST", "/journal-proposals", b"{", self.policy.tenant_reference)
        empty = self._http_raw("POST", "/journal-proposals", b"", self.policy.tenant_reference)
        not_object = self._http_raw(
            "POST", "/journal-proposals", b"[1]", self.policy.tenant_reference
        )
        invalid_bytes = self._http_raw(
            "POST", "/journal-proposals", b"\xff\xfe", self.policy.tenant_reference
        )
        invalid_length = self._http_invalid_length()
        unknown_path = self._http_json("POST", "/unknown", payload)
        get_status, _get_body = self._http_json("GET", "/journal-proposals", None)
        conflict_payload = self._billing_validated_payload(
            proposal_id=str(uuid.uuid4()),
            source_payload_hash="sha256:" + "c" * 64,
        )
        accept_journal_proposal(payload, DATABASE_URL, self.policy.tenant_reference)
        conflict_status, _conflict_body = self._http_json(
            "POST", "/journal-proposals", conflict_payload
        )
        self.assertEqual(missing_header[0], 400)
        self.assertEqual(wrong_header[0], 403)
        self.assertEqual(payload_mismatch[0], 403)
        self.assertEqual(bad_json[0], 400)
        self.assertEqual(empty[0], 400)
        self.assertEqual(not_object[0], 400)
        self.assertEqual(invalid_bytes[0], 400)
        self.assertEqual(invalid_length[0], 400)
        self.assertEqual(unknown_path[0], 404)
        self.assertEqual(get_status, 405)
        self.assertEqual(conflict_status, 409)
        self.assertEqual(self._count_table("accounting_core.general_journal"), 1)
        with self.assertRaisesRegex(AccountingValidationError, "Set a PostgreSQL 18 URL"):
            create_journal_proposal_server("", self.policy.tenant_reference)
        with mock.patch.dict(
            os.environ,
            {
                "ACCOUNTING_DATABASE_URL": DATABASE_URL,
                "ACCOUNTING_TENANT_REFERENCE": self.policy.tenant_reference,
                "PORT": "not-a-port",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(AccountingValidationError, "PORT must be an integer"):
                run_journal_proposal_server(host="127.0.0.1")
        with mock.patch.dict(
            os.environ,
            {
                "ACCOUNTING_DATABASE_URL": DATABASE_URL,
                "ACCOUNTING_TENANT_REFERENCE": self.policy.tenant_reference,
                "PORT": "0",
            },
            clear=False,
        ):
            started = run_journal_proposal_server(serve=lambda: None)
            started.server_close()
        with mock.patch(
            "accounting_information_platform.http_api.JournalProposalServer.serve_forever",
            return_value=None,
        ):
            bound = run_journal_proposal_server(
                DATABASE_URL, self.policy.tenant_reference, "127.0.0.1", 0
            )
            bound.server_close()
        with mock.patch(
            "accounting_information_platform.http_api.create_journal_proposal_server"
        ) as create_server:
            fake_server = mock.Mock()
            create_server.return_value = fake_server
            env = {
                key: value
                for key, value in os.environ.items()
                if key != "PORT"
            }
            env["ACCOUNTING_DATABASE_URL"] = DATABASE_URL
            env["ACCOUNTING_TENANT_REFERENCE"] = self.policy.tenant_reference
            with mock.patch.dict(os.environ, env, clear=True):
                run_journal_proposal_server(host="127.0.0.1")
            self.assertEqual(create_server.call_args.args[3], 8080)
            fake_server.serve_forever.assert_called_once()
        server.shutdown()

    def test_post_proposal_catalog_misses_write_zero_rows(self) -> None:
        """Unmapped roles, missing books, and closed periods write no durable rows."""
        self._delete_role_mapping("tax_payable")
        with self.assertRaisesRegex(AccountingValidationError, "Create the account_role_mapping row"):
            self.ledger.post_proposal(
                ingest_journal_proposal(self._billing_taxed_payload())
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
            for (
                account_code,
                account_name,
                normal_balance_code,
                account_class_code,
                account_role_code,
            ) in (
                ("110100", "Accounts receivable", "debit", "asset", "accounts_receivable"),
                ("410100", "Usage revenue", "credit", "revenue", "usage_revenue"),
                ("110200", "Cash receipts", "debit", "asset", "cash_receipt"),
                ("210100", "Tax payable", "credit", "liability", "tax_payable"),
                ("310100", "Retained earnings", "credit", "equity", "retained_earnings"),
                ("510100", "Write-off expense", "debit", "expense", "write_off_expense"),
                ("210200", "unapplied_cash", "credit", "liability", "unapplied_cash"),
            ):
                chart_account_id = connection.execute(
                    """
                    INSERT INTO accounting_core.chart_account (
                        tenant_account_id, accounting_book_id, chart_account_code,
                        account_name, normal_balance_code, account_class_code, valid_from
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    RETURNING chart_account_id
                    """,
                    (
                        tenant_id,
                        book_id,
                        account_code,
                        account_name,
                        normal_balance_code,
                        account_class_code,
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
                        account_role_code,
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

    def _count_closing_journals(self) -> int:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (self.tenant_id,),
            )
            return connection.execute(
                """
                SELECT COUNT(*)
                FROM accounting_core.general_journal
                WHERE tenant_account_id = %s
                  AND journal_reference LIKE %s
                """,
                (
                    self.tenant_id,
                    "urn:cwl:accounting:general_journal:period_closing:%",
                ),
            ).fetchone()[0]

    def _seed_issued_capital_account(self) -> None:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (self.tenant_id,),
            )
            book_id = connection.execute(
                """
                SELECT accounting_book_id
                FROM accounting_core.accounting_book
                WHERE tenant_account_id = %s
                """,
                (self.tenant_id,),
            ).fetchone()[0]
            chart_account_id = connection.execute(
                """
                INSERT INTO accounting_core.chart_account (
                    tenant_account_id, accounting_book_id, chart_account_code,
                    account_name, normal_balance_code, account_class_code, valid_from
                )
                VALUES (%s, %s, '320100', 'Issued capital', 'credit', 'equity', %s)
                RETURNING chart_account_id
                """,
                (self.tenant_id, book_id, VALID_FROM),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO accounting_core.account_role_mapping (
                    tenant_account_id, accounting_book_id, account_role_code,
                    chart_account_id, accounting_policy_version, posting_rule_version,
                    valid_from
                )
                VALUES (%s, %s, 'issued_capital', %s, 'ifrs-v1', 'billing-issued-v1', %s)
                """,
                (self.tenant_id, book_id, chart_account_id, VALID_FROM),
            )
            connection.commit()

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

    def _billing_taxed_payload(self, **overrides: object) -> dict[str, object]:
        source_payload_hash = "sha256:" + "1" * 64
        invoice_draft_id = "019d7b92-3cc2-7a7f-b61c-962c0f4bf614"
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
                    "debit_amount": "27500",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "0",
                    "credit_amount": "25000",
                },
                {
                    "line_number": 3,
                    "account_role_code": "tax_payable",
                    "debit_amount": "0",
                    "credit_amount": "2500",
                },
            ],
        }
        values.update(overrides)
        return values

    def _billing_cash_payload(self, **overrides: object) -> dict[str, object]:
        source_payload_hash = "sha256:" + "c" * 64
        cash_receipt_id = "019d7b92-2bb1-7a7f-b61c-962c0f4bf613"
        values: dict[str, object] = {
            "proposal_id": cash_receipt_id,
            "proposal_contract_version": 1,
            "idempotency_key": (
                f"{self.policy.tenant_reference}:cash_receipt:{cash_receipt_id}"
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
                f"{self.policy.tenant_reference}:cash_receipt:{cash_receipt_id}",
            ),
            "lines": [
                {
                    "line_number": 1,
                    "account_role_code": "cash_receipt",
                    "debit_amount": "18000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0",
                    "credit_amount": "18000",
                },
            ],
        }
        values.update(overrides)
        return values

    def _billing_credit_payload(self, **overrides: object) -> dict[str, object]:
        source_payload_hash = "sha256:" + "b" * 64
        credit_adjustment_id = "11111111-1111-1111-1111-111111111111"
        values: dict[str, object] = {
            "proposal_id": credit_adjustment_id,
            "proposal_contract_version": 1,
            "idempotency_key": (
                f"{self.policy.tenant_reference}:credit_adjustment:{credit_adjustment_id}"
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
                f"{self.policy.tenant_reference}:credit_adjustment:{credit_adjustment_id}",
            ),
            "lines": [
                {
                    "line_number": 1,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "4000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0",
                    "credit_amount": "4000",
                },
            ],
        }
        values.update(overrides)
        return values

    def _billing_taxed_credit_payload(self, **overrides: object) -> dict[str, object]:
        source_payload_hash = "sha256:" + "3" * 64
        credit_adjustment_id = "22222222-2222-2222-2222-222222222222"
        values: dict[str, object] = {
            "proposal_id": credit_adjustment_id,
            "proposal_contract_version": 1,
            "idempotency_key": (
                f"{self.policy.tenant_reference}:credit_adjustment:{credit_adjustment_id}"
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
                f"{self.policy.tenant_reference}:credit_adjustment:{credit_adjustment_id}",
            ),
            "lines": [
                {
                    "line_number": 1,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "25000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "tax_payable",
                    "debit_amount": "2500",
                    "credit_amount": "0",
                },
                {
                    "line_number": 3,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0",
                    "credit_amount": "27500",
                },
            ],
        }
        values.update(overrides)
        return values

    def _billing_write_off_payload(self, **overrides: object) -> dict[str, object]:
        source_payload_hash = "sha256:" + "5" * 64
        collection_write_off_id = "019d7b92-5ee4-7a7f-b61c-962c0f4bf617"
        values: dict[str, object] = {
            "proposal_id": collection_write_off_id,
            "proposal_contract_version": 1,
            "idempotency_key": (
                f"{self.policy.tenant_reference}:collection_write_off:"
                f"{collection_write_off_id}:{source_payload_hash}:v1"
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
                f"{self.policy.tenant_reference}:collection_write_off:{collection_write_off_id}",
            ),
            "lines": [
                {
                    "line_number": 1,
                    "account_role_code": "write_off_expense",
                    "debit_amount": "7000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0",
                    "credit_amount": "7000",
                },
            ],
        }
        values.update(overrides)
        return values

    def _billing_issued_invoice_void_payload(self, **overrides: object) -> dict[str, object]:
        issued_invoice_void_id = str(
            overrides.pop("issued_invoice_void_id", "019d7b92-9dd6-7a7f-b61c-962c0f4bf630")
        )
        void_source_payload_hash = str(
            overrides.pop("void_source_payload_hash", "sha256:" + "7" * 64)
        )
        issued_invoice_void_contract_version = int(
            overrides.pop("issued_invoice_void_contract_version", 1)
        )
        source_payload_hash = "sha256:" + "4" * 64
        proposal_id = "019d7b92-9ee8-7a7f-b61c-962c0f4bf640"
        values: dict[str, object] = {
            "proposal_id": proposal_id,
            "proposal_contract_version": 1,
            "idempotency_key": (
                f"{self.policy.tenant_reference}:issued_invoice_void:"
                f"{issued_invoice_void_id}:{void_source_payload_hash}"
                f":v{issued_invoice_void_contract_version}"
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
                f"{self.policy.tenant_reference}:issued_invoice_void:"
                f"{issued_invoice_void_id}",
            ),
            "lines": [
                {
                    "line_number": 1,
                    "account_role_code": "usage_revenue",
                    "debit_amount": "25000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "tax_payable",
                    "debit_amount": "2500",
                    "credit_amount": "0",
                },
                {
                    "line_number": 3,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0",
                    "credit_amount": "27500",
                },
            ],
        }
        values.update(overrides)
        return values

    def _billing_unapplied_cash_refund_payload(self, **overrides: object) -> dict[str, object]:
        source_payload_hash = "sha256:" + "8" * 64
        unapplied_cash_refund_id = "019d7b92-8cc5-7a7f-b61c-962c0f4bf621"
        values: dict[str, object] = {
            "proposal_id": unapplied_cash_refund_id,
            "proposal_contract_version": 1,
            "idempotency_key": (
                f"{self.policy.tenant_reference}:unapplied_cash_refund:"
                f"{unapplied_cash_refund_id}:{source_payload_hash}:v1"
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
                f"{self.policy.tenant_reference}:unapplied_cash_refund:"
                f"{unapplied_cash_refund_id}",
            ),
            "lines": [
                {
                    "line_number": 1,
                    "account_role_code": "unapplied_cash",
                    "debit_amount": "8000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "cash_receipt",
                    "debit_amount": "0",
                    "credit_amount": "8000",
                },
            ],
        }
        values.update(overrides)
        return values

    def _billing_unapplied_cash_park_payload(self, **overrides: object) -> dict[str, object]:
        source_payload_hash = "sha256:" + "4" * 64
        unapplied_cash_id = "019d7b92-8cc5-7a7f-b61c-962c0f4bf622"
        values: dict[str, object] = {
            "proposal_id": unapplied_cash_id,
            "proposal_contract_version": 1,
            "idempotency_key": (
                f"{self.policy.tenant_reference}:unapplied_cash:"
                f"{unapplied_cash_id}:{source_payload_hash}:v1"
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
                f"{self.policy.tenant_reference}:unapplied_cash:{unapplied_cash_id}",
            ),
            "lines": [
                {
                    "line_number": 1,
                    "account_role_code": "cash_receipt",
                    "debit_amount": "3000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "unapplied_cash",
                    "debit_amount": "0",
                    "credit_amount": "3000",
                },
            ],
        }
        values.update(overrides)
        return values

    def _billing_unapplied_cash_application_payload(
        self, **overrides: object
    ) -> dict[str, object]:
        source_payload_hash = "sha256:" + "d" * 64
        unapplied_cash_application_id = "019d7b92-8cc5-7a7f-b61c-962c0f4bf624"
        values: dict[str, object] = {
            "proposal_id": unapplied_cash_application_id,
            "proposal_contract_version": 1,
            "idempotency_key": (
                f"{self.policy.tenant_reference}:unapplied_cash_application:"
                f"{unapplied_cash_application_id}:{source_payload_hash}:v1"
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
                f"{self.policy.tenant_reference}:unapplied_cash_application:"
                f"{unapplied_cash_application_id}",
            ),
            "lines": [
                {
                    "line_number": 1,
                    "account_role_code": "unapplied_cash",
                    "debit_amount": "7000",
                    "credit_amount": "0",
                },
                {
                    "line_number": 2,
                    "account_role_code": "accounts_receivable",
                    "debit_amount": "0",
                    "credit_amount": "7000",
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

    def _delete_role_mapping(self, account_role_code: str) -> None:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (self.tenant_id,),
            )
            connection.execute(
                """
                DELETE FROM accounting_core.account_role_mapping
                WHERE tenant_account_id = %s AND account_role_code = %s
                """,
                (self.tenant_id, account_role_code),
            )
            connection.commit()

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

    def _assert_published_receipt(
        self, document: dict[str, object], payload: dict[str, object]
    ) -> None:
        required = {
            "receipt_id",
            "receipt_contract_version",
            "idempotency_key",
            "source_proposal_id",
            "source_payload_hash",
            "tenant_reference",
            "legal_entity_reference",
            "accounting_book_reference",
            "accounting_policy_version",
            "posting_rule_version",
            "posting_status_code",
            "recorded_at",
        }
        self.assertTrue(required <= set(document))
        self.assertEqual(document["receipt_contract_version"], 1)
        self.assertEqual(document["idempotency_key"], payload["idempotency_key"])
        self.assertEqual(document["source_proposal_id"], payload["proposal_id"])
        self.assertEqual(document["source_payload_hash"], payload["source_payload_hash"])
        self.assertEqual(document["tenant_reference"], self.policy.tenant_reference)
        self.assertEqual(document["posting_status_code"], "posted")
        self.assertEqual(document["accounting_policy_version"], "ifrs-v1")
        self.assertEqual(document["posting_rule_version"], "billing-issued-v1")
        self.assertEqual(document["accounting_book_reference"], self.policy.accounting_book_reference)
        self.assertEqual(document["line_count"], 2)
        uuid.UUID(str(document["receipt_id"]))

    def _start_http_server(self, tenant_reference: str | None = None):
        server = create_journal_proposal_server(
            DATABASE_URL,
            self.policy.tenant_reference if tenant_reference is None else tenant_reference,
            "127.0.0.1",
            0,
        )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self._http_server = server
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def _start_fake_billing(
        self,
        proposals: list[object],
        *,
        list_status: int = 200,
        get_status: int = 200,
        list_raw: bytes | None = None,
        get_raw: bytes | None = None,
    ) -> str:
        server = FakeBillingServer(
            ("127.0.0.1", 0),
            proposals,
            list_status=list_status,
            get_status=get_status,
            list_raw=list_raw,
            get_raw=get_raw,
        )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self._last_fake_billing = server
        host, port = server.server_address
        return f"http://{host}:{port}"

    def _adjusting_journal_payload(self, **overrides: object) -> dict[str, object]:
        values: dict[str, object] = {
            "tenant_reference": self.policy.tenant_reference,
            "legal_entity_reference": self.policy.legal_entity_reference,
            "accounting_book_reference": self.policy.accounting_book_reference,
            "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
            "journal_date": "2026-08-31",
            "idempotency_key": f"{self.policy.tenant_reference}:adjusting_journal:accrual:v1",
            "journal_description": "Accrue unbilled receivable",
            "journal_lines": [
                {
                    "chart_account_code": "110100",
                    "debit_credit_code": "debit",
                    "amount": "1000",
                    "currency_code": "KRW",
                },
                {
                    "chart_account_code": "410100",
                    "debit_credit_code": "credit",
                    "amount": "1000",
                    "currency_code": "KRW",
                },
            ],
        }
        values.update(overrides)
        return values

    def _period_close_payload(self, **overrides: object) -> dict[str, object]:
        values: dict[str, object] = {
            "tenant_reference": self.policy.tenant_reference,
            "legal_entity_reference": self.policy.legal_entity_reference,
            "book_reference": self.policy.accounting_book_reference,
            "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-08",
            "closed_by_actor_reference": "urn:cwl:actor:controller",
            "snapshot_currency_code": "KRW",
        }
        values.update(overrides)
        return values

    def _seed_entity_without_books(self) -> str:
        entity_code = f"urn:cwl:legal_entity:nobooks_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (self.tenant_id,),
            )
            connection.execute(
                """
                INSERT INTO accounting_core.legal_entity_record (
                    tenant_account_id, legal_entity_code, entity_name,
                    functional_currency_code, valid_from
                )
                VALUES (%s, %s, %s, 'KRW', %s)
                """,
                (self.tenant_id, entity_code, "Entity without books", VALID_FROM),
            )
            connection.commit()
        return entity_code

    def _seed_book_without_chart_accounts(self) -> str:
        book_name = f"urn:cwl:accounting_book:empty_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (self.tenant_id,),
            )
            legal_entity_id = connection.execute(
                """
                SELECT legal_entity_id
                FROM accounting_core.legal_entity_record
                WHERE tenant_account_id = %s AND legal_entity_code = %s
                """,
                (self.tenant_id, self.policy.legal_entity_reference),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO accounting_core.accounting_book (
                    tenant_account_id, legal_entity_id, book_role_code, book_name,
                    reporting_currency_code, valid_from
                )
                VALUES (%s, %s, 'management', %s, 'KRW', %s)
                """,
                (self.tenant_id, legal_entity_id, book_name, VALID_FROM),
            )
            connection.commit()
        return book_name

    def _seed_tenant_without_entities(self) -> str:
        tenant_code = f"urn:cwl:tenant:noentities_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                INSERT INTO accounting_core.tenant_account (tenant_account_code)
                VALUES (%s)
                """,
                (tenant_code,),
            )
            connection.commit()
        return tenant_code

    def _seed_tenant_without_calendar(self) -> tuple[str, str]:
        tenant_code = f"urn:cwl:tenant:nocalendar_{uuid.uuid4().hex[:8]}"
        entity_code = f"urn:cwl:legal_entity:nocalendar_{uuid.uuid4().hex[:8]}"
        with psycopg.connect(DATABASE_URL) as connection:
            tenant_id = connection.execute(
                """
                INSERT INTO accounting_core.tenant_account (tenant_account_code)
                VALUES (%s)
                RETURNING tenant_account_id
                """,
                (tenant_code,),
            ).fetchone()[0]
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(tenant_id),),
            )
            connection.execute(
                """
                INSERT INTO accounting_core.legal_entity_record (
                    tenant_account_id, legal_entity_code, entity_name,
                    functional_currency_code, valid_from
                )
                VALUES (%s, %s, %s, 'KRW', %s)
                """,
                (tenant_id, entity_code, "No calendar entity", VALID_FROM),
            )
            connection.commit()
        return tenant_code, entity_code

    def _period_open_payload(self, **overrides: object) -> dict[str, object]:
        values: dict[str, object] = {
            "tenant_reference": self.policy.tenant_reference,
            "legal_entity_reference": self.policy.legal_entity_reference,
            "fiscal_period_reference": "urn:cwl:accounting:fiscal_period:2026-09",
            "period_start_date": "2026-09-01",
            "period_end_date": "2026-09-30",
        }
        values.update(overrides)
        return values

    def _september_invoice_payload(self) -> dict[str, object]:
        proposal_id = str(uuid.uuid4())
        source_payload_hash = "sha256:" + "e" * 64
        return self._billing_validated_payload(
            proposal_id=proposal_id,
            source_payload_hash=source_payload_hash,
            idempotency_key=(
                f"{self.policy.tenant_reference}:invoice_draft:{proposal_id}"
                f":{source_payload_hash}:v1"
            ),
            accounting_date="2026-09-15",
            transaction_date="2026-09-15",
        )

    def _http_unapplied_cash_rollforward(
        self,
        *,
        legal_entity_reference: str | None = None,
        book_reference: str | None = None,
        fiscal_period_reference: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        fields = {
            "legal_entity_reference": (
                self.policy.legal_entity_reference
                if legal_entity_reference is None
                else legal_entity_reference
            ),
            "book_reference": (
                self.policy.accounting_book_reference
                if book_reference is None
                else book_reference
            ),
            "fiscal_period_reference": (
                "urn:cwl:accounting:fiscal_period:2026-08"
                if fiscal_period_reference is None
                else fiscal_period_reference
            ),
        }
        query = urllib.parse.urlencode(fields)
        return self._http_json(
            "GET",
            f"/unapplied-cash-rollforwards?{query}",
            None,
            tenant_header=tenant_header,
        )

    def _http_vat_period_register(
        self,
        *,
        legal_entity_reference: str | None = None,
        book_reference: str | None = None,
        fiscal_period_reference: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        fields = {
            "legal_entity_reference": (
                self.policy.legal_entity_reference
                if legal_entity_reference is None
                else legal_entity_reference
            ),
            "book_reference": (
                self.policy.accounting_book_reference
                if book_reference is None
                else book_reference
            ),
            "fiscal_period_reference": (
                "urn:cwl:accounting:fiscal_period:2026-08"
                if fiscal_period_reference is None
                else fiscal_period_reference
            ),
        }
        query = urllib.parse.urlencode(fields)
        return self._http_json(
            "GET",
            f"/vat-period-registers?{query}",
            None,
            tenant_header=tenant_header,
        )

    def _http_home_tax_submission(
        self,
        *,
        legal_entity_reference: str | None = None,
        book_reference: str | None = None,
        fiscal_period_reference: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        payload = {
            "tenant_reference": (
                self.policy.tenant_reference
                if tenant_header in ("", None)
                else tenant_header
            ),
            "legal_entity_reference": (
                self.policy.legal_entity_reference
                if legal_entity_reference is None
                else legal_entity_reference
            ),
            "book_reference": (
                self.policy.accounting_book_reference
                if book_reference is None
                else book_reference
            ),
            "fiscal_period_reference": (
                "urn:cwl:accounting:fiscal_period:2026-08"
                if fiscal_period_reference is None
                else fiscal_period_reference
            ),
        }
        return self._http_json(
            "POST",
            "/home-tax-submissions",
            payload,
            tenant_header=tenant_header,
        )

    def _http_home_tax_submissions(
        self,
        *,
        legal_entity_reference: str | None = None,
        book_reference: str | None = None,
        fiscal_period_reference: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        fields = {
            "legal_entity_reference": (
                self.policy.legal_entity_reference
                if legal_entity_reference is None
                else legal_entity_reference
            ),
            "book_reference": (
                self.policy.accounting_book_reference
                if book_reference is None
                else book_reference
            ),
            "fiscal_period_reference": (
                "urn:cwl:accounting:fiscal_period:2026-08"
                if fiscal_period_reference is None
                else fiscal_period_reference
            ),
        }
        query = urllib.parse.urlencode(fields)
        return self._http_json(
            "GET",
            f"/home-tax-submissions?{query}",
            None,
            tenant_header=tenant_header,
        )

    def _http_account_rollforward(
        self,
        chart_account_code: str,
        *,
        legal_entity_reference: str | None = None,
        book_reference: str | None = None,
        fiscal_period_reference: str | None = None,
        statement_scope_code: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        fields = {
            "legal_entity_reference": (
                self.policy.legal_entity_reference
                if legal_entity_reference is None
                else legal_entity_reference
            ),
            "book_reference": (
                self.policy.accounting_book_reference
                if book_reference is None
                else book_reference
            ),
            "fiscal_period_reference": (
                "urn:cwl:accounting:fiscal_period:2026-08"
                if fiscal_period_reference is None
                else fiscal_period_reference
            ),
            "chart_account_code": chart_account_code,
        }
        if statement_scope_code is not None:
            fields["statement_scope_code"] = statement_scope_code
        query = urllib.parse.urlencode(fields)
        return self._http_json(
            "GET",
            f"/account-rollforwards?{query}",
            None,
            tenant_header=tenant_header,
        )

    def _http_account_balances(
        self,
        *,
        legal_entity_reference: str | None = None,
        book_reference: str | None = None,
        fiscal_period_reference: str | None = None,
        chart_account_code: str | None = None,
        page_limit: object | None = None,
        cursor: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        fields = {
            "legal_entity_reference": (
                self.policy.legal_entity_reference
                if legal_entity_reference is None
                else legal_entity_reference
            ),
            "book_reference": (
                self.policy.accounting_book_reference
                if book_reference is None
                else book_reference
            ),
            "fiscal_period_reference": (
                "urn:cwl:accounting:fiscal_period:2026-08"
                if fiscal_period_reference is None
                else fiscal_period_reference
            ),
        }
        if chart_account_code is not None:
            fields["chart_account_code"] = chart_account_code
        if page_limit is not None:
            fields["page_limit"] = str(page_limit)
        if cursor is not None:
            fields["cursor"] = cursor
        query = urllib.parse.urlencode(fields)
        return self._http_json(
            "GET",
            f"/account-balances?{query}",
            None,
            tenant_header=tenant_header,
        )

    def _http_receivable_aging(
        self,
        *,
        legal_entity_reference: str | None = None,
        book_reference: str | None = None,
        fiscal_period_reference: str | None = None,
        chart_account_code: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        fields = {
            "legal_entity_reference": (
                self.policy.legal_entity_reference
                if legal_entity_reference is None
                else legal_entity_reference
            ),
            "book_reference": (
                self.policy.accounting_book_reference
                if book_reference is None
                else book_reference
            ),
            "fiscal_period_reference": (
                "urn:cwl:accounting:fiscal_period:2026-08"
                if fiscal_period_reference is None
                else fiscal_period_reference
            ),
        }
        if chart_account_code is not None:
            fields["chart_account_code"] = chart_account_code
        query = urllib.parse.urlencode(fields)
        return self._http_json(
            "GET",
            f"/receivable-agings?{query}",
            None,
            tenant_header=tenant_header,
        )

    def _http_payable_aging(
        self,
        *,
        legal_entity_reference: str | None = None,
        book_reference: str | None = None,
        fiscal_period_reference: str | None = None,
        chart_account_code: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        fields = {
            "legal_entity_reference": (
                self.policy.legal_entity_reference
                if legal_entity_reference is None
                else legal_entity_reference
            ),
            "book_reference": (
                self.policy.accounting_book_reference
                if book_reference is None
                else book_reference
            ),
            "fiscal_period_reference": (
                "urn:cwl:accounting:fiscal_period:2026-08"
                if fiscal_period_reference is None
                else fiscal_period_reference
            ),
        }
        if chart_account_code is not None:
            fields["chart_account_code"] = chart_account_code
        query = urllib.parse.urlencode(fields)
        return self._http_json(
            "GET",
            f"/payable-agings?{query}",
            None,
            tenant_header=tenant_header,
        )

    def _http_account_ledger(
        self,
        chart_account_code: str,
        *,
        legal_entity_reference: str | None = None,
        fiscal_period_reference: str | None = None,
        page_limit: object | None = None,
        cursor: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        query: dict[str, str] = {
            "legal_entity_reference": (
                self.policy.legal_entity_reference
                if legal_entity_reference is None
                else legal_entity_reference
            ),
            "chart_account_code": chart_account_code,
        }
        if fiscal_period_reference is not None:
            query["fiscal_period_reference"] = fiscal_period_reference
        if page_limit is not None:
            query["page_limit"] = str(page_limit)
        if cursor is not None:
            query["cursor"] = cursor
        return self._http_json(
            "GET",
            f"/account-ledgers?{urllib.parse.urlencode(query)}",
            None,
            tenant_header=tenant_header,
        )

    def _http_fiscal_periods(
        self,
        *,
        legal_entity_reference: str | None = None,
        page_limit: object | None = None,
        cursor: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        query: dict[str, str] = {
            "legal_entity_reference": (
                self.policy.legal_entity_reference
                if legal_entity_reference is None
                else legal_entity_reference
            )
        }
        if page_limit is not None:
            query["page_limit"] = str(page_limit)
        if cursor is not None:
            query["cursor"] = cursor
        return self._http_json(
            "GET",
            f"/fiscal-periods?{urllib.parse.urlencode(query)}",
            None,
            tenant_header=tenant_header,
        )

    def _http_fiscal_period(
        self,
        *,
        legal_entity_reference: str | None = None,
        fiscal_period_reference: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        query = urllib.parse.urlencode(
            {
                "legal_entity_reference": (
                    self.policy.legal_entity_reference
                    if legal_entity_reference is None
                    else legal_entity_reference
                ),
                "fiscal_period_reference": (
                    "urn:cwl:accounting:fiscal_period:2026-08"
                    if fiscal_period_reference is None
                    else fiscal_period_reference
                ),
            }
        )
        return self._http_json(
            "GET",
            f"/fiscal-periods?{query}",
            None,
            tenant_header=tenant_header,
        )

    def _http_audit_events(
        self,
        *,
        event_type_code: str | None = None,
        page_limit: object | None = None,
        cursor: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        query: dict[str, str] = {}
        if event_type_code is not None:
            query["event_type_code"] = event_type_code
        if page_limit is not None:
            query["page_limit"] = str(page_limit)
        if cursor is not None:
            query["cursor"] = cursor
        path = "/audit-events"
        if query:
            path = f"/audit-events?{urllib.parse.urlencode(query)}"
        return self._http_json("GET", path, None, tenant_header=tenant_header)

    def _http_outbox_events(
        self,
        event_type_code: str,
        *,
        page_limit: object | None = None,
        cursor: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        query: dict[str, str] = {"event_type_code": event_type_code}
        if page_limit is not None:
            query["page_limit"] = str(page_limit)
        if cursor is not None:
            query["cursor"] = cursor
        return self._http_json(
            "GET",
            f"/outbox-events?{urllib.parse.urlencode(query)}",
            None,
            tenant_header=tenant_header,
        )

    def _http_publish_outbox(
        self,
        outbox_event_id: str,
        *,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        return self._http_json(
            "POST",
            f"/outbox-events/{outbox_event_id}/publish",
            {},
            tenant_header=tenant_header,
        )

    def _http_period_closes(
        self,
        *,
        legal_entity_reference: str | None = None,
        fiscal_period_reference: str | None = None,
        period_status_code: str | None = None,
        page_limit: object | None = None,
        cursor: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        query: dict[str, str] = {
            "legal_entity_reference": (
                self.policy.legal_entity_reference
                if legal_entity_reference is None
                else legal_entity_reference
            )
        }
        if fiscal_period_reference is not None:
            query["fiscal_period_reference"] = fiscal_period_reference
        if period_status_code is not None:
            query["period_status_code"] = period_status_code
        if page_limit is not None:
            query["page_limit"] = str(page_limit)
        if cursor is not None:
            query["cursor"] = cursor
        return self._http_json(
            "GET",
            f"/period-closes?{urllib.parse.urlencode(query)}",
            None,
            tenant_header=tenant_header,
        )

    def _http_journal_reversals(
        self,
        *,
        legal_entity_reference: str | None = None,
        original_journal_reference: str | None = None,
        fiscal_period_reference: str | None = None,
        page_limit: object | None = None,
        cursor: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        query: dict[str, str] = {
            "legal_entity_reference": (
                self.policy.legal_entity_reference
                if legal_entity_reference is None
                else legal_entity_reference
            )
        }
        if original_journal_reference is not None:
            query["original_journal_reference"] = original_journal_reference
        if fiscal_period_reference is not None:
            query["fiscal_period_reference"] = fiscal_period_reference
        if page_limit is not None:
            query["page_limit"] = str(page_limit)
        if cursor is not None:
            query["cursor"] = cursor
        return self._http_json(
            "GET",
            f"/journal-reversals?{urllib.parse.urlencode(query)}",
            None,
            tenant_header=tenant_header,
        )

    def _http_period_journals(
        self,
        *,
        legal_entity_reference: str | None = None,
        book_reference: str | None = None,
        fiscal_period_reference: str | None = None,
        page_limit: object | None = None,
        cursor: str | None = None,
        journal_source_code: str | None = None,
        use_book_alias: bool = False,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        query: dict[str, str] = {
            "legal_entity_reference": (
                self.policy.legal_entity_reference
                if legal_entity_reference is None
                else legal_entity_reference
            ),
            "fiscal_period_reference": (
                "urn:cwl:accounting:fiscal_period:2026-08"
                if fiscal_period_reference is None
                else fiscal_period_reference
            ),
        }
        book_key = "accounting_book_reference" if use_book_alias else "book_reference"
        query[book_key] = (
            self.policy.accounting_book_reference
            if book_reference is None
            else book_reference
        )
        if page_limit is not None:
            query["page_limit"] = str(page_limit)
        if cursor is not None:
            query["cursor"] = cursor
        if journal_source_code is not None:
            query["journal_source_code"] = journal_source_code
        return self._http_json(
            "GET",
            f"/journals?{urllib.parse.urlencode(query)}",
            None,
            tenant_header=tenant_header,
        )

    def _http_journal(
        self,
        *,
        idempotency_key: str | None = None,
        journal_reference: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        query: dict[str, str] = {}
        if idempotency_key is not None:
            query["idempotency_key"] = idempotency_key
        if journal_reference is not None:
            query["journal_reference"] = journal_reference
        path = "/journals"
        if query:
            path = f"/journals?{urllib.parse.urlencode(query)}"
        return self._http_json("GET", path, None, tenant_header=tenant_header)

    def _http_legal_entities(
        self,
        *,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        return self._http_json("GET", "/legal-entities", None, tenant_header=tenant_header)

    def _http_accounting_books(
        self,
        *,
        legal_entity_reference: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        query = urllib.parse.urlencode(
            {
                "legal_entity_reference": (
                    self.policy.legal_entity_reference
                    if legal_entity_reference is None
                    else legal_entity_reference
                )
            }
        )
        return self._http_json(
            "GET",
            f"/accounting-books?{query}",
            None,
            tenant_header=tenant_header,
        )

    def _http_chart_accounts(
        self,
        *,
        legal_entity_reference: str | None = None,
        book_reference: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        query = urllib.parse.urlencode(
            {
                "legal_entity_reference": (
                    self.policy.legal_entity_reference
                    if legal_entity_reference is None
                    else legal_entity_reference
                ),
                "book_reference": (
                    self.policy.accounting_book_reference
                    if book_reference is None
                    else book_reference
                ),
            }
        )
        return self._http_json(
            "GET",
            f"/chart-accounts?{query}",
            None,
            tenant_header=tenant_header,
        )

    def _http_account_role_mappings(
        self,
        *,
        legal_entity_reference: str | None = None,
        book_reference: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        query = urllib.parse.urlencode(
            {
                "legal_entity_reference": (
                    self.policy.legal_entity_reference
                    if legal_entity_reference is None
                    else legal_entity_reference
                ),
                "book_reference": (
                    self.policy.accounting_book_reference
                    if book_reference is None
                    else book_reference
                ),
            }
        )
        return self._http_json(
            "GET",
            f"/account-role-mappings?{query}",
            None,
            tenant_header=tenant_header,
        )

    def _http_financial_statement(
        self,
        statement_type_code: str,
        *,
        legal_entity_reference: str | None = None,
        book_reference: str | None = None,
        fiscal_period_reference: str | None = None,
        comparison_fiscal_period_reference: str | None = None,
        statement_scope_code: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        fields = {
            "legal_entity_reference": (
                self.policy.legal_entity_reference
                if legal_entity_reference is None
                else legal_entity_reference
            ),
            "book_reference": (
                self.policy.accounting_book_reference
                if book_reference is None
                else book_reference
            ),
            "fiscal_period_reference": (
                "urn:cwl:accounting:fiscal_period:2026-08"
                if fiscal_period_reference is None
                else fiscal_period_reference
            ),
            "statement_type_code": statement_type_code,
        }
        if comparison_fiscal_period_reference is not None:
            fields["comparison_fiscal_period_reference"] = (
                comparison_fiscal_period_reference
            )
        if statement_scope_code is not None:
            fields["statement_scope_code"] = statement_scope_code
        query = urllib.parse.urlencode(fields)
        return self._http_json(
            "GET",
            f"/financial-statements?{query}",
            None,
            tenant_header=tenant_header,
        )

    def _http_financial_statement_package(
        self,
        *,
        legal_entity_reference: str | None = None,
        book_reference: str | None = None,
        fiscal_period_reference: str | None = None,
        comparison_fiscal_period_reference: str | None = None,
        statement_scope_code: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        fields = {
            "legal_entity_reference": (
                self.policy.legal_entity_reference
                if legal_entity_reference is None
                else legal_entity_reference
            ),
            "book_reference": (
                self.policy.accounting_book_reference
                if book_reference is None
                else book_reference
            ),
            "fiscal_period_reference": (
                "urn:cwl:accounting:fiscal_period:2026-08"
                if fiscal_period_reference is None
                else fiscal_period_reference
            ),
        }
        if comparison_fiscal_period_reference is not None:
            fields["comparison_fiscal_period_reference"] = (
                comparison_fiscal_period_reference
            )
        if statement_scope_code is not None:
            fields["statement_scope_code"] = statement_scope_code
        query = urllib.parse.urlencode(fields)
        return self._http_json(
            "GET",
            f"/financial-statement-packages?{query}",
            None,
            tenant_header=tenant_header,
        )

    def _http_period_close_package(
        self,
        *,
        legal_entity_reference: str | None = None,
        book_reference: str | None = None,
        fiscal_period_reference: str | None = None,
        comparison_fiscal_period_reference: str | None = None,
        statement_scope_code: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        fields = {
            "legal_entity_reference": (
                self.policy.legal_entity_reference
                if legal_entity_reference is None
                else legal_entity_reference
            ),
            "book_reference": (
                self.policy.accounting_book_reference
                if book_reference is None
                else book_reference
            ),
            "fiscal_period_reference": (
                "urn:cwl:accounting:fiscal_period:2026-08"
                if fiscal_period_reference is None
                else fiscal_period_reference
            ),
        }
        if comparison_fiscal_period_reference is not None:
            fields["comparison_fiscal_period_reference"] = (
                comparison_fiscal_period_reference
            )
        if statement_scope_code is not None:
            fields["statement_scope_code"] = statement_scope_code
        query = urllib.parse.urlencode(fields)
        return self._http_json(
            "GET",
            f"/period-close-packages?{query}",
            None,
            tenant_header=tenant_header,
        )

    def _assert_financial_statement_package_tie_outs(
        self, package: dict[str, object]
    ) -> None:
        income = package["income_statement"]
        sheet = package["balance_sheet"]
        equity = package["changes_in_equity"]
        cash_flow = package["cash_flow"]
        assert isinstance(income, dict)
        assert isinstance(sheet, dict)
        assert isinstance(equity, dict)
        assert isinstance(cash_flow, dict)
        equity_roles = {
            str(item["account_role_code"]): item for item in equity["statement_lines"]
        }
        period_net_income = Decimal(
            str(equity_roles["period_net_income"]["credit_amount"])
        ) - Decimal(str(equity_roles["period_net_income"]["debit_amount"]))
        self.assertEqual(period_net_income, Decimal(str(income["net_income_amount"])))
        closing_equity = Decimal(
            str(equity_roles["closing_equity"]["credit_amount"])
        ) - Decimal(str(equity_roles["closing_equity"]["debit_amount"]))
        sheet_equity = sum(
            Decimal(str(item["credit_amount"])) - Decimal(str(item["debit_amount"]))
            for item in sheet["statement_lines"]
            if item["account_class_code"] == "equity"
        )
        self.assertEqual(
            closing_equity,
            sheet_equity + Decimal(str(sheet["net_income_amount"])),
        )
        cash_roles = {
            str(item["account_role_code"]): item for item in cash_flow["statement_lines"]
        }
        closing_cash = Decimal(str(cash_roles["closing_cash"]["credit_amount"])) - Decimal(
            str(cash_roles["closing_cash"]["debit_amount"])
        )
        sheet_cash = sum(
            Decimal(str(item["debit_amount"])) - Decimal(str(item["credit_amount"]))
            for item in sheet["statement_lines"]
            if item["account_role_code"] == "cash_receipt"
        )
        self.assertEqual(closing_cash, sheet_cash)

    def _http_trial_balance(
        self,
        *,
        legal_entity_reference: str | None = None,
        book_reference: str | None = None,
        fiscal_period_reference: str | None = None,
        balance_basis_code: str | None = None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        fields = {
            "legal_entity_reference": (
                self.policy.legal_entity_reference
                if legal_entity_reference is None
                else legal_entity_reference
            ),
            "book_reference": (
                self.policy.accounting_book_reference
                if book_reference is None
                else book_reference
            ),
            "fiscal_period_reference": (
                "urn:cwl:accounting:fiscal_period:2026-08"
                if fiscal_period_reference is None
                else fiscal_period_reference
            ),
        }
        if balance_basis_code is not None:
            fields["balance_basis_code"] = balance_basis_code
        query = urllib.parse.urlencode(fields)
        return self._http_json(
            "GET",
            f"/trial-balances?{query}",
            None,
            tenant_header=tenant_header,
        )

    def _assert_period_close_package_worksheets_agree(
        self, package: dict[str, object]
    ) -> None:
        trial_balance = package["trial_balance"]
        income = package["financial_statement_package"]["income_statement"]
        codes = {
            str(line["chart_account_code"])
            for line in trial_balance["lines"]
            if isinstance(line, dict)
        }
        receivable = Decimal(str(package["receivable_aging"]["total_outstanding_amount"]))
        payable = Decimal(str(package["payable_aging"]["total_outstanding_amount"]))
        leftover = Decimal(str(package["unapplied_cash_rollforward"]["closing_amount"]))
        self.assertEqual(
            set(package),
            {
                "tenant_reference",
                "legal_entity_reference",
                "accounting_book_reference",
                "book_reference",
                "fiscal_period_reference",
                "fiscal_period",
                "trial_balance",
                "financial_statement_package",
                "receivable_aging",
                "payable_aging",
                "unapplied_cash_rollforward",
                "period_close",
            },
        )
        self.assertEqual(package["payable_aging"]["chart_account_code"], "210100")
        self.assertEqual(
            receivable,
            self._trial_balance_account_net(trial_balance, "110100"),
        )
        if "210100" in codes:
            self.assertEqual(
                payable,
                -self._trial_balance_account_net(trial_balance, "210100"),
            )
        else:
            self.assertEqual(payable, Decimal("0"))
        if "210200" in codes:
            self.assertEqual(
                leftover,
                -self._trial_balance_account_net(trial_balance, "210200"),
            )
        else:
            self.assertEqual(leftover, Decimal("0"))
        self.assertEqual(
            Decimal(str(income["net_income_amount"])),
            -self._trial_balance_account_net(trial_balance, "410100"),
        )

    def _trial_balance_line(
        self, document: dict[str, object], chart_account_code: str
    ) -> dict[str, object]:
        lines = document["lines"]
        assert isinstance(lines, list)
        for line in lines:
            assert isinstance(line, dict)
            if line.get("chart_account_code") == chart_account_code:
                return line
        self.fail(f"trial-balance line {chart_account_code} is missing")

    def _trial_balance_account_net(
        self, document: dict[str, object], chart_account_code: str
    ) -> Decimal:
        line = self._trial_balance_line(document, chart_account_code)
        return Decimal(str(line["debit_amount"])) - Decimal(str(line["credit_amount"]))

    def _account_balance_net(
        self, document: dict[str, object], chart_account_code: str
    ) -> Decimal:
        balances = document["account_balances"]
        assert isinstance(balances, list)
        for item in balances:
            assert isinstance(item, dict)
            if item.get("chart_account_code") == chart_account_code:
                return Decimal(str(item["debit_amount"])) - Decimal(
                    str(item["credit_amount"])
                )
        self.fail(f"account-balance {chart_account_code} is missing")

    def _http_lookup(
        self,
        idempotency_key: str,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        query = urllib.parse.urlencode({"idempotency_key": idempotency_key})
        return self._http_json(
            "GET",
            f"/posting-receipts?{query}",
            None,
            tenant_header=tenant_header,
        )

    def _http_json(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None,
        tenant_header: str | None = "",
    ) -> tuple[int, dict[str, object]]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if tenant_header is None:
            pass
        elif tenant_header == "":
            headers["X-CWL-Tenant-Reference"] = self.policy.tenant_reference
        else:
            headers["X-CWL-Tenant-Reference"] = tenant_header
        request = urllib.request.Request(
            f"http://127.0.0.1:{self._http_port()}{path}",
            data=body,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def _http_raw(self, method: str, path: str, body: bytes, tenant_reference: str) -> tuple[int, dict[str, object]]:
        connection = http.client.HTTPConnection("127.0.0.1", self._http_port())
        try:
            connection.request(
                method,
                path,
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "X-CWL-Tenant-Reference": tenant_reference,
                },
            )
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

    def _http_invalid_length(self) -> tuple[int, dict[str, object]]:
        connection = http.client.HTTPConnection("127.0.0.1", self._http_port())
        try:
            connection.putrequest("POST", "/journal-proposals")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("X-CWL-Tenant-Reference", self.policy.tenant_reference)
            connection.putheader("Content-Length", "abc")
            connection.endheaders()
            connection.send(b"{}")
            response = connection.getresponse()
            return response.status, json.loads(response.read().decode("utf-8"))
        finally:
            connection.close()

    def _http_port(self) -> int:
        return self._http_server.server_address[1]


if __name__ == "__main__":
    unittest.main()
