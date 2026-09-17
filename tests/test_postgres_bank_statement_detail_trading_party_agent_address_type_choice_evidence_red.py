"""PostgreSQL REDs for trading-agent PostalAddress27 address-type choice evidence."""

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


class BankStatementDetailTradingPartyAgentAddressTypeChoiceEvidenceRedTests(
    unittest.TestCase
):
    """Retain AddressType3Choice semantics without changing accounting truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare institution and branch proprietary address-type variants."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "              </Dbtr>\n            </RltdPties>"
        self.assertEqual(fixture.count(marker), 1)

        self.base = {
            "choice": "agent",
            "financial_institution": {
                "bicfi": "DEUTDEFFXXX",
                "name": "Trading Party Agent",
                "postal_address": {
                    "address_type": {
                        "proprietary": {
                            "id": "CSTM",
                            "issuer": "CWL Address Registry",
                            "scheme_name": "ADDR_TYPE",
                        }
                    },
                    "country": "DE",
                },
            },
            "branch": {
                "id": "TRADING-BRANCH-001",
                "name": "Frankfurt Custody Branch",
                "postal_address": {
                    "address_type": {
                        "proprietary": {
                            "id": "CSTB",
                            "issuer": "CWL Branch Registry",
                            "scheme_name": "BRANCH_ADDR_TYPE",
                        }
                    },
                    "country": "DE",
                },
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
            "                        <Prtry>\n",
            "                        <Prtry>\n                          \n",
            1,
        )
        self.assertNotEqual(reformatted_xml, trading_party_xml)
        self.reformatted_payload = self.base_payload.replace(
            trading_party_xml.encode("utf-8"), reformatted_xml.encode("utf-8"), 1
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

    def test_address_type_choice_and_proprietary_fields_are_material(self) -> None:
        """Choice discriminator and GenericIdentification30 fields stay material."""
        base_hash = self._expected_hash(self.base)
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        self.assertRegex(base_hash, _HASH_PATTERN)
        self.assertEqual(
            getattr(base_detail, "trading_party_evidence_hash", None), base_hash
        )
        self._assert_entry_hash_binding(base_entry, base_hash)
        self._assert_exact_accounting_amount(base_entry, base_detail)

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
                self._assert_exact_accounting_amount(entry, detail)
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

    def test_xml_layout_does_not_change_address_type_choice_identity(self) -> None:
        """Whitespace inside Prtry changes bytes, not semantic identity."""
        expected = self._expected_hash(self.base)
        base_entry = self.base_statement.entries[0]
        reformatted_entry = self.reformatted_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        reformatted_detail = reformatted_entry.entry_details[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(base_detail, "trading_party_evidence_hash", None), expected
        )
        self.assertEqual(
            getattr(reformatted_detail, "trading_party_evidence_hash", None), expected
        )
        self._assert_entry_hash_binding(base_entry, expected)
        self._assert_entry_hash_binding(reformatted_entry, expected)
        self._assert_exact_accounting_amount(reformatted_entry, reformatted_detail)
        self.assertEqual(base_detail.source_detail_hash, reformatted_detail.source_detail_hash)
        self.assertEqual(base_entry.source_entry_hash, reformatted_entry.source_entry_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_address_type_choice_requires_explicit_correction(self) -> None:
        """Accepted address-type provenance cannot be replaced by silent replay."""
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

    def test_buyer_read_preserves_address_type_choice_without_changing_amount(self) -> None:
        """Tenant reads expose proprietary AdrTp while exact 25000 KRW stays fixed."""
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

    @staticmethod
    def _assert_exact_accounting_amount(entry: object, detail: object) -> None:
        """Pin accounting truth independently from bank-reported address semantics."""
        if getattr(entry, "entry_amount", None) != Decimal("25000.00"):
            raise AssertionError("address-type evidence must retain exact 25000.00 entry amount")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("address-type evidence must retain KRW entry currency")
        if getattr(detail, "detail_amount", None) != Decimal("25000.00"):
            raise AssertionError("address-type evidence must retain exact 25000.00 detail amount")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("address-type evidence must retain KRW detail currency")

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Require source_entry_hash to digest the trading-party evidence hash."""
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
        """Digest the complete admitted Party50Choice agent projection."""
        preimage = json.dumps(
            {"evidence_type": _TRADING_PARTY_PURPOSE, "trading_party": value},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @classmethod
    def _variants(cls, base: dict[str, object]) -> dict[str, dict[str, object]]:
        """Change each proprietary field and the Cd/Prtry discriminator independently."""
        variants: dict[str, dict[str, object]] = {}
        for owner in ("financial_institution", "branch"):
            for field, replacement in (
                ("id", "OTHR"),
                ("issuer", "Alternate Address Registry"),
                ("scheme_name", "ALT_ADDR_TYPE"),
            ):
                variant = copy.deepcopy(base)
                variant[owner]["postal_address"]["address_type"]["proprietary"][field] = replacement  # type: ignore[index]
                variants[f"{owner}-proprietary-{field}"] = variant

            without_scheme = copy.deepcopy(base)
            del without_scheme[owner]["postal_address"]["address_type"]["proprietary"]["scheme_name"]  # type: ignore[index]
            variants[f"{owner}-proprietary-scheme-absent"] = without_scheme

            coded = copy.deepcopy(base)
            coded[owner]["postal_address"]["address_type"] = {"code": "BIZZ"}  # type: ignore[index]
            variants[f"{owner}-choice-code"] = coded
        return variants

    @classmethod
    def _trading_party_xml(cls, value: dict[str, object]) -> str:
        """Serialize agent postal address-type evidence in V14 sequence order."""
        if value.get("choice") != "agent":
            raise AssertionError("address-type RED requires the Agt branch")
        institution = value.get("financial_institution")
        branch = value.get("branch")
        if not isinstance(institution, dict) or not isinstance(branch, dict):
            raise AssertionError("trading-agent evidence must provide institution and branch")
        institution_address = institution.get("postal_address")
        branch_address = branch.get("postal_address")
        if not isinstance(institution_address, dict) or not isinstance(branch_address, dict):
            raise AssertionError("both trading-agent levels must provide PostalAddress27")

        return (
            "              <TradgPty>\n"
            "                <Agt>\n"
            "                  <FinInstnId>\n"
            f"                    <BICFI>{institution['bicfi']}</BICFI>\n"
            f"                    <Nm>{institution['name']}</Nm>\n"
            + cls._postal_xml(institution_address, "                    ")
            + "                  </FinInstnId>\n"
            "                  <BrnchId>\n"
            f"                    <Id>{branch['id']}</Id>\n"
            f"                    <Nm>{branch['name']}</Nm>\n"
            + cls._postal_xml(branch_address, "                    ")
            + "                  </BrnchId>\n"
            "                </Agt>\n"
            "              </TradgPty>\n"
        )

    @staticmethod
    def _postal_xml(address: dict[str, object], indent: str) -> str:
        """Serialize the AddressType3Choice branch before remaining PostalAddress27 fields."""
        address_type = address.get("address_type")
        if not isinstance(address_type, dict) or len(address_type) != 1:
            raise AssertionError("AdrTp must contain exactly one AddressType3Choice branch")
        if "code" in address_type:
            address_type_xml = (
                f"{indent}  <AdrTp>\n"
                f"{indent}    <Cd>{address_type['code']}</Cd>\n"
                f"{indent}  </AdrTp>\n"
            )
        elif "proprietary" in address_type:
            proprietary = address_type["proprietary"]
            if not isinstance(proprietary, dict):
                raise AssertionError("proprietary AdrTp must be structured")
            scheme_name = proprietary.get("scheme_name")
            scheme_xml = (
                f"{indent}      <SchmeNm>{scheme_name}</SchmeNm>\n"
                if scheme_name is not None
                else ""
            )
            address_type_xml = (
                f"{indent}  <AdrTp>\n"
                f"{indent}    <Prtry>\n"
                f"{indent}      <Id>{proprietary['id']}</Id>\n"
                f"{indent}      <Issr>{proprietary['issuer']}</Issr>\n"
                + scheme_xml
                + f"{indent}    </Prtry>\n"
                f"{indent}  </AdrTp>\n"
            )
        else:
            raise AssertionError("unsupported AddressType3Choice branch")

        return (
            f"{indent}<PstlAdr>\n"
            + address_type_xml
            + f"{indent}  <Ctry>{address['country']}</Ctry>\n"
            f"{indent}</PstlAdr>\n"
        )

    @classmethod
    def _with_trading_party(
        cls, fixture: str, marker: str, value: dict[str, object]
    ) -> bytes:
        """Insert agent address-type evidence after debtor evidence."""
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
                f"detail-trading-agent-address-type-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }
