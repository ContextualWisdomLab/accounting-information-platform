"""RED/GREEN contracts for the tenant-scoped reconciliation-run command API."""

from __future__ import annotations

import hashlib
import unittest
import uuid
from contextlib import nullcontext
from unittest import mock

import psycopg

from accounting_information_platform import (
    CAMT053_MESSAGE_DEFINITION,
    AccountingValidationError,
    IdempotencyConflictError,
    MemoryArtifactStore,
    accept_bank_account_assignment,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    accept_reconciliation_run,
    load_canonical_statement_fixture,
    lookup_reconciliation_run,
)
from accounting_information_platform.reconciliation_run import (
    _parse_timestamp,
    _parse_uuid,
    _require_command,
)
from tests import test_postgres_posting as posting


class ReconciliationRunApiTests(unittest.TestCase):
    """Prove a run is opened only from persisted tenant and bank evidence."""

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
                "assignment_idempotency_key": f"assign-run-{uuid.uuid4().hex}",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )

    def _statement_and_command(self) -> tuple[dict[str, object], dict[str, object]]:
        fixture = load_canonical_statement_fixture()
        statement = accept_bank_statement_evidence(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.account_reference,
                "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                "statement_payload": fixture.decode("utf-8"),
                "ingestion_idempotency_key": f"statement-run-{uuid.uuid4().hex}",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        source_payload_hash = "sha256:" + hashlib.sha256(fixture).hexdigest()
        return statement, {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_statement_record_id": statement["bank_statement_record_id"],
            "legal_entity_reference": self.case.policy.legal_entity_reference,
            "accounting_book_reference": self.case.policy.accounting_book_reference,
            "bank_cutoff_at": "2026-08-24T23:59:59Z",
            "book_cutoff_at": "2026-08-24T23:59:59Z",
            "matching_policy_version": "deterministic-v1",
            "knowledge_cutoff_at": "2026-08-25T00:00:00Z",
            "reconciliation_idempotency_key": f"run-{uuid.uuid4().hex}",
            "source_payload_hash": source_payload_hash,
        }

    def test_open_run_binds_statement_scope_and_replays(self) -> None:
        """An exact command opens one evaluating run and an exact retry replays it."""
        statement, command = self._statement_and_command()
        first = accept_reconciliation_run(
            command, posting.DATABASE_URL, self.case.policy.tenant_reference
        )
        replay = accept_reconciliation_run(
            command, posting.DATABASE_URL, self.case.policy.tenant_reference
        )
        self.assertEqual(first["run_status_code"], "evaluating")
        self.assertEqual(first["currency_code"], "KRW")
        self.assertEqual(first["bank_statement_record_id"], statement["bank_statement_record_id"])
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["reconciliation_run_id"], replay["reconciliation_run_id"])

    def test_same_key_changed_command_fails_without_new_run(self) -> None:
        """A reused key cannot alter the immutable run command evidence."""
        _statement, command = self._statement_and_command()
        first = accept_reconciliation_run(
            command, posting.DATABASE_URL, self.case.policy.tenant_reference
        )
        changed = dict(command, matching_policy_version="deterministic-v2")
        with self.assertRaises(IdempotencyConflictError):
            accept_reconciliation_run(
                changed, posting.DATABASE_URL, self.case.policy.tenant_reference
            )
        replay = accept_reconciliation_run(
            command, posting.DATABASE_URL, self.case.policy.tenant_reference
        )
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["reconciliation_run_id"], replay["reconciliation_run_id"])

    def test_wrong_source_hash_fails_before_run_persistence(self) -> None:
        """A run cannot claim a different immutable bank-statement source."""
        _statement, command = self._statement_and_command()
        command["source_payload_hash"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(AccountingValidationError, "does not match"):
            accept_reconciliation_run(
                command, posting.DATABASE_URL, self.case.policy.tenant_reference
            )

    def test_command_validation_rejects_noncanonical_input_before_database(self) -> None:
        """Command identity, source hash, cutoff, and timestamp boundaries fail closed."""
        _statement, command = self._statement_and_command()
        cases = (
            (dict(command, matching_policy_version=" deterministic-v1"), "matching_policy"),
            (dict(command, matching_policy_version=1), "matching policy type"),
            (dict(command, reconciliation_idempotency_key=""), "idempotency"),
            (dict(command, source_payload_hash="not-a-sha256"), "source hash"),
            (
                dict(command, knowledge_cutoff_at="2026-08-24T00:00:00Z"),
                "knowledge cutoff",
            ),
        )
        for invalid, label in cases:
            with self.subTest(label=label):
                with self.assertRaises(AccountingValidationError):
                    accept_reconciliation_run(
                        invalid, posting.DATABASE_URL, self.case.policy.tenant_reference
                    )
        with self.assertRaises(AccountingValidationError):
            _require_command([], self.case.policy.tenant_reference)
        with self.assertRaises(AccountingValidationError):
            _require_command(command, "urn:cwl:tenant:other")
        with self.assertRaises(AccountingValidationError):
            _parse_uuid("not-a-uuid", "reconciliation_run_id")
        with self.assertRaises(AccountingValidationError):
            _parse_timestamp("not-a-timestamp", "bank_cutoff_at")
        with self.assertRaisesRegex(AccountingValidationError, "explicit UTC"):
            _parse_timestamp("2026-08-24T23:59:59", "bank_cutoff_at")
        with self.assertRaisesRegex(AccountingValidationError, "explicit UTC"):
            _parse_timestamp("2026-08-24T23:59:59+09:00", "bank_cutoff_at")

    def test_statement_period_must_be_covered_by_bank_cutoff(self) -> None:
        """A run cannot use a bank cutoff before the statement period or period end."""
        _statement, command = self._statement_and_command()
        no_binding = dict(
            command,
            legal_entity_reference="urn:cwl:legal_entity:not-the-fixture",
        )
        with self.assertRaisesRegex(AccountingValidationError, "not bound"):
            accept_reconciliation_run(
                no_binding, posting.DATABASE_URL, self.case.policy.tenant_reference
            )
        before_start = dict(
            command,
            bank_cutoff_at="2026-08-22T23:59:59Z",
            knowledge_cutoff_at="2026-08-25T00:00:00Z",
        )
        with self.assertRaisesRegex(AccountingValidationError, "before the statement period"):
            accept_reconciliation_run(
                before_start, posting.DATABASE_URL, self.case.policy.tenant_reference
            )
        before_end = dict(
            command,
            bank_cutoff_at="2026-08-24T12:00:00Z",
            knowledge_cutoff_at="2026-08-25T00:00:00Z",
        )
        with self.assertRaisesRegex(
            AccountingValidationError, "before the statement period end"
        ):
            accept_reconciliation_run(
                before_end, posting.DATABASE_URL, self.case.policy.tenant_reference
            )

    def test_multiple_active_bindings_fail_closed_even_if_database_returns_them(self) -> None:
        """The application does not silently choose when binding evidence is ambiguous."""
        _statement, command = self._statement_and_command()
        fake_connection = mock.Mock()
        fake_connection.execute.return_value.fetchall.return_value = [
            (uuid.uuid4(), None, None, None, None, None, None, None, None, None, None),
            (uuid.uuid4(), None, None, None, None, None, None, None, None, None, None),
        ]
        fake_ledger = mock.Mock()
        fake_ledger._require_tenant.return_value = uuid.uuid4()
        fake_ledger._session.return_value = nullcontext(fake_connection)
        with mock.patch(
            "accounting_information_platform.reconciliation_run.PostgresPostingLedger",
            return_value=fake_ledger,
        ):
            with self.assertRaisesRegex(AccountingValidationError, "more than one"):
                accept_reconciliation_run(
                    command, posting.DATABASE_URL, self.case.policy.tenant_reference
                )

    def test_lookup_and_http_routes_return_same_scoped_run(self) -> None:
        """The public read and HTTP command routes expose only the evaluating run."""
        _statement, command = self._statement_and_command()
        server = self.case._start_http_server()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        status, created = self.case._http_json("POST", "/reconciliation-runs", command)
        read_status, read = self.case._http_json(
            "GET",
            f"/reconciliation-runs?reconciliation_run_id={created['reconciliation_run_id']}",
            None,
        )
        direct = lookup_reconciliation_run(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(created["reconciliation_run_id"]),
        )
        wrong_payload = dict(command, tenant_reference="urn:cwl:tenant:other")
        wrong_status, _wrong = self.case._http_json(
            "POST", "/reconciliation-runs", wrong_payload
        )
        missing_header_post_status, _missing_header_post = self.case._http_json(
            "POST", "/reconciliation-runs", command, tenant_header=None
        )
        missing_header_get_status, _missing_header_get = self.case._http_json(
            "GET", "/reconciliation-runs", None, tenant_header=None
        )
        wrong_header_get_status, _wrong_header_get = self.case._http_json(
            "GET",
            f"/reconciliation-runs?reconciliation_run_id={created['reconciliation_run_id']}",
            None,
            tenant_header="urn:cwl:tenant:other",
        )
        invalid_body_status, _invalid_body = self.case._http_raw(
            "POST", "/reconciliation-runs", b"[]", self.case.policy.tenant_reference
        )
        self.assertEqual(status, 200)
        self.assertEqual(read_status, 200)
        self.assertEqual(created["run_status_code"], "evaluating")
        self.assertEqual(read["reconciliation_run_id"], created["reconciliation_run_id"])
        self.assertEqual(direct["reconciliation_run_id"], created["reconciliation_run_id"])
        self.assertEqual(wrong_status, 403)
        self.assertEqual(missing_header_post_status, 400)
        self.assertEqual(missing_header_get_status, 400)
        self.assertEqual(wrong_header_get_status, 403)
        self.assertEqual(invalid_body_status, 400)

        conflict_status, _conflict = self.case._http_json(
            "POST",
            "/reconciliation-runs",
            dict(command, matching_policy_version="deterministic-v2"),
        )
        bad_source_status, _bad_source = self.case._http_json(
            "POST",
            "/reconciliation-runs",
            dict(
                command,
                reconciliation_idempotency_key=f"run-bad-{uuid.uuid4().hex}",
                source_payload_hash="sha256:" + "0" * 64,
            ),
        )
        missing_id_status, _missing_id = self.case._http_json(
            "GET", "/reconciliation-runs", None
        )
        invalid_id_status, _invalid_id = self.case._http_json(
            "GET", "/reconciliation-runs?reconciliation_run_id=not-a-uuid", None
        )
        missing_status, _missing = self.case._http_json(
            "GET",
            f"/reconciliation-runs?reconciliation_run_id={uuid.uuid4()}",
            None,
        )
        missing_policy_status, _missing_policy = self.case._http_json(
            "POST",
            "/reconciliation-runs",
            dict(command, reconciliation_idempotency_key=f"run-policy-{uuid.uuid4().hex}", matching_policy_version=""),
        )
        self.assertEqual(conflict_status, 409)
        self.assertEqual(bad_source_status, 422)
        self.assertEqual(missing_id_status, 400)
        self.assertEqual(invalid_id_status, 400)
        self.assertEqual(missing_status, 404)
        self.assertEqual(missing_policy_status, 400)

    def test_run_command_evidence_is_immutable(self) -> None:
        """The database trigger prevents mutation of recorded run command evidence."""
        _statement, command = self._statement_and_command()
        document = accept_reconciliation_run(
            command, posting.DATABASE_URL, self.case.policy.tenant_reference
        )
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaises(psycopg.Error):
                connection.execute(
                    """
                    UPDATE accounting_core.reconciliation_run_command
                    SET source_payload_reference = 'memory:tampered'
                    WHERE reconciliation_run_id = %s
                    """,
                    (document["reconciliation_run_id"],),
                )


if __name__ == "__main__":
    unittest.main()
