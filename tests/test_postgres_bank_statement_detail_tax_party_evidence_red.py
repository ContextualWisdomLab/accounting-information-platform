"""PostgreSQL REDs for camt.053 TaxData1 party provenance."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
import uuid

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
from tests import test_postgres_posting as posting
from tests.test_postgres_bank_statement_detail_tax_evidence_red import (
    BankStatementDetailTaxEvidenceRedTests as TaxEvidenceRed,
)

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_DETAIL_TAX_PURPOSE = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/Tax"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailTaxPartyEvidenceRedTests(unittest.TestCase):
    """Retain TaxData1 creditor/debtor provenance without creating accounting truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Build schema-valid TaxData1 party variants with fixed monetary facts."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "            <RmtInf>\n"
            "              <Ustrd>Invoice 1001</Ustrd>\n"
            "            </RmtInf>\n"
            "          </TxDtls>"
        )
        self.assertEqual(fixture.count(marker), 1)

        self.tax = {
            "administration_zone": "KR-SEOUL",
            "reference_number": "TAX-REF-001",
            "method": "WITHHOLDING",
            "total_taxable_base_amount": "25000.00",
            "total_taxable_base_currency_code": "KRW",
            "total_tax_amount": "2500.00",
            "total_tax_currency_code": "KRW",
            "tax_date": "2026-08-24",
            "records": (
                (
                    "VAT",
                    "OUTPUT",
                    "2026",
                    "MM08",
                    "10.00",
                    "25000.00",
                    "KRW",
                    "2500.00",
                    "KRW",
                ),
            ),
        }
        self.base_parties = {
            "creditor": {
                "tax_id": "KR-TAX-CRED-001",
                "registration_id": "KR-REG-CRED-001",
                "tax_type": "VAT",
            },
            "debtor": {
                "tax_id": "KR-TAX-DEBT-001",
                "registration_id": "KR-REG-DEBT-001",
                "tax_type": "VAT",
                "authorisation": {
                    "title": "Tax agent",
                    "name": "Seoul Tax Representative",
                },
            },
            "ultimate_debtor": {
                "tax_id": "KR-TAX-ULT-001",
                "registration_id": "KR-REG-ULT-001",
                "tax_type": "VAT",
                "authorisation": {
                    "title": "Ultimate tax agent",
                    "name": "Ultimate Tax Representative",
                },
            },
        }
        self.changed_creditor = self._changed_party(
            "creditor", "tax_id", "KR-TAX-CRED-002"
        )
        self.changed_debtor_title = self._changed_authorisation(
            "debtor", "title", "Senior tax agent"
        )
        self.changed_debtor_name = self._changed_authorisation(
            "debtor", "name", "Seoul Tax Representative Revised"
        )
        self.changed_ultimate_registration = self._changed_party(
            "ultimate_debtor", "registration_id", "KR-REG-ULT-002"
        )

        without_parties = TaxEvidenceRed._with_tax(fixture, marker, self.tax)
        tax_open = b"            <Tax>\n"
        self.assertEqual(without_parties.count(tax_open), 1)

        self.base_payload = self._with_parties(without_parties, self.base_parties)
        self.changed_creditor_payload = self._with_parties(
            without_parties, self.changed_creditor
        )
        self.changed_debtor_title_payload = self._with_parties(
            without_parties, self.changed_debtor_title
        )
        self.changed_debtor_name_payload = self._with_parties(
            without_parties, self.changed_debtor_name
        )
        self.changed_ultimate_registration_payload = self._with_parties(
            without_parties, self.changed_ultimate_registration
        )
        self.reformatted_payload = self.base_payload.replace(
            b"              <TaxId>KR-TAX-CRED-001</TaxId>\n",
            b"              <TaxId>\n"
            b"                KR-TAX-CRED-001\n"
            b"              </TaxId>\n",
            1,
        )

        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_creditor_statement = parse_bank_statement_payload(
            self.changed_creditor_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_debtor_title_statement = parse_bank_statement_payload(
            self.changed_debtor_title_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_debtor_name_statement = parse_bank_statement_payload(
            self.changed_debtor_name_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_ultimate_registration_statement = parse_bank_statement_payload(
            self.changed_ultimate_registration_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.reformatted_statement = parse_bank_statement_payload(
            self.reformatted_payload, CAMT053_MESSAGE_DEFINITION
        )

        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.base_statement.account_currency_code,
                "account_identifier_hash": self.base_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_tax_parties_are_independently_material_evidence(self) -> None:
        """Tax IDs and debtor authorisation facts must affect canonical identity."""
        variants = (
            (self.base_statement, self.base_parties),
            (self.changed_creditor_statement, self.changed_creditor),
            (self.changed_debtor_title_statement, self.changed_debtor_title),
            (self.changed_debtor_name_statement, self.changed_debtor_name),
            (
                self.changed_ultimate_registration_statement,
                self.changed_ultimate_registration,
            ),
        )
        hashes: list[str] = []
        for statement, parties in variants:
            expected_hash = self._expected_hash(parties)
            detail = statement.entries[0].entry_details[0]
            self.assertRegex(expected_hash, _HASH_PATTERN)
            self.assertEqual(
                getattr(detail, "detail_tax_evidence_hash", None),
                expected_hash,
            )
            TaxEvidenceRed._assert_entry_hash_binding(
                statement.entries[0], expected_hash
            )
            hashes.append(expected_hash)

        self.assertEqual(len(set(hashes)), len(hashes))
        for changed in (
            self.changed_creditor_statement,
            self.changed_debtor_title_statement,
            self.changed_debtor_name_statement,
            self.changed_ultimate_registration_statement,
        ):
            base_detail = self.base_statement.entries[0].entry_details[0]
            changed_detail = changed.entries[0].entry_details[0]
            self.assertEqual(
                self.base_statement.account_identifier_hash,
                changed.account_identifier_hash,
            )
            self.assertEqual(
                self.base_statement.entries[0].entry_amount,
                changed.entries[0].entry_amount,
            )
            self.assertEqual(base_detail.detail_amount, changed_detail.detail_amount)
            self.assertNotEqual(
                base_detail.source_detail_hash,
                changed_detail.source_detail_hash,
            )
            self.assertNotEqual(
                self.base_statement.entries[0].source_entry_hash,
                changed.entries[0].source_entry_hash,
            )
            self.assertNotEqual(
                self.base_statement.normalized_payload_hash,
                changed.normalized_payload_hash,
            )
            self.assertEqual(
                self.base_statement.entries[1].source_entry_hash,
                changed.entries[1].source_entry_hash,
            )

    def test_tax_party_layout_is_not_semantic_identity(self) -> None:
        """Whitespace changes raw provenance, not TaxData1 party semantics."""
        expected_hash = self._expected_hash(self.base_parties)
        base_detail = self.base_statement.entries[0].entry_details[0]
        reformatted_detail = self.reformatted_statement.entries[0].entry_details[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(base_detail, "detail_tax_evidence_hash", None), expected_hash
        )
        self.assertEqual(
            getattr(reformatted_detail, "detail_tax_evidence_hash", None), expected_hash
        )
        TaxEvidenceRed._assert_entry_hash_binding(
            self.reformatted_statement.entries[0], expected_hash
        )
        self.assertEqual(base_detail.source_detail_hash, reformatted_detail.source_detail_hash)
        self.assertEqual(
            self.base_statement.entries[0].source_entry_hash,
            self.reformatted_statement.entries[0].source_entry_hash,
        )
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_tax_party_requires_explicit_statement_correction(self) -> None:
        """Changed bank-reported tax parties cannot silently replace accepted evidence."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        for suffix, payload in (
            ("creditor-tax-id", self.changed_creditor_payload),
            ("debtor-authorisation-title", self.changed_debtor_title_payload),
            ("debtor-authorisation-name", self.changed_debtor_name_payload),
            ("ultimate-debtor-registration", self.changed_ultimate_registration_payload),
        ):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(
                    AccountingValidationError, _CORRECTION_ERROR
                ):
                    accept_bank_statement_evidence(
                        self._command(payload, suffix),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_buyer_read_preserves_tax_parties_without_recomputing_amount_truth(
        self,
    ) -> None:
        """Buyer read exposes party provenance while transaction amount stays distinct."""
        expected_hash = self._expected_hash(self.base_parties)
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "lookup"),
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
        detail = entry["entry_details"][0]

        self.assertEqual(detail.get("detail_tax_evidence_hash"), expected_hash)
        tax_evidence = detail.get("tax_evidence")
        self.assertIsInstance(tax_evidence, dict)
        if not isinstance(tax_evidence, dict):
            raise AssertionError("tax_evidence must be a mapping")
        self.assertEqual(tax_evidence.get("creditor"), self.base_parties["creditor"])
        self.assertEqual(tax_evidence.get("debtor"), self.base_parties["debtor"])
        self.assertEqual(
            tax_evidence.get("ultimate_debtor"), self.base_parties["ultimate_debtor"]
        )
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _expected_hash(self, parties: dict[str, object]) -> str:
        """Digest TaxData1 with creditor, debtor, and ultimate-debtor provenance."""
        records = self.tax["records"]
        if not isinstance(records, tuple):
            raise AssertionError("test tax records must be an immutable tuple")
        preimage = json.dumps(
            {
                "evidence_type": _DETAIL_TAX_PURPOSE,
                "creditor": parties["creditor"],
                "debtor": parties["debtor"],
                "ultimate_debtor": parties["ultimate_debtor"],
                "administration_zone": self.tax["administration_zone"],
                "reference_number": self.tax["reference_number"],
                "method": self.tax["method"],
                "total_taxable_base_amount": self.tax[
                    "total_taxable_base_amount"
                ],
                "total_taxable_base_currency_code": self.tax[
                    "total_taxable_base_currency_code"
                ],
                "total_tax_amount": self.tax["total_tax_amount"],
                "total_tax_currency_code": self.tax["total_tax_currency_code"],
                "tax_date": self.tax["tax_date"],
                "records": [
                    {
                        "type": record[0],
                        "category": record[1],
                        "period_year": record[2],
                        "period_type": record[3],
                        "rate": record[4],
                        "taxable_base_amount": record[5],
                        "taxable_base_currency_code": record[6],
                        "total_amount": record[7],
                        "total_amount_currency_code": record[8],
                    }
                    for record in records
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    def _changed_party(
        self, party_name: str, field_name: str, value: str
    ) -> dict[str, object]:
        parties = json.loads(json.dumps(self.base_parties))
        party = parties[party_name]
        if not isinstance(party, dict):
            raise AssertionError("test party projection must be a mapping")
        party[field_name] = value
        return parties

    def _changed_authorisation(
        self, party_name: str, field_name: str, value: str
    ) -> dict[str, object]:
        parties = json.loads(json.dumps(self.base_parties))
        party = parties[party_name]
        if not isinstance(party, dict):
            raise AssertionError("test party projection must be a mapping")
        authorisation = party.get("authorisation")
        if not isinstance(authorisation, dict):
            raise AssertionError("test debtor authorisation must be a mapping")
        authorisation[field_name] = value
        return parties

    @classmethod
    def _with_parties(cls, payload: bytes, parties: dict[str, object]) -> bytes:
        """Insert schema-ordered TaxData1 parties before administration-zone evidence."""
        tax_open = b"            <Tax>\n"
        if payload.count(tax_open) != 1:
            raise AssertionError("fixture must contain one TaxData1 element")
        rendered = cls._party_xml("Cdtr", parties["creditor"], authorised=False)
        rendered += cls._party_xml("Dbtr", parties["debtor"], authorised=True)
        rendered += cls._party_xml(
            "UltmtDbtr", parties["ultimate_debtor"], authorised=True
        )
        return payload.replace(tax_open, tax_open + rendered, 1)

    @staticmethod
    def _party_xml(tag: str, party: object, *, authorised: bool) -> bytes:
        """Render TaxParty1/TaxParty2 in current V14 sequence order."""
        if not isinstance(party, dict):
            raise AssertionError("test tax party must be a mapping")
        rendered = (
            f"              <{tag}>\n"
            f"                <TaxId>{party['tax_id']}</TaxId>\n"
            f"                <RegnId>{party['registration_id']}</RegnId>\n"
            f"                <TaxTp>{party['tax_type']}</TaxTp>\n"
        )
        if authorised:
            authorisation = party.get("authorisation")
            if not isinstance(authorisation, dict):
                raise AssertionError("TaxParty2 fixture requires authorisation")
            rendered += (
                "                <Authstn>\n"
                f"                  <Titl>{authorisation['title']}</Titl>\n"
                f"                  <Nm>{authorisation['name']}</Nm>\n"
                "                </Authstn>\n"
            )
        rendered += f"              </{tag}>\n"
        return rendered.encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with stable statement identity."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"detail-tax-party-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
