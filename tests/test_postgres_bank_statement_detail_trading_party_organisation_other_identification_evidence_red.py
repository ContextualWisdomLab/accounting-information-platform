"""PostgreSQL REDs for complete camt.053 trading-party organisation Othr evidence."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import unittest
import uuid
from decimal import Decimal

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
_TRADING_PARTY_PURPOSE = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/RltdPties/TradgPty"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailTradingPartyOrganisationOtherIdentificationEvidenceRedTests(
    unittest.TestCase
):
    """Retain GenericOrganisationIdentification3 without granting accounting authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare scheme, issuer, multiplicity, and representation-control variants."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "              </Dbtr>\n            </RltdPties>"
        self.assertEqual(fixture.count(marker), 1)
        self.base = {
            "choice": "party",
            "name": "Trading Party Alpha",
            "identification": {
                "choice": "organisation",
                "identifier": "TRADING-ID-001",
                "scheme": {"code": "BANK"},
                "issuer": "Relationship Bank Registry",
                "additional_identifiers": [
                    {
                        "id": "TRADING-TAX-001",
                        "scheme": {"proprietary": "LOCAL_TAX_ID"},
                        "issuer": "National Tax Registry",
                    }
                ],
            },
        }
        self.variants = self._variants(self.base)
        self.base_payload = self._with_trading_party(fixture, marker, self.base)
        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.variant_payloads = {
            name: self._with_trading_party(fixture, marker, value)
            for name, value in self.variants.items()
        }
        self.variant_statements = {
            name: parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
            for name, payload in self.variant_payloads.items()
        }

        trading_party_xml = self._trading_party_xml(self.base)
        self.assertEqual(self.base_payload.count(trading_party_xml.encode("utf-8")), 1)
        reformatted_xml = trading_party_xml.replace(
            "                        <SchmeNm>\n",
            "                        <SchmeNm>\n                          \n",
            1,
        )
        self.assertNotEqual(reformatted_xml, trading_party_xml)
        self.reformatted_payload = self.base_payload.replace(
            trading_party_xml.encode("utf-8"), reformatted_xml.encode("utf-8"), 1
        )
        self.assertNotEqual(self.reformatted_payload, self.base_payload)
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

    def test_other_identification_scheme_issuer_and_population_are_material(self) -> None:
        """Scheme choice, issuer, and repeated Othr population change evidence identity."""
        base_hash = self._expected_hash(self.base)
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        self.assertRegex(base_hash, _HASH_PATTERN)
        self.assertEqual(
            getattr(base_detail, "trading_party_evidence_hash", None), base_hash
        )
        self._assert_entry_hash_binding(base_entry, base_hash)
        self._assert_accounting_truth(base_entry, base_detail)

        variant_hashes = {
            name: self._expected_hash(value) for name, value in self.variants.items()
        }
        self.assertEqual(len(set(variant_hashes.values())), len(variant_hashes))
        self.assertNotIn(base_hash, set(variant_hashes.values()))
        for name, statement in self.variant_statements.items():
            with self.subTest(name=name):
                expected_hash = variant_hashes[name]
                entry = statement.entries[0]
                detail = entry.entry_details[0]
                self.assertEqual(
                    getattr(detail, "trading_party_evidence_hash", None), expected_hash
                )
                self._assert_entry_hash_binding(entry, expected_hash)
                self._assert_accounting_truth(entry, detail)
                self.assertEqual(
                    self.base_statement.account_identifier_hash,
                    statement.account_identifier_hash,
                )
                self.assertNotEqual(base_detail.source_detail_hash, detail.source_detail_hash)
                self.assertNotEqual(base_entry.source_entry_hash, entry.source_entry_hash)
                self.assertNotEqual(
                    self.base_statement.normalized_payload_hash,
                    statement.normalized_payload_hash,
                )
                self.assertEqual(
                    self.base_statement.entries[1].source_entry_hash,
                    statement.entries[1].source_entry_hash,
                )

    def test_xml_layout_does_not_change_other_identification_semantics(self) -> None:
        """Whitespace in SchmeNm changes raw bytes, not organisation identity semantics."""
        expected_hash = self._expected_hash(self.base)
        base_entry = self.base_statement.entries[0]
        reformatted_entry = self.reformatted_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        reformatted_detail = reformatted_entry.entry_details[0]
        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(base_detail, "trading_party_evidence_hash", None), expected_hash
        )
        self.assertEqual(
            getattr(reformatted_detail, "trading_party_evidence_hash", None), expected_hash
        )
        self._assert_entry_hash_binding(base_entry, expected_hash)
        self._assert_entry_hash_binding(reformatted_entry, expected_hash)
        self._assert_accounting_truth(reformatted_entry, reformatted_detail)
        self.assertEqual(base_detail.source_detail_hash, reformatted_detail.source_detail_hash)
        self.assertEqual(base_entry.source_entry_hash, reformatted_entry.source_entry_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_other_identification_requires_explicit_statement_correction(self) -> None:
        """Accepted organisation identifier provenance cannot be silently replaced."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])
        for name, payload in self.variant_payloads.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                    accept_bank_statement_evidence(
                        self._command(payload, name),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_buyer_read_preserves_other_identifications_without_changing_amount_truth(self) -> None:
        """Tenant reads expose complete Othr evidence while 25000 KRW remains fixed."""
        expected_hash = self._expected_hash(self.base)
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
        self.assertEqual(detail.get("trading_party_evidence_hash"), expected_hash)
        self.assertEqual(detail.get("trading_party"), self.base)
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    @classmethod
    def _variants(cls, base: dict[str, object]) -> dict[str, dict[str, object]]:
        """Return independent first/repeated Othr scheme, issuer, and presence variants."""
        variants: dict[str, dict[str, object]] = {}
        first_scheme_value = copy.deepcopy(base)
        cls._identification(first_scheme_value)["scheme"] = {"code": "DUNS"}
        variants["first-scheme-code-value"] = first_scheme_value

        first_scheme_choice = copy.deepcopy(base)
        cls._identification(first_scheme_choice)["scheme"] = {"proprietary": "BANK"}
        variants["first-scheme-choice-same-scalar"] = first_scheme_choice

        first_scheme_absent = copy.deepcopy(base)
        cls._identification(first_scheme_absent).pop("scheme")
        variants["first-scheme-absent"] = first_scheme_absent

        first_issuer_value = copy.deepcopy(base)
        cls._identification(first_issuer_value)["issuer"] = "Alternate Relationship Registry"
        variants["first-issuer-value"] = first_issuer_value

        first_issuer_absent = copy.deepcopy(base)
        cls._identification(first_issuer_absent).pop("issuer")
        variants["first-issuer-absent"] = first_issuer_absent

        additional_id = copy.deepcopy(base)
        cls._additional(additional_id)["id"] = "TRADING-TAX-002"
        variants["additional-id-value"] = additional_id

        additional_scheme = copy.deepcopy(base)
        cls._additional(additional_scheme)["scheme"] = {"proprietary": "NATIONAL_TAX_ID"}
        variants["additional-scheme-value"] = additional_scheme

        additional_issuer = copy.deepcopy(base)
        cls._additional(additional_issuer)["issuer"] = "Alternate Tax Registry"
        variants["additional-issuer-value"] = additional_issuer

        additional_removed = copy.deepcopy(base)
        cls._identification(additional_removed).pop("additional_identifiers")
        variants["additional-identifier-removed"] = additional_removed
        return variants

    @staticmethod
    def _identification(value: dict[str, object]) -> dict[str, object]:
        identification = value.get("identification")
        if not isinstance(identification, dict):
            raise AssertionError("trading-party identification must be a mapping")
        return identification

    @classmethod
    def _additional(cls, value: dict[str, object]) -> dict[str, object]:
        additional = cls._identification(value).get("additional_identifiers")
        if not isinstance(additional, list) or len(additional) != 1:
            raise AssertionError("RED fixture must contain one additional organisation identifier")
        item = additional[0]
        if not isinstance(item, dict):
            raise AssertionError("additional organisation identifier must be a mapping")
        return item

    @staticmethod
    def _assert_accounting_truth(entry: object, detail: object) -> None:
        """Keep organisation identifier provenance outside accounting measurement truth."""
        if getattr(entry, "entry_amount", None) != Decimal("25000.00"):
            raise AssertionError("organisation identifiers must not alter entry amount")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("organisation identifiers must not alter entry currency")
        if getattr(detail, "detail_amount", None) != Decimal("25000.00"):
            raise AssertionError("organisation identifiers must not alter detail amount")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("organisation identifiers must not alter detail currency")

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Require source_entry_hash to digest the complete trading-party evidence hash."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        first_detail = details[0]
        if not isinstance(first_detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if first_detail.get("trading_party_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry exact trading_party_evidence_hash"
            )
        preimage = json.dumps(
            projection, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if getattr(entry, "source_entry_hash", None) != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must digest the canonical entry projection containing "
                "trading_party_evidence_hash"
            )

    @staticmethod
    def _expected_hash(value: dict[str, object]) -> str:
        """Digest complete admitted GenericOrganisationIdentification3 provenance."""
        preimage = json.dumps(
            {"evidence_type": _TRADING_PARTY_PURPOSE, "trading_party": value},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @classmethod
    def _trading_party_xml(cls, value: dict[str, object]) -> str:
        """Serialize repeated GenericOrganisationIdentification3 evidence in schema order."""
        if value.get("choice") != "party":
            raise AssertionError("organisation-Othr RED requires the Pty branch")
        identification = cls._identification(value)
        if identification.get("choice") != "organisation":
            raise AssertionError("organisation-Othr RED requires OrganisationIdentification39")
        first = {
            "id": str(identification["identifier"]),
            "scheme": identification.get("scheme"),
            "issuer": identification.get("issuer"),
        }
        additional = identification.get("additional_identifiers", [])
        if not isinstance(additional, list):
            raise AssertionError("additional_identifiers must be a list")
        items: list[dict[str, object]] = [first]
        for item in additional:
            if not isinstance(item, dict):
                raise AssertionError("additional organisation identifier must be a mapping")
            items.append(item)
        other_xml = "".join(cls._other_xml(item) for item in items)
        return (
            "              <TradgPty>\n"
            "                <Pty>\n"
            f"                  <Nm>{value['name']}</Nm>\n"
            "                  <Id>\n"
            "                    <OrgId>\n"
            + other_xml
            + "                    </OrgId>\n"
            "                  </Id>\n"
            "                </Pty>\n"
            "              </TradgPty>\n"
        )

    @classmethod
    def _other_xml(cls, value: dict[str, object]) -> str:
        """Serialize one GenericOrganisationIdentification3 in Id/SchmeNm/Issr order."""
        identifier = str(value["id"])
        scheme = value.get("scheme")
        scheme_xml = "" if scheme is None else cls._scheme_xml(scheme)
        issuer = value.get("issuer")
        issuer_xml = "" if issuer is None else f"                        <Issr>{issuer}</Issr>\n"
        return (
            "                      <Othr>\n"
            f"                        <Id>{identifier}</Id>\n"
            + scheme_xml
            + issuer_xml
            + "                      </Othr>\n"
        )

    @staticmethod
    def _scheme_xml(value: object) -> str:
        """Serialize OrganisationIdentificationSchemeName1Choice without aliasing its branch."""
        if not isinstance(value, dict):
            raise AssertionError("organisation identification scheme must be a mapping")
        if set(value) == {"code"}:
            choice_xml = f"                          <Cd>{value['code']}</Cd>\n"
        elif set(value) == {"proprietary"}:
            choice_xml = f"                          <Prtry>{value['proprietary']}</Prtry>\n"
        else:
            raise AssertionError("scheme must contain exactly one of code or proprietary")
        return (
            "                        <SchmeNm>\n"
            + choice_xml
            + "                        </SchmeNm>\n"
        )

    @classmethod
    def _with_trading_party(
        cls,
        fixture: str,
        marker: str,
        value: dict[str, object],
    ) -> bytes:
        """Insert organisation Othr evidence after debtor evidence."""
        return fixture.replace(
            marker,
            "              </Dbtr>\n"
            + cls._trading_party_xml(value)
            + "            </RltdPties>",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"detail-trading-party-org-other-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
