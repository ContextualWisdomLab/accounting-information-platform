"""RED/GREEN parser and adapter-evidence contracts for camt.053.001.14."""

from __future__ import annotations

import hashlib
import unittest
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
from accounting_information_platform.bank_statement import (
    MAX_ATTRIBUTE_COUNT,
    MAX_ELEMENT_COUNT,
    MAX_ENTRY_COUNT,
    MAX_TEXT_BYTES,
    MAX_XML_DEPTH,
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


if __name__ == "__main__":
    unittest.main()
