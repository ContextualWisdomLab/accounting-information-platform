"""RED/GREEN parser and adapter-evidence contracts for camt.053.001.14."""

from __future__ import annotations

import contextlib
import hashlib
import json
import unittest
import uuid
from decimal import Decimal
from pathlib import Path
from unittest import mock

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    load_adapter_manifest,
    load_canonical_statement_fixture,
    parse_bank_statement_payload,
)
from accounting_information_platform.http_api import _bank_statement_status
from accounting_information_platform.bank_statement import (
    MAX_ATTRIBUTE_COUNT,
    MAX_ELEMENT_COUNT,
    MAX_ENTRY_COUNT,
    MAX_TEXT_BYTES,
    MAX_XML_DEPTH,
    accept_bank_account_assignment,
    _XmlElement,
    _account_identifier_hash,
    _decimal_text,
    _entry_cursor,
    _find_path,
    _is_foreign_key_error,
    _normalize_detail,
    _page_limit,
    _parse_bounded_xml,
    _parse_reversal,
    _parse_timestamp,
    _require_uuid,
    _required_child,
    _required_text,
    _split_expat_name,
    _statement_cursor,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    ROOT
    / "src"
    / "accounting_information_platform"
    / "iso20022"
    / "fixtures"
    / "camt.053.001.14.valid.xml"
)


class BankStatementAdapterTests(unittest.TestCase):
    """Pin revision dispatch, parser security, and exact monetary normalize."""

    def test_canonical_fixture_normalizes_exact_entries(self) -> None:
        """The pinned camt.053.001.14 fixture yields one statement and two entries."""
        payload = load_canonical_statement_fixture()
        statement = parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
        self.assertEqual(statement.message_definition_identifier, CAMT053_MESSAGE_DEFINITION)
        self.assertEqual(statement.statement_identity_reference, "BANK-STMT-2026-08-24")
        self.assertEqual(statement.electronic_sequence_number, "42")
        self.assertEqual(len(statement.entries), 2)
        credit, debit = statement.entries
        self.assertEqual(credit.credit_debit_code, "CRDT")
        self.assertEqual(credit.entry_amount, Decimal("25000.00"))
        self.assertEqual(credit.entry_currency_code, "KRW")
        self.assertEqual(credit.end_to_end_reference, "E2E-1")
        self.assertEqual(credit.source_locator_path, "Document/BkToCstmrStmt/Stmt/Ntry[1]")
        self.assertEqual(len(debit.entry_details), 2)
        self.assertEqual(debit.credit_debit_code, "DBIT")
        self.assertEqual(debit.entry_amount, Decimal("10000.00"))
        self.assertEqual(debit.entry_details[0].detail_amount, Decimal("6000.00"))
        self.assertEqual(debit.entry_details[1].detail_amount, Decimal("4000.00"))
        self.assertTrue(credit.source_entry_hash.startswith("sha256:"))
        self.assertTrue(statement.source_artifact_hash.startswith("sha256:"))

    def test_remittance_and_counterparty_are_material_entry_evidence(self) -> None:
        """Same statement identity with changed remittance or counterparty is not an exact replay."""
        original = parse_bank_statement_payload(
            load_canonical_statement_fixture(),
            CAMT053_MESSAGE_DEFINITION,
        )
        remittance = parse_bank_statement_payload(
            FIXTURE.read_bytes().replace(b"Invoice 1001", b"Invoice 1999", 1),
            CAMT053_MESSAGE_DEFINITION,
        )
        counterparty = parse_bank_statement_payload(
            FIXTURE.read_bytes().replace(b"Counterparty One", b"Counterparty Two", 1),
            CAMT053_MESSAGE_DEFINITION,
        )
        self.assertNotEqual(original.normalized_payload_hash, remittance.normalized_payload_hash)
        self.assertNotEqual(original.normalized_payload_hash, counterparty.normalized_payload_hash)
        self.assertNotEqual(
            original.entries[0].source_entry_hash,
            remittance.entries[0].source_entry_hash,
        )

    def test_transaction_codes_are_material_entry_evidence(self) -> None:
        """Changed BkTxCd domain evidence is not an exact entry replay."""
        original = parse_bank_statement_payload(
            load_canonical_statement_fixture(),
            CAMT053_MESSAGE_DEFINITION,
        )
        changed = parse_bank_statement_payload(
            FIXTURE.read_bytes().replace(b"<Cd>PMNT</Cd>", b"<Cd>ACMT</Cd>", 1),
            CAMT053_MESSAGE_DEFINITION,
        )
        self.assertEqual(
            original.entries[0].bank_transaction_domain_code,
            "PMNT",
        )
        self.assertEqual(changed.entries[0].bank_transaction_domain_code, "ACMT")
        self.assertNotEqual(
            original.entries[0].source_entry_hash,
            changed.entries[0].source_entry_hash,
        )

    def test_counterparty_evidence_follows_entry_direction(self) -> None:
        """CRDT records the payer (Dbtr); DBIT records the payee (Cdtr)."""
        fixture_text = FIXTURE.read_text(encoding="utf-8")
        both_parties = (
            "<RltdPties>\n"
            "              <Dbtr>\n"
            "                <Pty>\n"
            "                  <Nm>Payer Alpha</Nm>\n"
            "                </Pty>\n"
            "              </Dbtr>\n"
            "              <Cdtr>\n"
            "                <Pty>\n"
            "                  <Nm>Payee Beta</Nm>\n"
            "                </Pty>\n"
            "              </Cdtr>\n"
            "            </RltdPties>"
        )
        credit_payload = fixture_text.replace(
            "<RltdPties>\n              <Dbtr>\n                <Pty>\n"
            "                  <Nm>Counterparty One</Nm>\n"
            "                </Pty>\n              </Dbtr>\n            </RltdPties>",
            both_parties,
            1,
        ).encode("utf-8")
        debit_payload = fixture_text.replace(
            "<TxDtls>\n            <Refs>\n              <EndToEndId>E2E-2A</EndToEndId>",
            "<TxDtls>\n            "
            + both_parties.replace("\n", "\n            ")
            + "\n            <Refs>\n              <EndToEndId>E2E-2A</EndToEndId>",
            1,
        ).encode("utf-8")
        statement = parse_bank_statement_payload(credit_payload, CAMT053_MESSAGE_DEFINITION)
        self.assertEqual(
            statement.entries[0].counterparty_evidence_hash,
            "sha256:" + hashlib.sha256(b"Payer Alpha").hexdigest(),
        )
        debit_statement = parse_bank_statement_payload(debit_payload, CAMT053_MESSAGE_DEFINITION)
        self.assertEqual(debit_statement.entries[1].credit_debit_code, "DBIT")
        self.assertEqual(
            debit_statement.entries[1].counterparty_evidence_hash,
            "sha256:" + hashlib.sha256(b"Payee Beta").hexdigest(),
        )

    def test_detail_records_txamt_when_instdamt_precedes_it(self) -> None:
        """TxDtls/AmtDtls/TxAmt/Amt is the stored detail amount, not an earlier InstdAmt."""
        payload = FIXTURE.read_text(encoding="utf-8").replace(
            "<AmtDtls>\n              <TxAmt>\n                <Amt Ccy=\"KRW\">25000.00</Amt>\n              </TxAmt>\n            </AmtDtls>",
            "<AmtDtls>\n              <InstdAmt>\n                <Amt Ccy=\"KRW\">99999.00</Amt>\n              </InstdAmt>\n              <TxAmt>\n                <Amt Ccy=\"KRW\">25000.00</Amt>\n              </TxAmt>\n            </AmtDtls>",
            1,
        ).encode("utf-8")
        statement = parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
        self.assertEqual(statement.entries[0].entry_details[0].detail_amount, Decimal("25000.00"))

    def test_detail_without_txamt_fails_closed(self) -> None:
        """A detail that has only InstdAmt is not recorded as transaction evidence."""
        payload = FIXTURE.read_text(encoding="utf-8").replace(
            "<AmtDtls>\n              <TxAmt>\n                <Amt Ccy=\"KRW\">25000.00</Amt>\n              </TxAmt>\n            </AmtDtls>",
            "<AmtDtls>\n              <InstdAmt>\n                <Amt Ccy=\"KRW\">99999.00</Amt>\n              </InstdAmt>\n            </AmtDtls>",
            1,
        ).encode("utf-8")
        with self.assertRaisesRegex(AccountingValidationError, "TxDtls/AmtDtls/TxAmt/Amt"):
            parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    def test_detail_credit_debit_indicator_must_be_crdt_or_dbit(self) -> None:
        """A malformed TxDtls CdtDbtInd fails closed before persist, same as Ntry."""
        payload = FIXTURE.read_text(encoding="utf-8").replace(
            "<TxDtls>\n            <Refs>\n              <EndToEndId>E2E-1</EndToEndId>",
            "<TxDtls>\n            <CdtDbtInd>FOOO</CdtDbtInd>\n            <Refs>\n              <EndToEndId>E2E-1</EndToEndId>",
            1,
        ).encode("utf-8")
        with self.assertRaisesRegex(AccountingValidationError, "CRDT or DBIT"):
            parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    def test_revision_mismatch_is_rejected(self) -> None:
        """Another camt.053 revision is not coerced into the pinned adapter."""
        payload = FIXTURE.read_text(encoding="utf-8").replace(
            "camt.053.001.14", "camt.053.001.13"
        ).encode("utf-8")
        with self.assertRaisesRegex(AccountingValidationError, "revision"):
            parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
        with self.assertRaisesRegex(AccountingValidationError, "camt.053.001.14"):
            parse_bank_statement_payload(FIXTURE.read_bytes(), "camt.053.001.13")

    def test_dtd_and_external_entity_perform_no_io(self) -> None:
        """DTD or external-entity input fails closed without opening a URL or file."""
        payload = (
            b'<?xml version="1.0"?>\n'
            b'<!DOCTYPE Document [\n'
            b'  <!ENTITY xxe SYSTEM "http://127.0.0.1:9/secret">\n'
            b"]>\n"
            b'<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.14">'
            b"&xxe;</Document>"
        )
        with mock.patch("urllib.request.urlopen") as urlopen:
            with self.assertRaisesRegex(AccountingValidationError, "DTD|entity"):
                parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
        urlopen.assert_not_called()

    def test_processing_instruction_and_empty_payload_fail_closed(self) -> None:
        """Stylesheet PI, empty bytes, and oversized payloads never parse."""
        with self.assertRaisesRegex(AccountingValidationError, "empty"):
            parse_bank_statement_payload(b"", CAMT053_MESSAGE_DEFINITION)
        with self.assertRaisesRegex(AccountingValidationError, "1 MiB"):
            parse_bank_statement_payload(b"<x/>" + b"a" * 1_048_577, CAMT053_MESSAGE_DEFINITION)
        payload = (
            b'<?xml version="1.0"?>\n'
            b'<?xml-stylesheet type="text/xsl" href="http://127.0.0.1:9/x.xsl"?>\n'
            b'<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.14"/>'
        )
        with self.assertRaisesRegex(AccountingValidationError, "stylesheet|processing"):
            parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    def test_excessive_depth_elements_text_and_entries_fail_before_normalize(self) -> None:
        """Bound violations fail closed before a normalized population exists."""
        deep = "<a>" * (MAX_XML_DEPTH + 1) + "</a>" * (MAX_XML_DEPTH + 1)
        with self.assertRaisesRegex(AccountingValidationError, "depth"):
            parse_bank_statement_payload(
                f'<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.14">{deep}</Document>'.encode(
                    "utf-8"
                ),
                CAMT053_MESSAGE_DEFINITION,
            )
        huge_text = "x" * (MAX_TEXT_BYTES + 1)
        with self.assertRaisesRegex(AccountingValidationError, "text length"):
            parse_bank_statement_payload(
                (
                    '<Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.14">'
                    f"<BkToCstmrStmt><x>{huge_text}</x></BkToCstmrStmt></Document>"
                ).encode("utf-8"),
                CAMT053_MESSAGE_DEFINITION,
            )
        entries = "".join(
            (
                "<Ntry><NtryRef>X</NtryRef><Amt Ccy=\"KRW\">1.00</Amt>"
                "<CdtDbtInd>CRDT</CdtDbtInd></Ntry>"
            )
            for _ in range(MAX_ENTRY_COUNT + 1)
        )
        bloated = FIXTURE.read_text(encoding="utf-8").replace(
            "</Stmt>",
            f"{entries}</Stmt>",
            1,
        )
        with self.assertRaisesRegex(AccountingValidationError, "entry count"):
            parse_bank_statement_payload(bloated.encode("utf-8"), CAMT053_MESSAGE_DEFINITION)
        with mock.patch(
            "accounting_information_platform.bank_statement.MAX_ELEMENT_COUNT", 3
        ):
            with self.assertRaisesRegex(AccountingValidationError, "element count"):
                parse_bank_statement_payload(
                    FIXTURE.read_bytes(), CAMT053_MESSAGE_DEFINITION
                )
        with mock.patch(
            "accounting_information_platform.bank_statement.MAX_ATTRIBUTE_COUNT", 0
        ):
            with self.assertRaisesRegex(AccountingValidationError, "attribute count"):
                parse_bank_statement_payload(
                    FIXTURE.read_bytes(), CAMT053_MESSAGE_DEFINITION
                )

    def test_malformed_decimal_and_currency_fail_closed(self) -> None:
        """Scientific notation, extra scale, and bad currency never coerce."""
        text = FIXTURE.read_text(encoding="utf-8")
        with self.assertRaisesRegex(AccountingValidationError, "exact decimal"):
            parse_bank_statement_payload(
                text.replace("25000.00", "1e4", 1).encode("utf-8"),
                CAMT053_MESSAGE_DEFINITION,
            )
        with self.assertRaisesRegex(AccountingValidationError, "exact decimal"):
            parse_bank_statement_payload(
                text.replace("25000.00", "25000.0000001", 1).encode("utf-8"),
                CAMT053_MESSAGE_DEFINITION,
            )
        with self.assertRaisesRegex(AccountingValidationError, "currency"):
            parse_bank_statement_payload(
                text.replace('Ccy="KRW"', 'Ccy="krw"', 1).encode("utf-8"),
                CAMT053_MESSAGE_DEFINITION,
            )

    def test_malformed_xml_and_two_statements_fail_closed(self) -> None:
        """Well-formedness and the one-statement adapter bound fail closed."""
        with self.assertRaisesRegex(AccountingValidationError, "well formed"):
            parse_bank_statement_payload(b"<Document>", CAMT053_MESSAGE_DEFINITION)
        extra = FIXTURE.read_text(encoding="utf-8").replace(
            "</BkToCstmrStmt>",
            "<Stmt><Id>OTHER</Id><Acct><Ccy>KRW</Ccy></Acct></Stmt></BkToCstmrStmt>",
            1,
        )
        with self.assertRaisesRegex(AccountingValidationError, "exactly one Stmt"):
            parse_bank_statement_payload(extra.encode("utf-8"), CAMT053_MESSAGE_DEFINITION)
        instruction = (
            b'<?xml version="1.0"?><?custom-pi data?><Document '
            b'xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.14"/>'
        )
        with self.assertRaisesRegex(AccountingValidationError, "processing"):
            parse_bank_statement_payload(instruction, CAMT053_MESSAGE_DEFINITION)
        no_entry = FIXTURE.read_text(encoding="utf-8")
        no_entry = no_entry.split("<Ntry>", 1)[0] + "</Stmt></BkToCstmrStmt></Document>"
        with self.assertRaisesRegex(AccountingValidationError, "no Ntry"):
            parse_bank_statement_payload(no_entry.encode("utf-8"), CAMT053_MESSAGE_DEFINITION)
        with self.assertRaisesRegex(AccountingValidationError, "reversal"):
            parse_bank_statement_payload(
                FIXTURE.read_text(encoding="utf-8")
                .replace("<RvslInd>false</RvslInd>", "<RvslInd>maybe</RvslInd>")
                .encode("utf-8"),
                CAMT053_MESSAGE_DEFINITION,
            )

    def test_adapter_manifest_pins_sha256_and_rejects_tamper(self) -> None:
        """The adapter manifest is executable provenance, not a remembered revision."""
        manifest = load_adapter_manifest()
        self.assertEqual(manifest["message_definition_identifier"], CAMT053_MESSAGE_DEFINITION)
        fixture = FIXTURE.read_bytes()
        self.assertEqual(
            hashlib.sha256(fixture).hexdigest(),
            "6c09078392519cbf035a499e9ef23fddd74876384aa3619e0e62d754e4959b60",
        )
        notice = (
            ROOT / "src" / "accounting_information_platform" / "iso20022" / "NOTICE"
        ).read_text(encoding="utf-8")
        self.assertIn("https://www.iso20022.org", notice)
        self.assertIn("not the official or current source", notice)
        with mock.patch(
            "accounting_information_platform.bank_statement.Path.read_bytes",
            return_value=b"tampered",
        ):
            with self.assertRaisesRegex(AccountingValidationError, "SHA-256"):
                load_adapter_manifest()

    def test_memory_artifact_store_is_hash_addressed(self) -> None:
        """Host evidence store retains original bytes by digest and rejects hash collision."""
        store = MemoryArtifactStore()
        digest = "sha256:" + hashlib.sha256(b"one").hexdigest()
        locator = store.put_artifact(digest, b"one")
        self.assertEqual(store.get_artifact(locator), b"one")
        self.assertEqual(store.put_artifact(digest, b"one"), locator)
        with self.assertRaisesRegex(AccountingValidationError, "different bytes"):
            store.put_artifact(digest, b"two")
        with self.assertRaisesRegex(AccountingValidationError, "memory locator"):
            store.get_artifact("s3://missing")
        with self.assertRaisesRegex(AccountingValidationError, "not retained"):
            store.get_artifact("memory:sha256:" + "0" * 64)

    def test_iban_account_and_reversal_indicator_normalize(self) -> None:
        """IBAN account identity and RvslInd=true survive the exact normalize path."""
        xml = FIXTURE.read_text(encoding="utf-8")
        xml = xml.replace(
            "<Othr>\n            <Id>acct-opaque-fixture-only</Id>\n          </Othr>",
            "<IBAN>GB82WEST12345698765432</IBAN>",
        ).replace("<RvslInd>false</RvslInd>", "<RvslInd>true</RvslInd>")
        statement = parse_bank_statement_payload(xml.encode("utf-8"), CAMT053_MESSAGE_DEFINITION)
        self.assertTrue(statement.entries[0].reversal_indicator)
        self.assertTrue(statement.account_identifier_hash.startswith("sha256:"))

    def test_missing_amount_and_zero_amount_fail_closed(self) -> None:
        """Entry amounts must be present and greater than zero."""
        text = FIXTURE.read_text(encoding="utf-8")
        with self.assertRaisesRegex(AccountingValidationError, "greater than zero"):
            parse_bank_statement_payload(
                text.replace("25000.00", "0", 1).encode("utf-8"),
                CAMT053_MESSAGE_DEFINITION,
            )
        with self.assertRaisesRegex(AccountingValidationError, "missing"):
            parse_bank_statement_payload(
                text.replace("<Amt Ccy=\"KRW\">25000.00</Amt>", "", 1).encode("utf-8"),
                CAMT053_MESSAGE_DEFINITION,
            )

    def test_zero_opening_balance_is_accepted(self) -> None:
        """camt.053 OPBD/CLBD may be zero; only Ntry/TxAmt amounts must be positive."""
        payload = FIXTURE.read_text(encoding="utf-8").replace(
            '<Amt Ccy="KRW">100000.00</Amt>',
            '<Amt Ccy="KRW">0.00</Amt>',
            1,
        ).encode("utf-8")
        statement = parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
        self.assertEqual(statement.statement_identity_reference, "BANK-STMT-2026-08-24")
        self.assertIsNotNone(statement.opening_balance_hash)

    def test_helper_boundaries_fail_closed(self) -> None:
        """List cursors, hashes, and foreign-key detection stay fail-closed."""
        self.assertEqual(_page_limit(None), 50)
        self.assertEqual(_page_limit(10), 10)
        with self.assertRaisesRegex(AccountingValidationError, "page_limit"):
            _page_limit(101)
        self.assertEqual(_entry_cursor(None), 0)
        self.assertEqual(_entry_cursor("3"), 3)
        with self.assertRaisesRegex(AccountingValidationError, "cursor"):
            _entry_cursor("-1")
        _require_uuid("6ba7b810-9dad-11d1-80b4-00c04fd430c8", "bank_statement_record_id")
        with self.assertRaisesRegex(AccountingValidationError, "UUID"):
            _require_uuid("nope", "bank_statement_record_id")
        self.assertEqual(_split_expat_name("Document"), ("", "Document"))
        self.assertFalse(_parse_reversal("0"))
        self.assertTrue(_parse_reversal("1"))
        digest = _account_identifier_hash(
            {"account_identifier_hash": "sha256:" + "ab" * 32}
        )
        self.assertTrue(digest.startswith("sha256:"))
        with self.assertRaisesRegex(AccountingValidationError, "account_identifier_hash"):
            _account_identifier_hash({"account_identifier_hash": "bad"})
        with self.assertRaisesRegex(AccountingValidationError, "account_identifier_hash is required"):
            _account_identifier_hash({})
        error = Exception("fk")
        error.sqlstate = "23503"
        self.assertTrue(_is_foreign_key_error(error))
        wrapped = Exception("wrap")
        wrapped.__cause__ = error
        self.assertTrue(_is_foreign_key_error(wrapped))
        self.assertFalse(_is_foreign_key_error(Exception("other")))
        self.assertEqual(_decimal_text(Decimal("25000")), "25000")
        self.assertEqual(_decimal_text(1), "1")
        parsed_cursor = _statement_cursor(
            "2026-08-24T00:00:00+00:00|6ba7b810-9dad-11d1-80b4-00c04fd430c8"
        )
        self.assertEqual(str(parsed_cursor[1]), "6ba7b810-9dad-11d1-80b4-00c04fd430c8")
        with self.assertRaisesRegex(AccountingValidationError, "ISO-8601"):
            _parse_timestamp("not-a-timestamp", "cursor")
        node = _XmlElement("Document", "", {}, "", [])
        self.assertIs(_find_path(node, []), node)
        self.assertIsNone(_find_path(node, ["Document", "Missing"]))
        amount = _XmlElement("Amt", "", {"Ccy": "KRW"}, "100.00", [])
        tx_amount = _XmlElement("TxAmt", "", {}, "", [amount])
        amount_details = _XmlElement("AmtDtls", "", {}, "", [tx_amount])
        detail = _normalize_detail(
            _XmlElement("TxDtls", "", {}, "", [amount_details]),
            1,
            1,
            "CRDT",
        )
        self.assertEqual(detail.detail_amount, Decimal("100.00"))
        empty_statement = _XmlElement("Stmt", "", {}, "", [])
        with self.assertRaisesRegex(AccountingValidationError, "statement account"):
            _required_child(empty_statement, "Acct", "statement account")
        with self.assertRaisesRegex(AccountingValidationError, "statement identity"):
            _required_text(empty_statement, ("Id",), "statement identity")

    def test_ingest_serializes_same_artifact_bytes_across_keys(self) -> None:
        """Same statement bytes under different keys share one advisory lock."""
        source = (
            ROOT
            / "src"
            / "accounting_information_platform"
            / "bank_statement.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            'ledger._acquire_command_lock(connection, f"bank_statement:{idempotency_key}")',
            source,
        )
        self.assertIn(
            'ledger._acquire_command_lock(connection, f"bank_statement_hash:{source_hash}")',
            source,
        )
        self.assertIn(
            'f"bank_statement_identity:{account_row[0]}:{statement.statement_identity_reference}"',
            source,
        )

    def test_assignment_reraises_non_foreign_key_insert_errors(self) -> None:
        """A non-FK assignment INSERT failure is not rewritten as a book-scope miss."""

        class _Result:
            def __init__(self, rows: tuple[object, ...]) -> None:
                self._rows = rows

            def fetchone(self) -> tuple[object, ...] | None:
                return self._rows or None

        class _Connection:
            def execute(self, sql: object, _params: object = None) -> "_Result":
                query = sql if isinstance(sql, str) else str(sql)
                if "INSERT INTO accounting_core.bank_account_assignment" in query:
                    raise RuntimeError("disk full")
                if (
                    "SELECT" in query
                    and "assignment_idempotency_key" in query
                    and "INSERT" not in query
                ):
                    # No stored command identity resolves in this probe; only
                    # the INSERT failure is under test.
                    return _Result(())
                return _Result((uuid.uuid4(),))

        class _Ledger:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                return None

            def _session(self) -> contextlib.AbstractContextManager[object]:
                return contextlib.nullcontext(_Connection())

            def _require_tenant(self, _connection: object) -> object:
                return uuid.uuid4()

            def _acquire_command_lock(self, _connection: object, _scope: str) -> None:
                return None

            def _load_legal_entity(
                self,
                _connection: object,
                _tenant_id: object,
                _reference: str,
                _label: str,
            ) -> tuple[object, str]:
                return uuid.uuid4(), "KRW"

        tenant = "urn:cwl:tenant:assignment-reraise"
        with mock.patch(
            "accounting_information_platform.bank_statement.PostgresPostingLedger",
            _Ledger,
        ):
            with mock.patch(
                "accounting_information_platform.bank_statement._load_bank_account",
                return_value=(uuid.uuid4(), "KRW", "sha256:" + "ab" * 32),
            ):
                with self.assertRaisesRegex(RuntimeError, "disk full"):
                    accept_bank_account_assignment(
                        {
                            "tenant_reference": tenant,
                            "bank_account_reference": "urn:cwl:bank_account:reraise",
                            "legal_entity_reference": "urn:cwl:legal_entity:reraise",
                            "accounting_book_reference": "urn:cwl:accounting_book:reraise",
                            "chart_account_code": "110200",
                            "valid_from": "2026-01-01T00:00:00Z",
                            "assignment_idempotency_key": "assign-reraise-probe",
                        },
                        "postgresql://unused",
                        tenant,
                    )

    def test_assignment_replay_reload_failure_fails_closed(self) -> None:
        """A replay whose stored binding cannot be reloaded fails closed, not 200."""

        class _Result:
            def __init__(self, rows: tuple[object, ...]) -> None:
                self._rows = rows

            def fetchone(self) -> tuple[object, ...] | None:
                return self._rows or None

        class _Connection:
            def execute(self, sql: object, _params: object = None) -> "_Result":
                query = sql if isinstance(sql, str) else str(sql)
                if "assignment_command_hash" in query:
                    # Prior command identity resolves with matching evidence.
                    return _Result((uuid.uuid4(), "sha256:probe"))
                if "SELECT" in query:
                    # The reload lookup finds nothing, which must fail closed.
                    return _Result(())
                return _Result((uuid.uuid4(),))

        class _Ledger:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                return None

            def _session(self) -> contextlib.AbstractContextManager[object]:
                return contextlib.nullcontext(_Connection())

            def _require_tenant(self, _connection: object) -> object:
                return uuid.uuid4()

            def _acquire_command_lock(self, _connection: object, _scope: str) -> None:
                return None

        tenant = "urn:cwl:tenant:assignment-reload"
        with mock.patch(
            "accounting_information_platform.bank_statement.PostgresPostingLedger",
            _Ledger,
        ), mock.patch(
            "accounting_information_platform.bank_statement._assignment_command_hash",
            return_value="sha256:probe",
        ):
            with self.assertRaisesRegex(AccountingValidationError, "could not be reloaded"):
                accept_bank_account_assignment(
                    {
                        "tenant_reference": tenant,
                        "bank_account_reference": "urn:cwl:bank_account:reload",
                        "legal_entity_reference": "urn:cwl:legal_entity:reload",
                        "accounting_book_reference": "urn:cwl:accounting_book:reload",
                        "chart_account_code": "110200",
                        "valid_from": "2026-01-01T00:00:00Z",
                        "assignment_idempotency_key": "assign-reload-probe",
                    },
                    "postgresql://unused",
                    tenant,
                )

    def test_manifest_revision_and_parser_reject_handlers_fail_closed(self) -> None:
        """Wrong adapter identity and unused expat reject handlers fail closed."""
        real_loads = json.loads

        def _wrong_revision(text: str, *args: object, **kwargs: object) -> object:
            data = real_loads(text, *args, **kwargs)
            if isinstance(data, dict) and "artifacts" in data:
                mutated = dict(data)
                mutated["message_definition_identifier"] = "camt.053.001.13"
                return mutated
            return data

        with mock.patch(
            "accounting_information_platform.bank_statement.json.loads",
            _wrong_revision,
        ):
            with self.assertRaisesRegex(
                AccountingValidationError, "message-definition identifier"
            ):
                load_adapter_manifest()
        captured: dict[str, object] = {}

        class _CapturingParser:
            ordered_attributes = True

            def __setattr__(self, name: str, value: object) -> None:
                if name.endswith("Handler"):
                    captured[name] = value
                object.__setattr__(self, name, value)

            def Parse(self, _payload: bytes, _final: bool) -> None:
                return None

        with mock.patch(
            "accounting_information_platform.bank_statement.expat.ParserCreate",
            return_value=_CapturingParser(),
        ):
            with self.assertRaisesRegex(AccountingValidationError, "no document element"):
                _parse_bounded_xml(b"<Document/>")
        with self.assertRaisesRegex(AccountingValidationError, "external"):
            captured["ExternalEntityRefHandler"]()
        with self.assertRaisesRegex(AccountingValidationError, "entity"):
            captured["EntityDeclHandler"]()
        captured["CharacterDataHandler"]("leading")
        self.assertEqual(
            _bank_statement_status(AccountingValidationError("bank account is not recorded")),
            404,
        )
        self.assertEqual(
            _bank_statement_status(AccountingValidationError("page_limit must be an integer")),
            400,
        )
        self.assertEqual(
            _bank_statement_status(AccountingValidationError("cursor must be recorded_at")),
            400,
        )
        self.assertEqual(
            _bank_statement_status(AccountingValidationError("bank_statement_record_id must be a UUID")),
            400,
        )
        self.assertEqual(
            _bank_statement_status(
                AccountingValidationError(
                    "assignment valid_from must be an ISO-8601 date or timestamp. "
                    "Supply a UTC timestamp"
                )
            ),
            422,
        )
        self.assertEqual(
            _bank_statement_status(AccountingValidationError("chart_account_code is required")),
            422,
        )

    def test_structural_account_and_indicator_misses_fail_closed(self) -> None:
        """Required paths, account identity, and CdtDbtInd stay fail-closed."""
        empty_document = (
            b'   <Document xmlns="urn:iso:std:iso:20022:tech:xsd:camt.053.001.14"/>'
        )
        with self.assertRaisesRegex(AccountingValidationError, "required camt.053"):
            parse_bank_statement_payload(empty_document, CAMT053_MESSAGE_DEFINITION)
        text = FIXTURE.read_text(encoding="utf-8")
        with self.assertRaisesRegex(AccountingValidationError, "account identifier"):
            parse_bank_statement_payload(
                text.replace(
                    "<Othr>\n            <Id>acct-opaque-fixture-only</Id>\n          </Othr>",
                    "",
                ).encode("utf-8"),
                CAMT053_MESSAGE_DEFINITION,
            )
        with self.assertRaisesRegex(AccountingValidationError, "CRDT or DBIT"):
            parse_bank_statement_payload(
                text.replace(
                    "<NtryRef>NTRY-1</NtryRef>\n        <Amt Ccy=\"KRW\">25000.00</Amt>\n        <CdtDbtInd>CRDT</CdtDbtInd>",
                    "<NtryRef>NTRY-1</NtryRef>\n        <Amt Ccy=\"KRW\">25000.00</Amt>\n        <CdtDbtInd>FOOO</CdtDbtInd>",
                    1,
                ).encode("utf-8"),
                CAMT053_MESSAGE_DEFINITION,
            )
        with self.assertRaisesRegex(AccountingValidationError, "transaction detail"):
            parse_bank_statement_payload(
                text.replace(
                    "<AmtDtls>\n              <TxAmt>\n                <Amt Ccy=\"KRW\">25000.00</Amt>\n              </TxAmt>\n            </AmtDtls>",
                    "",
                    1,
                ).encode("utf-8"),
                CAMT053_MESSAGE_DEFINITION,
            )
        with self.assertRaisesRegex(AccountingValidationError, "statement identity"):
            parse_bank_statement_payload(
                text.replace("<Id>BANK-STMT-2026-08-24</Id>", "<Id></Id>", 1).encode("utf-8"),
                CAMT053_MESSAGE_DEFINITION,
            )


if __name__ == "__main__":
    unittest.main()
