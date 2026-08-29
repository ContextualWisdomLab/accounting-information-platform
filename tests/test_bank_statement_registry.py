"""PostgreSQL acceptance tests for the immutable bank-statement evidence registry."""

from __future__ import annotations

import hashlib
import unittest
import uuid
from datetime import datetime, timezone
from unittest import mock
from urllib.parse import quote

import psycopg

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    IdempotencyConflictError,
    MemoryArtifactStore,
    accept_bank_account_assignment,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
    lookup_bank_statement,
    lookup_bank_statement_entries,
    lookup_bank_statements,
)
from tests import test_postgres_posting as posting


VALID_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)


class BankStatementRegistryTests(unittest.TestCase):
    """Prove ingest, replay, parser-security persistence, and tenant/book scope."""

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
                "assignment_idempotency_key": f"assign-setup-{uuid.uuid4().hex}",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )

    def test_valid_fixture_persists_one_statement_and_exact_entries(self) -> None:
        """A valid camt.053.001.14 fixture produces one statement and exact entries."""
        document = self._ingest(load_canonical_statement_fixture())
        self.assertFalse(document["replayed"])
        self.assertEqual(document["message_definition_identifier"], CAMT053_MESSAGE_DEFINITION)
        self.assertEqual(document["statement_identity_reference"], "BANK-STMT-2026-08-24")
        self.assertEqual(document["entry_count"], 2)
        self.assertEqual(document["credit_total_amount"], "25000")
        self.assertEqual(document["debit_total_amount"], "10000")
        self.assertEqual(
            document["balances"],
            [
                {
                    "balance_sequence_number": 1,
                    "balance_type_code": "OPBD",
                    "balance_amount": "100000",
                    "balance_currency_code": "KRW",
                    "credit_debit_code": "CRDT",
                    "balance_effective_at": "2026-08-23T00:00:00Z",
                    "source_locator_path": "Document/BkToCstmrStmt/Stmt/Bal[1]",
                    "source_balance_hash": document["balances"][0]["source_balance_hash"],
                },
                {
                    "balance_sequence_number": 2,
                    "balance_type_code": "CLBD",
                    "balance_amount": "115000",
                    "balance_currency_code": "KRW",
                    "credit_debit_code": "CRDT",
                    "balance_effective_at": "2026-08-24T00:00:00Z",
                    "source_locator_path": "Document/BkToCstmrStmt/Stmt/Bal[2]",
                    "source_balance_hash": document["balances"][1]["source_balance_hash"],
                },
            ],
        )
        self.assertTrue(document["artifact_store_reference"].startswith("memory:"))
        entries = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(document["bank_statement_record_id"]),
        )["bank_statement_entries"]
        self.assertEqual(entries[0]["credit_debit_code"], "CRDT")
        self.assertEqual(entries[0]["entry_amount"], "25000")
        self.assertEqual(entries[1]["credit_debit_code"], "DBIT")
        self.assertEqual(entries[1]["entry_amount"], "10000")
        self.assertEqual(len(entries[1]["entry_details"]), 2)
        self.assertEqual(self.case.ledger.journal_count, 0)

    def test_identical_bytes_replay_without_duplicate_rows(self) -> None:
        """Second delivery of identical bytes is an exact replay."""
        first = self._ingest(load_canonical_statement_fixture(), key="key-a")
        second = self._ingest(load_canonical_statement_fixture(), key="key-a")
        third = self._ingest(load_canonical_statement_fixture(), key="key-b")
        self.assertTrue(second["replayed"])
        self.assertTrue(third["replayed"])
        self.assertEqual(first["bank_statement_record_id"], second["bank_statement_record_id"])
        self.assertEqual(first["bank_statement_record_id"], third["bank_statement_record_id"])
        self.assertEqual(self._count("accounting_integration.bank_statement_record"), 1)
        self.assertEqual(self._count("accounting_integration.bank_statement_entry"), 2)
        self.assertEqual(self._count("accounting_integration.bank_statement_balance"), 2)

    def test_same_key_changed_bytes_writes_nothing(self) -> None:
        """Same idempotency key with changed bytes conflicts and writes nothing."""
        original = load_canonical_statement_fixture()
        self._ingest(original, key="stable-key")
        changed = original.replace(b"25000.00", b"25001.00", 1)
        with self.assertRaises(IdempotencyConflictError):
            self._ingest(changed, key="stable-key")
        self.assertEqual(self._count("accounting_integration.bank_statement_record"), 1)
        self.assertEqual(self._count("accounting_integration.bank_statement_entry"), 2)

    def test_same_statement_identity_changed_entries_fail_closed(self) -> None:
        """Same statement identity with changed material evidence writes nothing."""
        original = load_canonical_statement_fixture()
        self._ingest(original, key="first-identity")
        changed = original.replace(b"25000.00", b"26000.00")
        with self.assertRaisesRegex(AccountingValidationError, "different entry evidence"):
            self._ingest(changed, key="second-identity")
        self.assertEqual(self._count("accounting_integration.bank_statement_record"), 1)

    def test_balance_currency_mismatch_fails_closed_before_persist(self) -> None:
        """A balance outside the registered account currency cannot enter evidence."""
        payload = load_canonical_statement_fixture().replace(
            b'<Amt Ccy="KRW">100000.00</Amt>', b'<Amt Ccy="USD">100000.00</Amt>', 1
        )
        with self.assertRaisesRegex(AccountingValidationError, "statement balance"):
            self._ingest(payload, key="balance-currency-mismatch")
        self.assertEqual(self._count("accounting_integration.bank_statement_record"), 0)
        self.assertEqual(self._count("accounting_integration.bank_statement_balance"), 0)

    def test_parser_failures_write_no_partial_population(self) -> None:
        """Revision, DTD, bound, and decimal failures persist zero rows."""
        original = load_canonical_statement_fixture().decode("utf-8")
        cases = (
            original.replace("camt.053.001.14", "camt.053.001.13").encode("utf-8"),
            (
                '<?xml version="1.0"?><!DOCTYPE Document [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
                '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.14">&xxe;</Document>'
            ).encode("utf-8"),
            original.replace("25000.00", "1e4", 1).encode("utf-8"),
        )
        for index, payload in enumerate(cases):
            with self.subTest(index=index):
                with self.assertRaises(AccountingValidationError):
                    self._ingest(payload, key=f"fail-{index}")
        self.assertEqual(self._count("accounting_integration.bank_statement_record"), 0)
        self.assertEqual(self._count("accounting_integration.bank_statement_entry"), 0)
        self.assertEqual(self._count("accounting_integration.bank_statement_balance"), 0)
        self.assertEqual(self._count("accounting_integration.bank_statement_artifact"), 0)

    def test_debit_credit_survives_persist_read_round_trip(self) -> None:
        """Exact debit/credit amounts survive normalize, persist, and read."""
        document = self._ingest(load_canonical_statement_fixture())
        loaded = lookup_bank_statement(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(document["bank_statement_record_id"]),
        )
        self.assertEqual(loaded["credit_total_amount"], "25000")
        self.assertEqual(loaded["debit_total_amount"], "10000")
        self.assertEqual(
            loaded["balances"][0]["balance_effective_at"], "2026-08-23T00:00:00Z"
        )
        listed = lookup_bank_statements(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            self.account_reference,
            period_start="2026-08-23T00:00:00Z",
            period_end="2026-08-24T23:59:59Z",
        )
        self.assertEqual(len(listed["bank_statements"]), 1)
        page = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(document["bank_statement_record_id"]),
            page_limit=1,
        )
        self.assertEqual(len(page["bank_statement_entries"]), 1)
        self.assertEqual(page["next_cursor"], "1")
        rest = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(document["bank_statement_record_id"]),
            page_limit=1,
            cursor=str(page["next_cursor"]),
        )
        self.assertEqual(rest["bank_statement_entries"][0]["credit_debit_code"], "DBIT")

    def test_missing_balance_effective_date_round_trips_as_unavailable(self) -> None:
        """Unavailable balance dates remain explicit null evidence after persistence."""
        payload = load_canonical_statement_fixture().replace(
            b"        <Dt>\n          <Dt>2026-08-23</Dt>\n        </Dt>\n",
            b"",
            1,
        )
        document = self._ingest(payload, key="balance-date-missing")
        self.assertIsNone(document["balances"][0]["balance_effective_at"])

    def test_cross_tenant_assignment_and_read_fail(self) -> None:
        """Another tenant cannot assign, read, or overwrite this bank account."""
        other = posting.PostgresPostingTests("setUp")
        other.setUp()
        self.addCleanup(other.doCleanups)
        self.addCleanup(other.tearDown)
        with self.assertRaisesRegex(AccountingValidationError, "not recorded"):
            accept_bank_account_assignment(
                {
                    "tenant_reference": other.policy.tenant_reference,
                    "bank_account_reference": self.account_reference,
                    "legal_entity_reference": other.policy.legal_entity_reference,
                    "accounting_book_reference": other.policy.accounting_book_reference,
                    "chart_account_code": "110200",
                    "valid_from": "2026-01-01T00:00:00Z",
                    "assignment_idempotency_key": f"assign-cross-{uuid.uuid4().hex}",
                },
                posting.DATABASE_URL,
                other.policy.tenant_reference,
            )
        document = self._ingest(load_canonical_statement_fixture())
        with self.assertRaisesRegex(AccountingValidationError, "not recorded"):
            lookup_bank_statement(
                posting.DATABASE_URL,
                other.policy.tenant_reference,
                str(document["bank_statement_record_id"]),
            )
        foreign = self._command(load_canonical_statement_fixture(), key="other-tenant")
        foreign["tenant_reference"] = other.policy.tenant_reference
        with self.assertRaisesRegex(AccountingValidationError, "not recorded"):
            accept_bank_statement_evidence(
                foreign,
                posting.DATABASE_URL,
                other.policy.tenant_reference,
                artifact_store=MemoryArtifactStore(),
            )

    def test_assignment_to_other_book_chart_account_fails_at_postgres(self) -> None:
        """A chart account from another book is rejected at the PostgreSQL boundary."""
        other_book_id, other_chart_id = self._seed_second_book()
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = connection.execute(
                "SELECT tenant_account_id FROM accounting_core.tenant_account WHERE tenant_account_code = %s",
                (self.case.policy.tenant_reference,),
            ).fetchone()[0]
            account_id = connection.execute(
                """
                SELECT bank_account_record_id
                FROM accounting_core.bank_account_record
                WHERE tenant_account_id = %s AND bank_account_reference = %s
                """,
                (tenant_id, self.account_reference),
            ).fetchone()[0]
            legal_entity_id, book_id = connection.execute(
                """
                SELECT legal_entity_id, accounting_book_id
                FROM accounting_core.accounting_book
                WHERE tenant_account_id = %s AND book_name = %s
                """,
                (tenant_id, self.case.policy.accounting_book_reference),
            ).fetchone()
            with self.assertRaises(psycopg.Error):
                connection.execute(
                    """
                    INSERT INTO accounting_core.bank_account_assignment (
                        tenant_account_id, bank_account_record_id, legal_entity_id,
                        accounting_book_id, chart_account_id, valid_from,
                        assignment_idempotency_key, assignment_command_hash
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'sha256:probe')
                    """,
                    (
                        tenant_id,
                        account_id,
                        legal_entity_id,
                        book_id,
                        other_chart_id,
                        VALID_FROM,
                        f"assign-fk-probe-{uuid.uuid4().hex}",
                    ),
                )
                connection.commit()
        self.assertIsNotNone(other_book_id)
        with self.assertRaisesRegex(AccountingValidationError, "same accounting book|not recorded"):
            accept_bank_account_assignment(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": self.account_reference,
                    "legal_entity_reference": self.case.policy.legal_entity_reference,
                    "accounting_book_reference": self.case.policy.accounting_book_reference,
                    "chart_account_code": "119900",
                    "valid_from": "2026-02-01T00:00:00Z",
                    "assignment_idempotency_key": f"assign-x1-" + uuid.uuid4().hex,
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
            )

    def test_assignment_to_other_legal_entity_book_fails_at_postgres(self) -> None:
        """A book owned by another legal entity cannot be assigned at PostgreSQL."""
        other_legal_entity_id, other_book_id, other_chart_id = (
            self._seed_other_legal_entity_book()
        )
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = connection.execute(
                "SELECT tenant_account_id FROM accounting_core.tenant_account WHERE tenant_account_code = %s",
                (self.case.policy.tenant_reference,),
            ).fetchone()[0]
            account_id = connection.execute(
                """
                SELECT bank_account_record_id
                FROM accounting_core.bank_account_record
                WHERE tenant_account_id = %s AND bank_account_reference = %s
                """,
                (tenant_id, self.account_reference),
            ).fetchone()[0]
            legal_entity_id = connection.execute(
                """
                SELECT legal_entity_id
                FROM accounting_core.legal_entity_record
                WHERE tenant_account_id = %s AND legal_entity_code = %s
                """,
                (tenant_id, self.case.policy.legal_entity_reference),
            ).fetchone()[0]
            self.assertNotEqual(legal_entity_id, other_legal_entity_id)
            with self.assertRaises(psycopg.Error):
                connection.execute(
                    """
                    INSERT INTO accounting_core.bank_account_assignment (
                        tenant_account_id, bank_account_record_id, legal_entity_id,
                        accounting_book_id, chart_account_id, valid_from,
                        assignment_idempotency_key, assignment_command_hash
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'sha256:probe')
                    """,
                    (
                        tenant_id,
                        account_id,
                        legal_entity_id,
                        other_book_id,
                        other_chart_id,
                        VALID_FROM,
                        f"assign-fk-probe-2-{uuid.uuid4().hex}",
                    ),
                )
                connection.commit()

    def test_assignment_idempotency_key_is_required(self) -> None:
        """An assignment without tenant-scoped idempotency identity fails closed."""
        with self.assertRaisesRegex(
            AccountingValidationError, "assignment_idempotency_key is required"
        ):
            accept_bank_account_assignment(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": self.account_reference,
                    "legal_entity_reference": self.case.policy.legal_entity_reference,
                    "accounting_book_reference": self.case.policy.accounting_book_reference,
                    "chart_account_code": "110200",
                    "valid_from": "2026-01-01T00:00:00Z",
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
            )

    def test_assignment_replay_returns_original_and_conflicts_on_change(self) -> None:
        """Exact assignment replay returns the original binding; changed reuse fails closed."""
        key = f"assign-replay-{uuid.uuid4().hex}"
        command = {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": f"urn:cwl:bank_account:{uuid.uuid4().hex}",
            "legal_entity_reference": self.case.policy.legal_entity_reference,
            "accounting_book_reference": self.case.policy.accounting_book_reference,
            "chart_account_code": "110200",
            "valid_from": "2026-02-01T00:00:00Z",
            "assignment_idempotency_key": key,
        }
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": command["bank_account_reference"],
                "account_currency_code": "KRW",
                "account_identifier": "acct-opaque-fixture-only-2",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        first = accept_bank_account_assignment(
            dict(command), posting.DATABASE_URL, self.case.policy.tenant_reference
        )
        self.assertFalse(first["replayed"])
        second = accept_bank_account_assignment(
            dict(command), posting.DATABASE_URL, self.case.policy.tenant_reference
        )
        self.assertTrue(second["replayed"])
        self.assertEqual(
            first["bank_account_assignment_id"], second["bank_account_assignment_id"]
        )
        changed = dict(command)
        changed["chart_account_code"] = "110100"
        with self.assertRaises(IdempotencyConflictError):
            accept_bank_account_assignment(
                changed, posting.DATABASE_URL, self.case.policy.tenant_reference
            )

    def test_second_active_assignment_same_book_fails_closed(self) -> None:
        """A second active binding for one bank account and book is a data defect."""
        reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": reference,
                "account_currency_code": "KRW",
                "account_identifier": "acct-opaque-fixture-only-3",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        base = {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": reference,
            "legal_entity_reference": self.case.policy.legal_entity_reference,
            "accounting_book_reference": self.case.policy.accounting_book_reference,
            "chart_account_code": "110200",
            "valid_from": "2026-03-01T00:00:00Z",
        }
        accept_bank_account_assignment(
            {**base, "assignment_idempotency_key": f"assign-first-{uuid.uuid4().hex}"},
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        with self.assertRaisesRegex(AccountingValidationError, "active assignment"):
            accept_bank_account_assignment(
                {**base, "assignment_idempotency_key": f"assign-second-{uuid.uuid4().hex}"},
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
            )

    def test_entry_currency_outside_account_scope_fails_closed(self) -> None:
        """An entry in another currency is rejected before any evidence persists."""
        payload = load_canonical_statement_fixture().replace(
            b'<Amt Ccy="KRW">25000.00</Amt>',
            b'<Amt Ccy="USD">25000.00</Amt>',
            1,
        )
        with self.assertRaisesRegex(AccountingValidationError, "currency"):
            self._ingest(payload, key="fx-entry")
        self.assertEqual(self._count("accounting_integration.bank_statement_record"), 0)
        self.assertEqual(self._count("accounting_integration.bank_statement_entry"), 0)

    def test_assignment_key_reuse_conflicts_over_http_with_409(self) -> None:
        """Reuse of an assignment key with different evidence maps to HTTP 409."""
        server = self.case._start_http_server()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        tenant = self.case.policy.tenant_reference
        reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        registered, _ = self.case._http_json(
            "POST",
            "/bank-accounts",
            {
                "tenant_reference": tenant,
                "bank_account_reference": reference,
                "account_currency_code": "KRW",
                "account_identifier": "acct-opaque-fixture-only-http",
            },
        )
        self.assertEqual(registered, 200)
        key = f"assign-conflict-{uuid.uuid4().hex}"
        first_status, _ = self.case._http_json(
            "POST",
            "/bank-account-assignments",
            {
                "tenant_reference": tenant,
                "bank_account_reference": reference,
                "legal_entity_reference": self.case.policy.legal_entity_reference,
                "accounting_book_reference": self.case.policy.accounting_book_reference,
                "chart_account_code": "110200",
                "valid_from": "2026-06-01T00:00:00Z",
                "assignment_idempotency_key": key,
            },
        )
        self.assertEqual(first_status, 200)
        conflict_status, conflict_body = self.case._http_json(
            "POST",
            "/bank-account-assignments",
            {
                "tenant_reference": tenant,
                "bank_account_reference": reference,
                "legal_entity_reference": self.case.policy.legal_entity_reference,
                "accounting_book_reference": self.case.policy.accounting_book_reference,
                "chart_account_code": "110100",
                "valid_from": "2026-06-01T00:00:00Z",
                "assignment_idempotency_key": key,
            },
        )
        self.assertEqual(conflict_status, 409)
        self.assertIn("assignment", str(conflict_body).lower())

    def test_statement_account_identifier_must_match_registered_account(self) -> None:
        """A same-currency statement whose IBAN/Othr hash differs is not recorded."""
        other_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": other_reference,
                "account_currency_code": "KRW",
                "account_identifier": "acct-other-iban",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        command = self._command(load_canonical_statement_fixture(), key="identifier-mismatch")
        command["bank_account_reference"] = other_reference
        with self.assertRaisesRegex(AccountingValidationError, "account identifier"):
            accept_bank_statement_evidence(
                command,
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_source_artifact_statement_entry_provenance_is_complete(self) -> None:
        """Every persisted entry keeps artifact, statement, locator, and hash lineage."""
        document = self._ingest(load_canonical_statement_fixture())
        with psycopg.connect(posting.DATABASE_URL) as connection:
            rows = connection.execute(
                """
                SELECT artifact.source_artifact_hash,
                       statement.source_artifact_hash,
                       statement.normalized_payload_hash,
                       entry.source_locator_path,
                       entry.source_entry_hash,
                       detail.source_locator_path
                FROM accounting_integration.bank_statement_entry AS entry
                JOIN accounting_integration.bank_statement_record AS statement
                  ON statement.bank_statement_record_id = entry.bank_statement_record_id
                JOIN accounting_integration.bank_statement_artifact AS artifact
                  ON artifact.bank_statement_artifact_id = statement.bank_statement_artifact_id
                LEFT JOIN accounting_integration.bank_statement_entry_detail AS detail
                  ON detail.bank_statement_entry_id = entry.bank_statement_entry_id
                WHERE statement.bank_statement_record_id = %s
                """,
                (document["bank_statement_record_id"],),
            ).fetchall()
        self.assertGreaterEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row[0], document["source_artifact_hash"])
            self.assertEqual(row[1], document["source_artifact_hash"])
            self.assertEqual(row[2], document["normalized_payload_hash"])
            self.assertTrue(str(row[3]).startswith("Document/BkToCstmrStmt/Stmt/Ntry["))
            self.assertTrue(str(row[4]).startswith("sha256:"))

    def test_statement_rows_are_immutable(self) -> None:
        """UPDATE or DELETE of persisted statement evidence fails at PostgreSQL."""
        document = self._ingest(load_canonical_statement_fixture())
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaises(psycopg.Error):
                connection.execute(
                    """
                    UPDATE accounting_integration.bank_statement_record
                    SET statement_identity_reference = 'mutated'
                    WHERE bank_statement_record_id = %s
                    """,
                    (document["bank_statement_record_id"],),
                )
                connection.commit()

    def test_balance_rows_are_immutable(self) -> None:
        """UPDATE or DELETE of persisted numeric balance evidence fails at PostgreSQL."""
        document = self._ingest(load_canonical_statement_fixture())
        with psycopg.connect(posting.DATABASE_URL) as connection:
            with self.assertRaises(psycopg.Error):
                connection.execute(
                    """
                    UPDATE accounting_integration.bank_statement_balance
                    SET balance_amount = 1
                    WHERE bank_statement_record_id = %s
                    """,
                    (document["bank_statement_record_id"],),
                )
                connection.commit()

    def test_http_accept_list_get_and_conflict(self) -> None:
        """HTTP accept, list, get, replay, and tenant mismatch use the stdlib surface."""
        server = self.case._start_http_server()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        payload = load_canonical_statement_fixture().decode("utf-8")
        command = self._command(payload.encode("utf-8"), key="http-key")
        status, document = self.case._http_json("POST", "/bank-statements", command)
        self.assertEqual(status, 200)
        self.assertEqual(document["entry_count"], 2)
        replay_status, replay = self.case._http_json("POST", "/bank-statements", command)
        self.assertEqual(replay_status, 200)
        self.assertTrue(replay["replayed"])
        conflict = dict(command)
        changed_payload = payload.replace("25000.00", "27000.00")
        conflict["statement_payload"] = changed_payload
        conflict["source_artifact_hash"] = (
            "sha256:" + hashlib.sha256(changed_payload.encode("utf-8")).hexdigest()
        )
        conflict_status, conflict_body = self.case._http_json(
            "POST", "/bank-statements", conflict
        )
        self.assertEqual(conflict_status, 409)
        self.assertIn("idempotency", str(conflict_body).lower())
        listed_status, listed = self.case._http_json(
            "GET",
            f"/bank-statements?bank_account_reference={self.account_reference}",
            None,
        )
        self.assertEqual(listed_status, 200)
        self.assertEqual(len(listed["bank_statements"]), 1)
        one_status, one = self.case._http_json(
            "GET",
            f"/bank-statements?bank_statement_record_id={document['bank_statement_record_id']}",
            None,
        )
        self.assertEqual(one_status, 200)
        self.assertEqual(one["debit_total_amount"], "10000")
        entries_status, entries = self.case._http_json(
            "GET",
            f"/bank-statement-entries?bank_statement_record_id={document['bank_statement_record_id']}",
            None,
        )
        self.assertEqual(entries_status, 200)
        self.assertEqual(len(entries["bank_statement_entries"]), 2)
        forbidden, _error = self.case._http_json(
            "POST",
            "/bank-statements",
            command,
            tenant_header="urn:cwl:tenant_other",
        )
        self.assertEqual(forbidden, 403)
        method_status, _ = self.case._http_json("POST", "/bank-statement-entries", command)
        self.assertEqual(method_status, 405)
        get_account, _ = self.case._http_json("GET", "/bank-accounts", None)
        self.assertEqual(get_account, 405)
        get_assignment, _ = self.case._http_json("GET", "/bank-account-assignments", None)
        self.assertEqual(get_assignment, 405)

    def test_command_validation_and_list_cursors(self) -> None:
        """Missing fields, hash mismatch, and bad cursors fail before persistence."""
        with self.assertRaisesRegex(AccountingValidationError, "JSON object"):
            accept_bank_statement_evidence(
                "nope",
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "bound tenant"):
            accept_bank_statement_evidence(
                {"tenant_reference": "urn:cwl:tenant_other"},
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "ingestion_idempotency_key"):
            accept_bank_statement_evidence(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": self.account_reference,
                    "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                    "statement_payload": "<Document/>",
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "statement_payload"):
            accept_bank_statement_evidence(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": self.account_reference,
                    "ingestion_idempotency_key": "urn:cwl:bank_statement:empty-payload",
                    "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                    "statement_payload": "",
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "chart_account_code"):
            accept_bank_account_assignment(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": self.account_reference,
                    "legal_entity_reference": self.case.policy.legal_entity_reference,
                    "accounting_book_reference": self.case.policy.accounting_book_reference,
                    "valid_from": "2026-03-01T00:00:00Z",
                    "assignment_idempotency_key": f"assign-x2-" + uuid.uuid4().hex,
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "not recorded"):
            accept_bank_account_assignment(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": self.account_reference,
                    "legal_entity_reference": self.case.policy.legal_entity_reference,
                    "accounting_book_reference": "urn:cwl:accounting_book:missing",
                    "chart_account_code": "110200",
                    "valid_from": "2026-03-01T00:00:00Z",
                    "assignment_idempotency_key": f"assign-x3-" + uuid.uuid4().hex,
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
            )
        payload = load_canonical_statement_fixture()
        command = self._command(payload, key="hash-mismatch")
        command["source_artifact_hash"] = "not-a-digest"
        with self.assertRaisesRegex(AccountingValidationError, "sha256"):
            accept_bank_statement_evidence(
                command,
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )
        command["source_artifact_hash"] = "sha256:" + "ab" * 32
        with self.assertRaisesRegex(AccountingValidationError, "does not match"):
            accept_bank_statement_evidence(
                command,
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )
        replay = accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.account_reference,
                "account_currency_code": "KRW",
                "account_identifier": "acct-opaque-fixture-only",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.assertTrue(replay["replayed"])
        with self.assertRaises(IdempotencyConflictError):
            accept_bank_account_record(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": self.account_reference,
                    "account_currency_code": "USD",
                    "account_identifier": "acct-opaque-fixture-only",
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
            )
        with self.assertRaisesRegex(AccountingValidationError, "page_limit"):
            lookup_bank_statements(
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                self.account_reference,
                page_limit=0,
            )
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            lookup_bank_statements(
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                self.account_reference,
                cursor="not-a-cursor",
            )
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            lookup_bank_statement_entries(
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                str(uuid.uuid4()),
                cursor="nope",
            )
        self.assertEqual(self._count("accounting_integration.bank_statement_record"), 0)
        usd_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": usd_reference,
                "account_currency_code": "USD",
                "account_identifier_hash": "sha256:" + hashlib.sha256(b"usd").hexdigest(),
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        usd_command = self._command(load_canonical_statement_fixture(), key="usd")
        usd_command["bank_account_reference"] = usd_reference
        with self.assertRaisesRegex(AccountingValidationError, "currency"):
            accept_bank_statement_evidence(
                usd_command,
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_http_query_validation(self) -> None:
        """Statement GETs require identifiers and reject non-integer page limits."""
        server = self.case._start_http_server()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        missing, _ = self.case._http_json("GET", "/bank-statements", None)
        self.assertEqual(missing, 400)
        bad_limit, _ = self.case._http_json(
            "GET",
            f"/bank-statements?bank_account_reference={self.account_reference}&page_limit=x",
            None,
        )
        self.assertEqual(bad_limit, 400)
        missing_entries, _ = self.case._http_json("GET", "/bank-statement-entries", None)
        self.assertEqual(missing_entries, 400)
        bad_entry_limit, _ = self.case._http_json(
            "GET",
            "/bank-statement-entries?bank_statement_record_id=not-a-uuid&page_limit=x",
            None,
        )
        self.assertEqual(bad_entry_limit, 400)

    def test_http_bank_account_register_and_assignment(self) -> None:
        """HTTP bank-account register and assignment stay tenant-scoped."""
        server = self.case._start_http_server()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        status, document = self.case._http_json(
            "POST",
            "/bank-accounts",
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": reference,
                "account_currency_code": "KRW",
                "account_identifier_hash": "sha256:" + hashlib.sha256(b"other").hexdigest(),
            },
        )
        self.assertEqual(status, 200)
        self.assertFalse(document["replayed"])
        assign_status, assignment = self.case._http_json(
            "POST",
            "/bank-account-assignments",
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": reference,
                "legal_entity_reference": self.case.policy.legal_entity_reference,
                "accounting_book_reference": self.case.policy.accounting_book_reference,
                "chart_account_code": "110200",
                "valid_from": "2026-01-01T00:00:00Z",
            
                "assignment_idempotency_key": "assign-http-1-" + uuid.uuid4().hex,},
        )
        self.assertEqual(assign_status, 200)
        self.assertEqual(assignment["chart_account_code"], "110200")

    def test_same_identity_changed_remittance_fails_closed(self) -> None:
        """A remittance-only change under the same statement identity is not replay."""
        self._ingest(load_canonical_statement_fixture(), key="remittance-original")
        mutated = load_canonical_statement_fixture().replace(b"Invoice 1001", b"Invoice 1999", 1)
        with self.assertRaisesRegex(AccountingValidationError, "different entry evidence"):
            accept_bank_statement_evidence(
                self._command(mutated, key="remittance-changed"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_identity_replay_list_cursor_and_assignment_fk(self) -> None:
        """Same identity with a changed artifact hash replays; list cursors and FK fail closed."""
        original = load_canonical_statement_fixture()
        first = self._ingest(original, key="identity-original")
        mutated = original.replace(b"STMT-2026-08-24-001", b"STMT-2026-08-24-002", 1)
        without_hash = self._command(mutated, key="identity-mutated")
        del without_hash["source_artifact_hash"]
        replayed = accept_bank_statement_evidence(
            without_hash,
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertTrue(replayed["replayed"])
        self.assertEqual(replayed["bank_statement_record_id"], first["bank_statement_record_id"])
        second_identity = original.replace(
            b"<Id>BANK-STMT-2026-08-24</Id>",
            b"<Id>BANK-STMT-2026-08-25</Id>",
            1,
        )
        self._ingest(second_identity, key="second-identity")
        page = lookup_bank_statements(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            self.account_reference,
            page_limit=1,
        )
        self.assertEqual(len(page["bank_statements"]), 1)
        self.assertIsNotNone(page["next_cursor"])
        rest = lookup_bank_statements(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            self.account_reference,
            page_limit=1,
            cursor=str(page["next_cursor"]),
        )
        self.assertEqual(len(rest["bank_statements"]), 1)
        with self.assertRaisesRegex(AccountingValidationError, "not recorded"):
            lookup_bank_statement_entries(
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                str(uuid.uuid4()),
            )
        fk = Exception("foreign key")
        fk.sqlstate = "23503"
        original_execute = psycopg.Connection.execute

        def _raise_assignment_fk(
            self_connection: object, query: object, *args: object, **kwargs: object
        ) -> object:
            sql = query if isinstance(query, str) else str(query)
            if "INSERT INTO accounting_core.bank_account_assignment" in sql:
                raise fk
            return original_execute(self_connection, query, *args, **kwargs)

        reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": reference,
                "account_currency_code": "KRW",
                "account_identifier": "acct-fk-path",
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        with mock.patch.object(psycopg.Connection, "execute", _raise_assignment_fk):
            with self.assertRaisesRegex(AccountingValidationError, "same accounting book"):
                accept_bank_account_assignment(
                    {
                        "tenant_reference": self.case.policy.tenant_reference,
                        "bank_account_reference": reference,
                        "legal_entity_reference": self.case.policy.legal_entity_reference,
                        "accounting_book_reference": self.case.policy.accounting_book_reference,
                        "chart_account_code": "110200",
                        "valid_from": "2026-04-01T00:00:00Z",
                    "assignment_idempotency_key": f"assign-x4-" + uuid.uuid4().hex,
                    },
                    posting.DATABASE_URL,
                    self.case.policy.tenant_reference,
                )

    def test_http_bank_statement_error_status_mapping(self) -> None:
        """Bank-account and statement HTTP surfaces map tenant, JSON, and scope errors."""
        server = self.case._start_http_server()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        tenant = self.case.policy.tenant_reference
        missing_header, _ = self.case._http_json(
            "POST", "/bank-accounts", {"tenant_reference": tenant}, tenant_header=None
        )
        self.assertEqual(missing_header, 400)
        bad_json, _ = self.case._http_raw("POST", "/bank-accounts", b"not-json", tenant)
        self.assertEqual(bad_json, 400)
        mismatch = {
            "tenant_reference": "urn:cwl:tenant_other",
            "bank_account_reference": f"urn:cwl:bank_account:{uuid.uuid4().hex}",
            "account_currency_code": "KRW",
            "account_identifier": "http-mismatch",
        }
        forbidden, _ = self.case._http_json("POST", "/bank-accounts", mismatch)
        self.assertEqual(forbidden, 403)
        conflict, conflict_body = self.case._http_json(
            "POST",
            "/bank-accounts",
            {
                "tenant_reference": tenant,
                "bank_account_reference": self.account_reference,
                "account_currency_code": "USD",
                "account_identifier": "acct-opaque-fixture-only",
            },
        )
        self.assertEqual(conflict, 409)
        self.assertIn("bank_account_reference", str(conflict_body).lower())
        invalid_account, _ = self.case._http_json(
            "POST",
            "/bank-accounts",
            {"tenant_reference": tenant, "bank_account_reference": "urn:cwl:bank_account:x"},
        )
        self.assertEqual(invalid_account, 422)
        missing_assign_header, _ = self.case._http_json(
            "POST",
            "/bank-account-assignments",
            {"tenant_reference": tenant},
            tenant_header=None,
        )
        self.assertEqual(missing_assign_header, 400)
        bad_assign_json, _ = self.case._http_raw(
            "POST", "/bank-account-assignments", b"not-json", tenant
        )
        self.assertEqual(bad_assign_json, 400)
        assign_forbidden, _ = self.case._http_json(
            "POST",
            "/bank-account-assignments",
            {
                "tenant_reference": "urn:cwl:tenant_other",
                "bank_account_reference": self.account_reference,
                "legal_entity_reference": self.case.policy.legal_entity_reference,
                "accounting_book_reference": self.case.policy.accounting_book_reference,
                "chart_account_code": "110200",
                "valid_from": "2026-01-01T00:00:00Z",
            
                "assignment_idempotency_key": "assign-http-3-" + uuid.uuid4().hex,},
        )
        self.assertEqual(assign_forbidden, 403)
        assign_invalid, assign_body = self.case._http_json(
            "POST",
            "/bank-account-assignments",
            {
                "tenant_reference": tenant,
                "bank_account_reference": self.account_reference,
                "legal_entity_reference": self.case.policy.legal_entity_reference,
                "accounting_book_reference": self.case.policy.accounting_book_reference,
                "valid_from": "2026-05-01T00:00:00Z",
            
                "assignment_idempotency_key": "assign-http-4-" + uuid.uuid4().hex,},
        )
        self.assertEqual(assign_invalid, 422)
        self.assertIn("chart_account_code", str(assign_body))
        assign_timestamp, assign_timestamp_body = self.case._http_json(
            "POST",
            "/bank-account-assignments",
            {
                "tenant_reference": tenant,
                "bank_account_reference": self.account_reference,
                "legal_entity_reference": self.case.policy.legal_entity_reference,
                "accounting_book_reference": self.case.policy.accounting_book_reference,
                "chart_account_code": "110200",
                "valid_from": "not-a-timestamp",
            
                "assignment_idempotency_key": "assign-http-5-" + uuid.uuid4().hex,},
        )
        self.assertEqual(assign_timestamp, 422)
        self.assertIn("timestamp", str(assign_timestamp_body).lower())
        bad_statement_json, _ = self.case._http_raw(
            "POST", "/bank-statements", b"not-json", tenant
        )
        self.assertEqual(bad_statement_json, 400)
        statement_forbidden, _ = self.case._http_json(
            "POST",
            "/bank-statements",
            {
                "tenant_reference": "urn:cwl:tenant_other",
                "bank_account_reference": self.account_reference,
                "ingestion_idempotency_key": "urn:cwl:bank_statement:http-mismatch",
                "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                "statement_payload": "<Document/>",
            },
        )
        self.assertEqual(statement_forbidden, 403)
        statement_invalid, _ = self.case._http_json(
            "POST",
            "/bank-statements",
            {
                "tenant_reference": tenant,
                "bank_account_reference": self.account_reference,
                "ingestion_idempotency_key": "urn:cwl:bank_statement:http-empty",
                "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                "statement_payload": "",
            },
        )
        self.assertEqual(statement_invalid, 422)
        missing_get, _ = self.case._http_json(
            "GET",
            f"/bank-statements?bank_account_reference={self.account_reference}",
            None,
            tenant_header=None,
        )
        self.assertEqual(missing_get, 400)
        unknown, unknown_body = self.case._http_json(
            "GET",
            f"/bank-statements?bank_statement_record_id={uuid.uuid4()}",
            None,
        )
        self.assertEqual(unknown, 404)
        self.assertIn("not recorded", str(unknown_body).lower())
        bad_cursor, bad_cursor_body = self.case._http_json(
            "GET",
            f"/bank-statements?bank_account_reference={self.account_reference}"
            f"&cursor={quote(f'not-a-time|{uuid.uuid4()}')}",
            None,
        )
        self.assertEqual(bad_cursor, 400)
        self.assertIn("cursor", str(bad_cursor_body).lower())
        zero_limit, _ = self.case._http_json(
            "GET",
            f"/bank-statements?bank_account_reference={self.account_reference}&page_limit=0",
            None,
        )
        self.assertEqual(zero_limit, 400)
        missing_entries_header, _ = self.case._http_json(
            "GET",
            f"/bank-statement-entries?bank_statement_record_id={uuid.uuid4()}",
            None,
            tenant_header=None,
        )
        self.assertEqual(missing_entries_header, 400)
        unknown_entries, _ = self.case._http_json(
            "GET",
            f"/bank-statement-entries?bank_statement_record_id={uuid.uuid4()}",
            None,
        )
        self.assertEqual(unknown_entries, 404)

    def _ingest(self, payload: bytes, *, key: str = "ingest-key") -> dict[str, object]:
        return accept_bank_statement_evidence(
            self._command(payload, key=key),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )

    def _command(self, payload: bytes, *, key: str) -> dict[str, object]:
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.account_reference,
            "ingestion_idempotency_key": f"urn:cwl:bank_statement:{key}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
            "source_artifact_hash": "sha256:" + hashlib.sha256(payload).hexdigest(),
        }

    def _count(self, table_name: str) -> int:
        return self.case._count_table(table_name)

    def _seed_second_book(self) -> tuple[object, object]:
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id, legal_entity_id = connection.execute(
                """
                SELECT tenant_account.tenant_account_id, legal_entity_record.legal_entity_id
                FROM accounting_core.tenant_account
                JOIN accounting_core.legal_entity_record
                  ON legal_entity_record.tenant_account_id = tenant_account.tenant_account_id
                WHERE tenant_account.tenant_account_code = %s
                """,
                (self.case.policy.tenant_reference,),
            ).fetchone()
            book_id = connection.execute(
                """
                INSERT INTO accounting_core.accounting_book (
                    tenant_account_id, legal_entity_id, book_role_code, book_name,
                    reporting_currency_code, valid_from
                )
                VALUES (%s, %s, 'management', %s, 'KRW', %s)
                RETURNING accounting_book_id
                """,
                (
                    tenant_id,
                    legal_entity_id,
                    f"urn:cwl:accounting_book:management_{uuid.uuid4().hex}",
                    VALID_FROM,
                ),
            ).fetchone()[0]
            chart_id = connection.execute(
                """
                INSERT INTO accounting_core.chart_account (
                    tenant_account_id, accounting_book_id, chart_account_code,
                    account_name, normal_balance_code, account_class_code, valid_from
                )
                VALUES (%s, %s, '119900', 'Other cash', 'debit', 'asset', %s)
                RETURNING chart_account_id
                """,
                (tenant_id, book_id, VALID_FROM),
            ).fetchone()[0]
            connection.commit()
        return book_id, chart_id

    def _seed_other_legal_entity_book(self) -> tuple[object, object, object]:
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = connection.execute(
                "SELECT tenant_account_id FROM accounting_core.tenant_account WHERE tenant_account_code = %s",
                (self.case.policy.tenant_reference,),
            ).fetchone()[0]
            other_legal_entity_id = connection.execute(
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
                    f"urn:cwl:legal_entity:other_{uuid.uuid4().hex}",
                    "Other statutory entity",
                    VALID_FROM,
                ),
            ).fetchone()[0]
            other_book_id = connection.execute(
                """
                INSERT INTO accounting_core.accounting_book (
                    tenant_account_id, legal_entity_id, book_role_code, book_name,
                    reporting_currency_code, valid_from
                )
                VALUES (%s, %s, 'statutory', %s, 'KRW', %s)
                RETURNING accounting_book_id
                """,
                (
                    tenant_id,
                    other_legal_entity_id,
                    f"urn:cwl:accounting_book:other_{uuid.uuid4().hex}",
                    VALID_FROM,
                ),
            ).fetchone()[0]
            other_chart_id = connection.execute(
                """
                INSERT INTO accounting_core.chart_account (
                    tenant_account_id, accounting_book_id, chart_account_code,
                    account_name, normal_balance_code, account_class_code, valid_from
                )
                VALUES (%s, %s, '110200', 'Other entity cash', 'debit', 'asset', %s)
                RETURNING chart_account_id
                """,
                (tenant_id, other_book_id, VALID_FROM),
            ).fetchone()[0]
            connection.commit()
        return other_legal_entity_id, other_book_id, other_chart_id


if __name__ == "__main__":
    unittest.main()
