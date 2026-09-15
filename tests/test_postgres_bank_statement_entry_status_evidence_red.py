"""PostgreSQL REDs for camt.053 entry-status provenance and schema admission."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
import uuid
from unittest.mock import patch

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
    lookup_bank_statement_entries,
    parse_bank_statement_payload,
)
from accounting_information_platform import bank_statement
from tests import test_postgres_posting as posting

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_ENTRY_STATUS_PURPOSE = "camt.053.001.14/Stmt/Ntry/Sts"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementEntryStatusEvidenceRedTests(unittest.TestCase):
    """Retain bank-reported entry status without turning it into posting authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare schema-shaped statements that isolate EntryStatus1Choice semantics."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        self.original_fixture = load_canonical_statement_fixture().decode("utf-8")
        self.first_marker = "        <RvslInd>false</RvslInd>\n        <BookgDt>"
        self.second_marker = "        <CdtDbtInd>DBIT</CdtDbtInd>\n        <BookgDt>"
        self.assertEqual(self.original_fixture.count(self.first_marker), 1)
        self.assertEqual(self.original_fixture.count(self.second_marker), 1)

        fixture = self._with_all_entry_statuses(self.original_fixture)
        self.book_code_payload = fixture.encode("utf-8")
        self.info_code_payload = self._replace_first_status(
            fixture, status_choice="Cd", status_value="INFO"
        )
        self.proprietary_book_payload = self._replace_first_status(
            fixture, status_choice="Prtry", status_value="BOOK"
        )
        self.proprietary_unknown_payload = self._replace_first_status(
            fixture, status_choice="Prtry", status_value="ZZZZ"
        )

        formatting_anchor = (
            "        <Sts>\n"
            "          <Cd>BOOK</Cd>\n"
            "        </Sts>\n"
        ).encode("utf-8")
        self.assertEqual(self.book_code_payload.count(formatting_anchor), 2)
        self.reformatted_payload = self.book_code_payload.replace(
            formatting_anchor,
            b"        <Sts><Cd>BOOK</Cd></Sts>\n",
            1,
        )

        self.book_code_statement = parse_bank_statement_payload(
            self.book_code_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.info_code_statement = parse_bank_statement_payload(
            self.info_code_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.proprietary_book_statement = parse_bank_statement_payload(
            self.proprietary_book_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.proprietary_unknown_statement = parse_bank_statement_payload(
            self.proprietary_unknown_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.reformatted_statement = parse_bank_statement_payload(
            self.reformatted_payload, CAMT053_MESSAGE_DEFINITION
        )

        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.book_code_statement.account_currency_code,
                "account_identifier_hash": self.book_code_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_canonical_fixture_supplies_status_for_every_entry(self) -> None:
        """The repository's V14 canonical fixture must not omit mandatory Ntry/Sts."""
        self.assertEqual(
            self.original_fixture.count("      <Ntry>"),
            self.original_fixture.count("        <Sts>"),
            "canonical camt.053.001.14 fixture must carry Sts for every Ntry",
        )

    def test_entry_status_value_and_choice_are_material_entry_evidence(self) -> None:
        """Status value and EntryStatus1Choice discriminator are independently material."""
        variants = (
            (self.book_code_statement, self._expected_status_hash("Cd", "BOOK")),
            (self.info_code_statement, self._expected_status_hash("Cd", "INFO")),
            (
                self.proprietary_book_statement,
                self._expected_status_hash("Prtry", "BOOK"),
            ),
            (
                self.proprietary_unknown_statement,
                self._expected_status_hash("Prtry", "ZZZZ"),
            ),
        )

        expected_hashes: list[str] = []
        for statement, expected_hash in variants:
            with self.subTest(expected_hash=expected_hash):
                entry = statement.entries[0]
                self.assertRegex(expected_hash, _HASH_PATTERN)
                self.assertEqual(
                    getattr(entry, "entry_status_evidence_hash", None),
                    expected_hash,
                )
                self._assert_entry_hash_binding(entry, expected_hash)
                expected_hashes.append(expected_hash)

        self.assertEqual(len(set(expected_hashes)), len(expected_hashes))
        for changed in (
            self.info_code_statement,
            self.proprietary_book_statement,
            self.proprietary_unknown_statement,
        ):
            self.assertEqual(
                self.book_code_statement.account_identifier_hash,
                changed.account_identifier_hash,
            )
            self.assertNotEqual(
                self.book_code_statement.entries[0].source_entry_hash,
                changed.entries[0].source_entry_hash,
            )
            self.assertNotEqual(
                self.book_code_statement.normalized_payload_hash,
                changed.normalized_payload_hash,
            )
            self.assertEqual(
                self.book_code_statement.entries[1].source_entry_hash,
                changed.entries[1].source_entry_hash,
            )

    def test_code_choice_routes_pinned_external_code_evidence_before_normalization(self) -> None:
        """Sts/Cd must use the versioned ISO external-code artifact before normalization."""
        fixture = self._with_all_entry_statuses(self.original_fixture)
        hostile = self._replace_first_status(
            fixture, status_choice="Cd", status_value="ZZZZ"
        )
        schema_artifact = self._artifact(
            "message_schema",
            "iso20022/fixtures/camt.053.001.14.xsd",
            "a",
            "camt.053.001.14",
        )
        external_code_artifact = self._artifact(
            "iso20022_external_code_sets",
            "iso20022/fixtures/iso20022-external-code-sets.json",
            "b",
            "August 2026 (v3)",
        )
        transaction_code_artifact = self._artifact(
            "bank_transaction_code_combinations",
            "iso20022/fixtures/bank-transaction-code-combinations.xlsx",
            "c",
            "30 November 2025 (v1)",
        )
        controlled_manifest = {
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "artifacts": [
                schema_artifact,
                external_code_artifact,
                transaction_code_artifact,
            ],
        }
        schema_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        semantic_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def accept_schema(*args: object, **kwargs: object) -> None:
            self.assertTrue(any(value == hostile for value in (*args, *kwargs.values())))
            schema_calls.append((args, kwargs))

        def reject_unknown_status_code(*args: object, **kwargs: object) -> None:
            rendered_contract = repr((args, kwargs))
            self.assertIn(external_code_artifact["local_package_path"], rendered_contract)
            self.assertIn(external_code_artifact["sha256"], rendered_contract)
            self.assertIn(external_code_artifact["source_version"], rendered_contract)
            self.assertTrue(any(value == hostile for value in (*args, *kwargs.values())))
            self.assertIn(b"<Sts>", hostile)
            self.assertIn(b"<Cd>ZZZZ</Cd>", hostile)
            self.assertEqual(len(schema_calls), 1)
            semantic_calls.append((args, kwargs))
            raise AccountingValidationError("unknown-entry-status-sentinel")

        with (
            patch.object(
                bank_statement,
                "load_adapter_manifest",
                return_value=controlled_manifest,
            ),
            patch.object(
                bank_statement,
                "_validate_message_schema",
                side_effect=accept_schema,
                create=True,
            ),
            patch.object(
                bank_statement,
                "_validate_external_code_evidence",
                side_effect=reject_unknown_status_code,
                create=True,
            ),
            patch.object(
                bank_statement,
                "_normalize_statement",
                side_effect=AssertionError(
                    "normalization ran before entry-status external-code admission"
                ),
            ),
        ):
            with self.assertRaisesRegex(
                AccountingValidationError,
                "unknown-entry-status-sentinel",
            ):
                bank_statement.parse_bank_statement_payload(
                    hostile,
                    CAMT053_MESSAGE_DEFINITION,
                )

        self.assertEqual(len(schema_calls), 1)
        self.assertEqual(len(semantic_calls), 1)

    def test_proprietary_status_is_not_forced_through_external_code_membership(self) -> None:
        """A proprietary status remains distinct from the external-code Cd branch."""
        expected_hash = self._expected_status_hash("Prtry", "ZZZZ")
        parsed_entry = self.proprietary_unknown_statement.entries[0]
        self.assertEqual(
            getattr(parsed_entry, "entry_status_evidence_hash", None),
            expected_hash,
        )
        self._assert_entry_hash_binding(parsed_entry, expected_hash)

        accepted = accept_bank_statement_evidence(
            self._command(self.proprietary_unknown_payload, "proprietary-unknown"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        entry = document["bank_statement_entries"][0]
        self.assertEqual(entry.get("entry_status_evidence_hash"), expected_hash)
        self.assertEqual(
            entry.get("status_evidence"),
            {"status_choice": "Prtry", "status_value": "ZZZZ"},
        )

    def test_xml_layout_does_not_change_entry_status_semantics(self) -> None:
        """Element layout differences must not alter normalized status evidence."""
        expected_hash = self._expected_status_hash("Cd", "BOOK")
        base_entry = self.book_code_statement.entries[0]
        reformatted_entry = self.reformatted_statement.entries[0]

        self.assertNotEqual(
            self.book_code_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(base_entry, "entry_status_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(reformatted_entry, "entry_status_evidence_hash", None),
            expected_hash,
        )
        self._assert_entry_hash_binding(base_entry, expected_hash)
        self._assert_entry_hash_binding(reformatted_entry, expected_hash)
        self.assertEqual(base_entry.source_entry_hash, reformatted_entry.source_entry_hash)
        self.assertEqual(
            self.book_code_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_every_material_status_change_requires_explicit_statement_correction(self) -> None:
        """Code value and choice changes cannot silently replay one statement identity."""
        accepted = accept_bank_statement_evidence(
            self._command(self.book_code_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        for suffix, changed_payload in (
            ("info-code", self.info_code_payload),
            ("proprietary-book", self.proprietary_book_payload),
            ("proprietary-unknown", self.proprietary_unknown_payload),
        ):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                    accept_bank_statement_evidence(
                        self._command(changed_payload, suffix),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_buyer_read_preserves_exact_entry_status_provenance(self) -> None:
        """Supported reads retain choice, value, and purpose digest without policy inference."""
        expected_hash = self._expected_status_hash("Cd", "BOOK")
        accepted = accept_bank_statement_evidence(
            self._command(self.book_code_payload, "lookup"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        entry = document["bank_statement_entries"][0]

        self.assertEqual(entry.get("entry_status_evidence_hash"), expected_hash)
        self.assertEqual(
            entry.get("status_evidence"),
            {"status_choice": "Cd", "status_value": "BOOK"},
        )

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Bind canonical entry identity to status semantics, never raw XML layout."""
        projection = dict(bank_statement._entry_payload(entry))
        if projection.get("entry_status_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry the exact entry_status_evidence_hash"
            )
        if "source_artifact_hash" in projection:
            raise AssertionError("canonical entry projection must not use raw artifact identity")
        preimage = json.dumps(
            projection,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if entry.source_entry_hash != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must be the digest of the canonical entry projection"
            )

    @staticmethod
    def _expected_status_hash(status_choice: str, status_value: str) -> str:
        """Digest one EntryStatus1Choice semantic value."""
        preimage = json.dumps(
            {
                "evidence_type": _ENTRY_STATUS_PURPOSE,
                "status_choice": status_choice,
                "status_value": status_value,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _artifact(
        role: str,
        path: str,
        digest_character: str,
        source_version: str,
    ) -> dict[str, object]:
        """Build one controlled manifest artifact for admission-order assertions."""
        return {
            "local_package_path": path,
            "artifact_role": role,
            "sha256": digest_character * 64,
            "byte_length": 123,
            "source_version": source_version,
        }

    def _with_all_entry_statuses(self, fixture: str) -> str:
        """Supply a coded BOOK status for both canonical entries before focused mutation."""
        first = fixture.replace(
            self.first_marker,
            "        <RvslInd>false</RvslInd>\n"
            "        <Sts>\n"
            "          <Cd>BOOK</Cd>\n"
            "        </Sts>\n"
            "        <BookgDt>",
            1,
        )
        return first.replace(
            self.second_marker,
            "        <CdtDbtInd>DBIT</CdtDbtInd>\n"
            "        <Sts>\n"
            "          <Cd>BOOK</Cd>\n"
            "        </Sts>\n"
            "        <BookgDt>",
            1,
        )

    @staticmethod
    def _replace_first_status(
        fixture: str,
        *,
        status_choice: str,
        status_value: str,
    ) -> bytes:
        """Replace only the first entry's EntryStatus1Choice."""
        marker = (
            "        <Sts>\n"
            "          <Cd>BOOK</Cd>\n"
            "        </Sts>\n"
        )
        replacement = (
            "        <Sts>\n"
            f"          <{status_choice}>{status_value}</{status_choice}>\n"
            "        </Sts>\n"
        )
        if fixture.count(marker) != 2:
            raise AssertionError("schema-shaped fixture must carry BOOK status on both entries")
        return fixture.replace(marker, replacement, 1).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Build a supported ingest command with an independent idempotency key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"entry-status-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
