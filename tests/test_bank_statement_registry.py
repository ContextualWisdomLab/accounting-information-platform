"""PostgreSQL acceptance tests for the immutable bank-statement evidence registry."""

from __future__ import annotations

import hashlib
import unittest
import uuid
from datetime import datetime, timezone

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
                        accounting_book_id, chart_account_id, valid_from
                    )
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        tenant_id,
                        account_id,
                        legal_entity_id,
                        book_id,
                        other_chart_id,
                        VALID_FROM,
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
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
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
            },
        )
        self.assertEqual(assign_status, 200)
        self.assertEqual(assignment["chart_account_code"], "110200")

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


if __name__ == "__main__":
    unittest.main()
