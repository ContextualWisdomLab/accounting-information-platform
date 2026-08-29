"""RED/GREEN contracts for the proposed reconciliation-match command API."""

from __future__ import annotations

import hashlib
import unittest
import uuid

import psycopg

from accounting_information_platform import (
    CAMT053_MESSAGE_DEFINITION,
    AccountingValidationError,
    IdempotencyConflictError,
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

    def _open_run(self) -> tuple[str, str]:
        fixture = load_canonical_statement_fixture()
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
        entry = entries[0]
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
                "knowledge_cutoff_at": "2026-08-25T00:00:00Z",
                "reconciliation_idempotency_key": f"run-match-{uuid.uuid4().hex}",
                "source_payload_hash": statement_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        return str(run["reconciliation_run_id"]), str(
            entry["source_entry_identity"] or entry["bank_statement_entry_id"]
        )

    def _command(self) -> dict[str, object]:
        run_id, statement_reference = self._open_run()
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "reconciliation_run_id": run_id,
            "statement_entry_reference": statement_reference,
            "journal_reference": "journal-match-fixture",
            "statement_amount": "25000.00",
            "journal_amount": "25000.00",
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
