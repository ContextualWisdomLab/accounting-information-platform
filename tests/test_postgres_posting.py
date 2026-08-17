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
    accept_billing_proposal_pull,
    accept_journal_proposal,
    accept_journal_reversal,
    accept_period_close,
    accept_period_open,
    accept_pulled_proposals,
    create_journal_proposal_server,
    ingest_journal_proposal,
    lookup_account_ledger,
    lookup_account_role_mappings,
    lookup_accounting_books,
    lookup_chart_accounts,
    lookup_legal_entities,
    lookup_financial_statement,
    lookup_fiscal_period,
    lookup_fiscal_periods,
    lookup_audit_events,
    lookup_outbox_events,
    lookup_journal_reversals,
    lookup_period_journals,
    lookup_posted_journal,
    publish_outbox_event,
    lookup_published_receipt,
    lookup_trial_balance,
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
        self._seed_expense_account()
        self.ledger.post(self._two_line_proposal(), self.policy)
        self.ledger.post(
            self._two_line_proposal(
                proposal_id=str(uuid.uuid4()),
                idempotency_key="usage-cost-v1",
                source_payload_hash="sha256:" + "7" * 64,
                lines=(
                    JournalLineProposal(1, "usage_cost", "25000", "0"),
                    JournalLineProposal(2, "accounts_receivable", "0", "25000"),
                ),
            ),
            self._policy_with(
                chart_account_mapping={
                    **self.policy.chart_account_mapping,
                    "usage_cost": "510100",
                }
            ),
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
        self.assertEqual(replayed, (post_receipt,))
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
            },
        )
        self.assertEqual(by_code["accounts_receivable"]["chart_account_code"], "110100")
        self.assertEqual(by_code["usage_revenue"]["chart_account_code"], "410100")
        self.assertEqual(by_code["cash_receipt"]["chart_account_code"], "110200")
        self.assertEqual(by_code["tax_payable"]["chart_account_code"], "210100")
        self.assertEqual(by_code["retained_earnings"]["chart_account_code"], "310100")
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
        self.assertEqual(set(by_code), {"110100", "410100", "110200", "210100", "310100"})
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
        bad_type = self._http_financial_statement("cash_flow")
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
                "cash_flow",
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
                "cash_flow",
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

        self.assertEqual(post_status, 405)
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
        self.assertEqual(len(receipts), 2)
        self.assertEqual(receipts[0], invoice_lookup)
        self.assertEqual(receipts[1], cash_lookup)
        self.assertEqual(replayed, receipts)
        self.assertEqual(lookup_status, 200)
        self.assertEqual(http_lookup, invoice_lookup)
        self.assertEqual(pulled_invoice["proposal_id"], invoice["proposal_id"])
        self.assertEqual(http_status, 200)
        self.assertEqual(http_body["posting_receipts"], list(replayed))
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
        self.assertEqual(from_env["posting_receipts"], list(replayed))
        self.assertEqual(empty_page["posting_receipts"], [])
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

    def _seed_expense_account(self) -> None:
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
                VALUES (%s, %s, '510100', 'Usage cost', 'debit', 'expense', %s)
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
                VALUES (%s, %s, 'usage_cost', %s, 'ifrs-v1', 'billing-issued-v1', %s)
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

    def _http_trial_balance(
        self,
        *,
        legal_entity_reference: str | None = None,
        book_reference: str | None = None,
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
        )
        return self._http_json(
            "GET",
            f"/trial-balances?{query}",
            None,
            tenant_header=tenant_header,
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
