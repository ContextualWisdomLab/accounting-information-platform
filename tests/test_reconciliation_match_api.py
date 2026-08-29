"""RED/GREEN contracts for the proposed reconciliation-match command API."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import unittest
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import mock

import psycopg

from accounting_information_platform import (
    CAMT053_MESSAGE_DEFINITION,
    AccountingValidationError,
    IdempotencyConflictError,
    JournalLineProposal,
    MemoryArtifactStore,
    accept_bank_account_assignment,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    accept_reconciliation_match,
    accept_reconciliation_run,
    load_canonical_statement_fixture,
    lookup_bank_statement_entries,
    lookup_reconciliation_match,
)
from accounting_information_platform.reconciliation_match import (
    _require_recorded_source_amounts,
)
from tests import test_postgres_posting as posting


class ReconciliationMatchApiTests(unittest.TestCase):
    """Prove one exact 1:1 match is durable evidence, not approval or posting."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        self.store = MemoryArtifactStore()
        self.account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.account_reference,
                "account_currency_code": "KRW",
                "account_identifier": "acct-opaque-fixture-only",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        accept_bank_account_assignment(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.account_reference,
                "legal_entity_reference": self.case.policy.legal_entity_reference,
                "accounting_book_reference": self.case.policy.accounting_book_reference,
                "chart_account_code": "110200",
                "valid_from": "2026-01-01T00:00:00Z",
                "assignment_idempotency_key": f"assign-match-{uuid.uuid4().hex}",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )

    def test_public_package_import_does_not_require_psycopg(self) -> None:
        """Dependency-free public imports do not load the optional database driver."""
        package_root = Path(__file__).resolve().parents[1]
        script = """
import builtins

real_import = builtins.__import__

def blocked_import(name, *args, **kwargs):
    if name == "psycopg" or name.startswith("psycopg."):
        raise ImportError("blocked for dependency-free import contract")
    return real_import(name, *args, **kwargs)

builtins.__import__ = blocked_import
import accounting_information_platform
"""
        environment = dict(os.environ, PYTHONPATH=str(package_root / "src"))
        completed = subprocess.run(
            [sys.executable, "-c", script],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def _open_run(
        self, entry_index: int = 0, late_statement_entry: bool = False
    ) -> tuple[str, str]:
        fixture = load_canonical_statement_fixture()
        fixture = fixture.replace(
            b"BANK-STMT-2026-08-24", f"BANK-STMT-{uuid.uuid4().hex[:12]}".encode(), 1
        )
        fixture = fixture.replace(
            b"Invoice 1001", f"Invoice {uuid.uuid4().hex[:8]}".encode(), 1
        )
        if late_statement_entry:
            fixture = fixture.replace(
                b"2026-08-24T01:15:00+00:00",
                b"2026-08-25T01:15:00+00:00",
                1,
            )
        statement = accept_bank_statement_evidence(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.account_reference,
                "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                "statement_payload": fixture.decode("utf-8"),
                "ingestion_idempotency_key": f"statement-match-{uuid.uuid4().hex}",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        entries = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(statement["bank_statement_record_id"]),
        )["bank_statement_entries"]
        entry = entries[entry_index]
        statement_hash = "sha256:" + hashlib.sha256(fixture).hexdigest()
        run = accept_reconciliation_run(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_statement_record_id": statement["bank_statement_record_id"],
                "legal_entity_reference": self.case.policy.legal_entity_reference,
                "accounting_book_reference": self.case.policy.accounting_book_reference,
                "bank_cutoff_at": "2026-08-24T23:59:59Z",
                "book_cutoff_at": "2026-08-24T23:59:59Z",
                "matching_policy_version": "deterministic-v1",
                "knowledge_cutoff_at": "2026-09-01T00:00:00Z",
                "reconciliation_idempotency_key": f"run-match-{uuid.uuid4().hex}",
                "source_payload_hash": statement_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        return str(run["reconciliation_run_id"]), str(
            entry["source_entry_identity"] or entry["bank_statement_entry_id"]
        )

    def _command(
        self,
        *,
        entry_index: int = 0,
        amount: str = "25000",
        cash_direction: str = "debit",
        accounting_date: date = date(2026, 8, 24),
        extra_debit: str = "0",
        late_statement_entry: bool = False,
    ) -> dict[str, object]:
        run_id, statement_reference = self._open_run(entry_index, late_statement_entry)
        cash_debit, cash_credit = (
            (amount, "0") if cash_direction == "debit" else ("0", amount)
        )
        if cash_direction == "debit" and Decimal(extra_debit) > 0:
            lines = (
                JournalLineProposal(1, "cash_receipt", cash_debit, cash_credit),
                JournalLineProposal(2, "accounts_receivable", extra_debit, "0"),
                JournalLineProposal(
                    3,
                    "usage_revenue",
                    "0",
                    str(Decimal(amount) + Decimal(extra_debit)),
                ),
            )
        elif cash_direction == "debit":
            lines = (
                JournalLineProposal(1, "cash_receipt", cash_debit, cash_credit),
                JournalLineProposal(2, "usage_revenue", "0", amount),
            )
        else:
            lines = (
                JournalLineProposal(1, "cash_receipt", cash_debit, cash_credit),
                JournalLineProposal(2, "accounts_receivable", amount, "0"),
            )
        journal = self.case.ledger.post(
            self.case._two_line_proposal(
                proposal_id=str(uuid.uuid4()),
                idempotency_key=f"match-journal-{uuid.uuid4().hex}",
                source_payload_hash="sha256:" + "9" * 64,
                source_event_references=(f"urn:cwl:reconciliation:journal:{uuid.uuid4()}",),
                transaction_date=accounting_date,
                accounting_date=accounting_date,
                lines=lines,
            ),
            self.case.policy,
        )
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "reconciliation_run_id": run_id,
            "statement_entry_reference": statement_reference,
            "journal_reference": journal.journal_reference,
            "statement_amount": f"{Decimal(amount):.2f}",
            "journal_amount": f"{Decimal(amount):.2f}",
            "rule_code": "provider_reference",
            "candidate_idempotency_key": f"candidate-{uuid.uuid4().hex}",
            "source_payload_hash": "sha256:" + "1" * 64,
            "source_payload_reference": "urn:cwl:object:match-evidence",
        }

    def test_proposed_match_is_persisted_and_replayed(self) -> None:
        """An exact retry returns the same proposed match without approval authority."""
        command = self._command()
        first = accept_reconciliation_match(
            command, posting.DATABASE_URL, self.case.policy.tenant_reference
        )
        replay = accept_reconciliation_match(
            command, posting.DATABASE_URL, self.case.policy.tenant_reference
        )
        loaded = lookup_reconciliation_match(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(first["reconciliation_match_id"]),
        )
        self.assertEqual(first["match_status_code"], "proposed")
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["reconciliation_match_id"], replay["reconciliation_match_id"])
        self.assertEqual(loaded["reconciliation_candidate_id"], first["reconciliation_candidate_id"])
        self.assertEqual(loaded["allocated_amount"], "25000")

    def test_same_key_changed_source_fails_closed(self) -> None:
        """A candidate key cannot be reused for changed immutable evidence."""
        command = self._command()
        accept_reconciliation_match(
            command, posting.DATABASE_URL, self.case.policy.tenant_reference
        )
        changed = dict(command, source_payload_hash="sha256:" + "2" * 64)
        with self.assertRaises(IdempotencyConflictError):
            accept_reconciliation_match(
                changed, posting.DATABASE_URL, self.case.policy.tenant_reference
            )

    def test_match_command_evidence_is_immutable(self) -> None:
        """The database trigger prevents mutation of recorded match command evidence."""
        command = self._command()
        document = accept_reconciliation_match(
            command, posting.DATABASE_URL, self.case.policy.tenant_reference
        )
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaises(psycopg.Error):
                connection.execute(
                    """
                    UPDATE accounting_core.reconciliation_match_command
                    SET source_payload_reference = 'urn:cwl:object:tampered'
                    WHERE reconciliation_match_id = %s
                    """,
                    (document["reconciliation_match_id"],),
                )

    def test_match_command_rejects_non_exact_or_unbalanced_amounts(self) -> None:
        """The command rejects JSON numbers and non-conserving 1:1 evidence."""
        command = self._command()
        for changed in (
            dict(command, statement_amount=25000.0),
            dict(command, journal_amount="24999.99"),
        ):
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(AccountingValidationError, "amount|equal"):
                    accept_reconciliation_match(
                        changed, posting.DATABASE_URL, self.case.policy.tenant_reference
                    )

    def test_match_command_requires_recorded_source_amounts(self) -> None:
        """A proposed match cannot invent amounts or point at an absent journal."""
        command = self._command()
        tenant = self.case.policy.tenant_reference
        with self.assertRaisesRegex(AccountingValidationError, "does not match recorded"):
            accept_reconciliation_match(
                dict(
                    command,
                    statement_amount="24999.99",
                    journal_amount="24999.99",
                    candidate_idempotency_key=f"source-amount-{uuid.uuid4().hex}",
                ),
                posting.DATABASE_URL,
                tenant,
            )
        with self.assertRaisesRegex(AccountingValidationError, "journal source"):
            accept_reconciliation_match(
                dict(
                    command,
                    journal_reference="urn:cwl:accounting:general_journal:missing",
                    candidate_idempotency_key=f"missing-journal-{uuid.uuid4().hex}",
                ),
                posting.DATABASE_URL,
                tenant,
            )
        with self.assertRaisesRegex(AccountingValidationError, "not recorded exactly once"):
            accept_reconciliation_match(
                dict(
                    command,
                    statement_entry_reference="statement-entry-missing",
                    candidate_idempotency_key=f"missing-statement-{uuid.uuid4().hex}",
                ),
                posting.DATABASE_URL,
                tenant,
            )

    def test_match_uses_assigned_cash_line_for_compound_journal(self) -> None:
        """A compound journal matches only the assigned cash line, not its total."""
        command = self._command(extra_debit="100")
        document = accept_reconciliation_match(
            command, posting.DATABASE_URL, self.case.policy.tenant_reference
        )
        self.assertEqual(document["allocated_amount"], "25000")

    def test_match_enforces_statement_and_cash_journal_direction(self) -> None:
        """CRDT uses cash debit and DBIT uses cash credit for source matching."""
        with self.assertRaisesRegex(AccountingValidationError, "direction"):
            accept_reconciliation_match(
                self._command(cash_direction="credit"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
            )
        valid = accept_reconciliation_match(
            self._command(entry_index=1, amount="10000", cash_direction="credit"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.assertEqual(valid["match_status_code"], "proposed")

    def test_match_rejects_journal_after_book_cutoff(self) -> None:
        """A journal recorded after the run book cutoff is not matchable evidence."""
        with self.assertRaisesRegex(AccountingValidationError, "journal source"):
            accept_reconciliation_match(
                self._command(accounting_date=date(2026, 8, 25)),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
            )

    def test_match_rejects_statement_entry_after_bank_cutoff(self) -> None:
        """A statement entry outside the run bank cutoff is not matchable evidence."""
        with self.assertRaisesRegex(AccountingValidationError, "not recorded exactly once"):
            accept_reconciliation_match(
                self._command(late_statement_entry=True),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
            )

    def test_match_command_requires_complete_allocation_evidence(self) -> None:
        """Direct command evidence cannot omit its one-to-one allocation rows."""
        command = self._command()
        tenant_id = self.case.tenant_id
        with psycopg.connect(posting.DATABASE_URL) as connection:
            candidate_id = connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_candidate (
                    tenant_account_id, reconciliation_run_id,
                    statement_entry_reference, journal_reference,
                    statement_amount, journal_amount, rule_code
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'direct-command')
                RETURNING reconciliation_candidate_id
                """,
                (
                    tenant_id,
                    command["reconciliation_run_id"],
                    command["statement_entry_reference"],
                    command["journal_reference"],
                    command["statement_amount"],
                    command["journal_amount"],
                ),
            ).fetchone()[0]
            match_id = connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_match (
                    tenant_account_id, reconciliation_run_id,
                    reconciliation_candidate_id, match_status_code
                )
                VALUES (%s, %s, %s, 'proposed')
                RETURNING reconciliation_match_id
                """,
                (tenant_id, command["reconciliation_run_id"], candidate_id),
            ).fetchone()[0]
            with self.assertRaisesRegex(psycopg.errors.CheckViolation, "one statement"):
                connection.execute(
                    """
                    INSERT INTO accounting_core.reconciliation_match_command (
                        tenant_account_id, reconciliation_run_id,
                        reconciliation_candidate_id, reconciliation_match_id,
                        candidate_idempotency_key, candidate_command_hash,
                        source_payload_hash, source_payload_reference
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        tenant_id,
                        command["reconciliation_run_id"],
                        candidate_id,
                        match_id,
                        f"direct-command-{uuid.uuid4().hex}",
                        "sha256:" + "4" * 64,
                        command["source_payload_hash"],
                        command["source_payload_reference"],
                    ),
                )

    def test_match_command_rejects_late_allocation_after_command_evidence(self) -> None:
        """Command evidence freezes its allocation population."""
        command = self._command()
        document = accept_reconciliation_match(
            command, posting.DATABASE_URL, self.case.policy.tenant_reference
        )
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaisesRegex(psycopg.errors.CheckViolation, "command evidence"):
                connection.execute(
                    """
                    INSERT INTO accounting_core.statement_match_allocation (
                        tenant_account_id, reconciliation_run_id,
                        reconciliation_match_id, statement_entry_reference,
                        allocated_amount
                    )
                    VALUES (%s, %s, %s, %s, '1')
                    """,
                    (
                        self.case.tenant_id,
                        command["reconciliation_run_id"],
                        document["reconciliation_match_id"],
                        command["statement_entry_reference"],
                    ),
                )

    def test_match_command_rejects_allocation_amount_different_from_candidate(self) -> None:
        """Command evidence cannot preserve allocations that disagree with its candidate."""
        command = self._command()
        tenant_id = self.case.tenant_id
        with psycopg.connect(posting.DATABASE_URL) as connection:
            candidate_id = connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_candidate (
                    tenant_account_id, reconciliation_run_id,
                    statement_entry_reference, journal_reference,
                    statement_amount, journal_amount, rule_code
                )
                VALUES (%s, %s, %s, %s, '24999', '24999', 'wrong-amount')
                RETURNING reconciliation_candidate_id
                """,
                (
                    tenant_id,
                    command["reconciliation_run_id"],
                    command["statement_entry_reference"],
                    command["journal_reference"],
                ),
            ).fetchone()[0]
            match_id = connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_match (
                    tenant_account_id, reconciliation_run_id,
                    reconciliation_candidate_id, match_status_code
                )
                VALUES (%s, %s, %s, 'proposed')
                RETURNING reconciliation_match_id
                """,
                (tenant_id, command["reconciliation_run_id"], candidate_id),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO accounting_core.statement_match_allocation (
                    tenant_account_id, reconciliation_run_id,
                    reconciliation_match_id, statement_entry_reference,
                    allocated_amount
                )
                VALUES (%s, %s, %s, %s, '25000')
                """,
                (
                    tenant_id,
                    command["reconciliation_run_id"],
                    match_id,
                    command["statement_entry_reference"],
                ),
            )
            connection.execute(
                """
                INSERT INTO accounting_core.journal_match_allocation (
                    tenant_account_id, reconciliation_run_id,
                    reconciliation_match_id, journal_reference,
                    allocated_amount
                )
                VALUES (%s, %s, %s, %s, '25000')
                """,
                (
                    tenant_id,
                    command["reconciliation_run_id"],
                    match_id,
                    command["journal_reference"],
                ),
            )
            with self.assertRaisesRegex(psycopg.errors.CheckViolation, "candidate"):
                connection.execute(
                    """
                    INSERT INTO accounting_core.reconciliation_match_command (
                        tenant_account_id, reconciliation_run_id,
                        reconciliation_candidate_id, reconciliation_match_id,
                        candidate_idempotency_key, candidate_command_hash,
                        source_payload_hash, source_payload_reference
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        tenant_id,
                        command["reconciliation_run_id"],
                        candidate_id,
                        match_id,
                        f"wrong-amount-{uuid.uuid4().hex}",
                        "sha256:" + "5" * 64,
                        command["source_payload_hash"],
                        command["source_payload_reference"],
                    ),
                )

    def test_match_source_guard_rejects_unbalanced_or_wrong_journal_amounts(self) -> None:
        """Defensive source checks reject impossible or mismatched journal evidence."""
        for journal_row, message in (
            (("posted", "KRW", Decimal("25000"), Decimal("24999"), Decimal("25000"), Decimal("0")), "balanced and positive"),
            (("posted", "KRW", Decimal("24999"), Decimal("24999"), Decimal("24999"), Decimal("0")), "does not match recorded"),
            (("draft", "KRW", Decimal("25000"), Decimal("25000"), Decimal("25000"), Decimal("0")), "not a posted journal"),
            (("posted", "USD", Decimal("25000"), Decimal("25000"), Decimal("25000"), Decimal("0")), "not a posted journal"),
        ):
            with self.subTest(message=message):
                connection = mock.Mock()
                statement_result = mock.Mock()
                statement_result.fetchall.return_value = [(Decimal("25000"), "KRW", "CRDT")]
                journal_result = mock.Mock()
                journal_result.fetchone.return_value = journal_row
                connection.execute.side_effect = [statement_result, journal_result]
                with self.assertRaisesRegex(AccountingValidationError, message):
                    _require_recorded_source_amounts(
                        connection,
                        tenant_id=uuid.uuid4(),
                        reconciliation_run_id=uuid.uuid4(),
                        accounting_book_id=uuid.uuid4(),
                        currency_code="KRW",
                        bank_account_assignment_id=uuid.uuid4(),
                        bank_cutoff_at=datetime(2026, 8, 24, 23, 59, 59, tzinfo=timezone.utc),
                        book_cutoff_at=datetime(2026, 8, 24, 23, 59, 59, tzinfo=timezone.utc),
                        knowledge_cutoff_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                        statement_reference="statement-entry",
                        journal_reference="journal-reference",
                        statement_amount=Decimal("25000"),
                        journal_amount=Decimal("25000"),
                    )

    def test_match_source_guard_rejects_unsupported_direction(self) -> None:
        """The source guard remains defensive if a malformed row bypasses its DB check."""
        connection = mock.Mock()
        statement_result = mock.Mock()
        statement_result.fetchall.return_value = [(Decimal("25000"), "KRW", "OTHER")]
        connection.execute.return_value = statement_result
        with self.assertRaisesRegex(AccountingValidationError, "unsupported direction"):
            _require_recorded_source_amounts(
                connection,
                tenant_id=uuid.uuid4(),
                reconciliation_run_id=uuid.uuid4(),
                accounting_book_id=uuid.uuid4(),
                currency_code="KRW",
                bank_account_assignment_id=uuid.uuid4(),
                bank_cutoff_at=datetime(2026, 8, 24, 23, 59, 59, tzinfo=timezone.utc),
                book_cutoff_at=datetime(2026, 8, 24, 23, 59, 59, tzinfo=timezone.utc),
                knowledge_cutoff_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                statement_reference="statement-entry",
                journal_reference="journal-reference",
                statement_amount=Decimal("25000"),
                journal_amount=Decimal("25000"),
            )

    def test_match_source_guard_applies_journal_knowledge_cutoff(self) -> None:
        """Historical runs cannot admit journals posted after their knowledge cutoff."""
        connection = mock.Mock()
        statement_result = mock.Mock()
        statement_result.fetchall.return_value = [(Decimal("25000"), "KRW", "CRDT")]
        journal_result = mock.Mock()
        journal_result.fetchone.return_value = None
        connection.execute.side_effect = [statement_result, journal_result]
        knowledge_cutoff = datetime(2026, 8, 25, tzinfo=timezone.utc)
        with self.assertRaisesRegex(AccountingValidationError, "journal source"):
            _require_recorded_source_amounts(
                connection,
                tenant_id=uuid.uuid4(),
                reconciliation_run_id=uuid.uuid4(),
                accounting_book_id=uuid.uuid4(),
                currency_code="KRW",
                bank_account_assignment_id=uuid.uuid4(),
                bank_cutoff_at=datetime(2026, 8, 24, 23, 59, 59, tzinfo=timezone.utc),
                book_cutoff_at=datetime(2026, 8, 24, 23, 59, 59, tzinfo=timezone.utc),
                knowledge_cutoff_at=knowledge_cutoff,
                statement_reference="statement-entry",
                journal_reference="journal-reference",
                statement_amount=Decimal("25000"),
                journal_amount=Decimal("25000"),
            )
        journal_query, journal_parameters = connection.execute.call_args_list[1].args
        self.assertIn("journal.posted_at <= %s", journal_query)
        self.assertEqual(journal_parameters[-1], knowledge_cutoff)

    def test_match_command_maps_source_conservation_guard_to_validation(self) -> None:
        """A legacy cross-run amount conflict cannot escape as a raw database error."""
        command = self._command()
        tenant = self.case.policy.tenant_reference
        with psycopg.connect(posting.DATABASE_URL) as connection:
            statement_id = connection.execute(
                """
                SELECT bank_statement_record_id
                FROM accounting_core.reconciliation_run_command
                WHERE tenant_account_id = %s AND reconciliation_run_id = %s
                """,
                (self.case.tenant_id, command["reconciliation_run_id"]),
            ).fetchone()[0]
            source_hash = connection.execute(
                """
                SELECT source_artifact_hash
                FROM accounting_integration.bank_statement_record
                WHERE tenant_account_id = %s AND bank_statement_record_id = %s
                """,
                (self.case.tenant_id, statement_id),
            ).fetchone()[0]
        second_run = accept_reconciliation_run(
            {
                "tenant_reference": tenant,
                "bank_statement_record_id": statement_id,
                "legal_entity_reference": self.case.policy.legal_entity_reference,
                "accounting_book_reference": self.case.policy.accounting_book_reference,
                "bank_cutoff_at": "2026-08-24T23:59:59Z",
                "book_cutoff_at": "2026-08-24T23:59:59Z",
                "matching_policy_version": "deterministic-v1",
                "knowledge_cutoff_at": "2026-09-01T00:00:00Z",
                "reconciliation_idempotency_key": f"run-conflict-{uuid.uuid4().hex}",
                "source_payload_hash": source_hash,
            },
            posting.DATABASE_URL,
            tenant,
        )
        with psycopg.connect(posting.DATABASE_URL) as connection:
            connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_candidate (
                    tenant_account_id, reconciliation_run_id,
                    statement_entry_reference, journal_reference,
                    statement_amount, journal_amount, rule_code
                )
                VALUES (%s, %s, %s, %s, '24999.99', '24999.99', 'legacy-conflict')
                """,
                (
                    self.case.tenant_id,
                    second_run["reconciliation_run_id"],
                    command["statement_entry_reference"],
                    command["journal_reference"],
                ),
            )
        with self.assertRaisesRegex(AccountingValidationError, "conservation evidence"):
            accept_reconciliation_match(command, posting.DATABASE_URL, tenant)

    def test_match_command_uuid_errors_refer_to_match(self) -> None:
        """Match endpoints use match-specific recovery guidance for UUID errors."""
        command = self._command()
        tenant = self.case.policy.tenant_reference
        with self.assertRaisesRegex(AccountingValidationError, "retry the match"):
            accept_reconciliation_match(
                dict(command, reconciliation_run_id="not-a-uuid"),
                posting.DATABASE_URL,
                tenant,
            )
        with self.assertRaisesRegex(AccountingValidationError, "retry the match"):
            lookup_reconciliation_match(posting.DATABASE_URL, tenant, "not-a-uuid")

    def test_match_command_validation_and_run_lifecycle_fail_closed(self) -> None:
        """Malformed, missing-run, and non-evaluating commands write no evidence."""
        command = self._command()
        tenant = self.case.policy.tenant_reference
        invalid_commands = (
            ([], "payload"),
            (dict(command, tenant_reference="urn:cwl:tenant:other"), "tenant_reference"),
            (dict(command, statement_entry_reference=""), "statement_entry_reference"),
            (dict(command, source_payload_hash="not-a-hash"), "source_payload_hash"),
            (dict(command, source_payload_reference=" evidence"), "source_payload_reference"),
            (dict(command, statement_amount="0"), "greater than zero"),
        )
        for invalid, message in invalid_commands:
            with self.subTest(message=message):
                with self.assertRaisesRegex(AccountingValidationError, message):
                    accept_reconciliation_match(invalid, posting.DATABASE_URL, tenant)

        missing_run = dict(
            command,
            reconciliation_run_id=str(uuid.uuid4()),
            candidate_idempotency_key=f"missing-run-{uuid.uuid4().hex}",
        )
        with self.assertRaisesRegex(AccountingValidationError, "not recorded"):
            accept_reconciliation_match(missing_run, posting.DATABASE_URL, tenant)

        accept_reconciliation_match(command, posting.DATABASE_URL, tenant)
        duplicate_source = dict(
            command,
            candidate_idempotency_key=f"duplicate-source-{uuid.uuid4().hex}",
        )
        with self.assertRaisesRegex(AccountingValidationError, "already recorded"):
            accept_reconciliation_match(duplicate_source, posting.DATABASE_URL, tenant)

        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                """
                UPDATE accounting_core.reconciliation_run
                SET run_status_code = 'review_required'
                WHERE reconciliation_run_id = %s
                """,
                (command["reconciliation_run_id"],),
            )
        non_evaluating = dict(
            command,
            candidate_idempotency_key=f"non-evaluating-{uuid.uuid4().hex}",
        )
        with self.assertRaisesRegex(AccountingValidationError, "evaluating"):
            accept_reconciliation_match(non_evaluating, posting.DATABASE_URL, tenant)

    def test_http_routes_persist_and_read_the_proposed_match(self) -> None:
        """HTTP exposes the proposed match while preserving tenant and identity gates."""
        command = self._command()
        server = self.case._start_http_server()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        status, created = self.case._http_json(
            "POST", "/reconciliation-matches", command
        )
        read_status, read = self.case._http_json(
            "GET",
            f"/reconciliation-matches?reconciliation_match_id={created['reconciliation_match_id']}",
            None,
        )
        conflict_status, _conflict = self.case._http_json(
            "POST",
            "/reconciliation-matches",
            dict(command, source_payload_hash="sha256:" + "3" * 64),
        )
        missing_journal_status, _missing_journal = self.case._http_json(
            "POST",
            "/reconciliation-matches",
            dict(
                command,
                journal_reference="urn:cwl:accounting:general_journal:missing",
                candidate_idempotency_key=f"http-missing-journal-{uuid.uuid4().hex}",
            ),
        )
        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:
            connection.execute(
                """
                UPDATE accounting_core.reconciliation_run
                SET run_status_code = 'review_required'
                WHERE reconciliation_run_id = %s
                """,
                (command["reconciliation_run_id"],),
            )
        state_conflict_status, _state_conflict = self.case._http_json(
            "POST",
            "/reconciliation-matches",
            dict(command, candidate_idempotency_key=f"http-state-{uuid.uuid4().hex}"),
        )
        wrong_status, _wrong = self.case._http_json(
            "POST",
            "/reconciliation-matches",
            dict(command, tenant_reference="urn:cwl:tenant:other"),
        )
        missing_header_status, _missing_header = self.case._http_json(
            "POST", "/reconciliation-matches", command, tenant_header=None
        )
        invalid_body_status, _invalid_body = self.case._http_raw(
            "POST", "/reconciliation-matches", b"[]", self.case.policy.tenant_reference
        )
        invalid_command_status, _invalid_command = self.case._http_json(
            "POST",
            "/reconciliation-matches",
            dict(
                command,
                candidate_idempotency_key=f"http-invalid-{uuid.uuid4().hex}",
                statement_amount="0",
            ),
        )
        missing_id_status, _missing_id = self.case._http_json(
            "GET", "/reconciliation-matches", None
        )
        missing_get_header_status, _missing_get_header = self.case._http_json(
            "GET", "/reconciliation-matches?reconciliation_match_id=not-a-uuid", None,
            tenant_header=None,
        )
        invalid_id_status, _invalid_id = self.case._http_json(
            "GET", "/reconciliation-matches?reconciliation_match_id=not-a-uuid", None
        )
        missing_status, _missing = self.case._http_json(
            "GET",
            f"/reconciliation-matches?reconciliation_match_id={uuid.uuid4()}",
            None,
        )
        self.assertEqual(status, 200)
        self.assertEqual(read_status, 200)
        self.assertEqual(read["reconciliation_match_id"], created["reconciliation_match_id"])
        self.assertEqual(conflict_status, 409)
        self.assertEqual(missing_journal_status, 404)
        self.assertEqual(state_conflict_status, 409)
        self.assertEqual(wrong_status, 403)
        self.assertEqual(missing_header_status, 400)
        self.assertEqual(invalid_body_status, 400)
        self.assertEqual(invalid_command_status, 422)
        self.assertEqual(missing_id_status, 400)
        self.assertEqual(missing_get_header_status, 400)
        self.assertEqual(invalid_id_status, 400)
        self.assertEqual(missing_status, 404)


if __name__ == "__main__":
    unittest.main()
