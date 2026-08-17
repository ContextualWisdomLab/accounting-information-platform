"""Realistic PostgreSQL posting tests against the foundation migration."""

from __future__ import annotations

import http.client
import json
import os
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
    accept_period_close,
    accept_pulled_proposals,
    create_journal_proposal_server,
    ingest_journal_proposal,
    lookup_published_receipt,
    lookup_trial_balance,
    pull_journal_proposal,
    pull_validated_journal_proposals,
    run_journal_proposal_server,
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
        self.assertEqual(self._count_table("accounting_core.general_journal"), 1)
        self.assertEqual(self._count_table("accounting_integration.journal_proposal_record"), 1)
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
        self.assertEqual(close_receipt["source_journal_count"], 1)
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
                    "account_role_code": "tax_payable",
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
            for account_code, account_name, normal_balance_code, account_role_code in (
                ("110100", "Accounts receivable", "debit", "accounts_receivable"),
                ("410100", "Usage revenue", "credit", "usage_revenue"),
                ("110200", "Cash receipts", "debit", "cash_receipt"),
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

    def _start_http_server(self):
        server = create_journal_proposal_server(
            DATABASE_URL, self.policy.tenant_reference, "127.0.0.1", 0
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
