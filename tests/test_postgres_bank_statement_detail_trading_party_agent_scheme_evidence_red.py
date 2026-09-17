"""PostgreSQL REDs for trading-agent clearing-system and alternate-ID evidence."""

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


class BankStatementDetailTradingPartyAgentSchemeEvidenceRedTests(unittest.TestCase):
    """Retain agent scheme evidence without promoting it to accounting authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare clearing-system and alternate financial-ID variants."""
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
                "clearing_system_member": {
                    "clearing_system": {"proprietary": "DE-BUNDESBANK"},
                    "member_id": "DE-CLEAR-001",
                },
                "lei": "7LTWFZYICNSX8D621K86",
                "name": "Trading Party Agent",
                "other": {
                    "id": "DE-BANK-ALT-001",
                    "scheme": {"proprietary": "LOCAL_BANK_ID"},
                    "issuer": "Bundesbank Registry",
                },
            },
            "branch": {
                "id": "TRADING-BRANCH-001",
                "lei": "529900Z6KVD8Y83D7K60",
                "name": "Frankfurt Custody Branch",
            },
        }
        self.variants = {
            "clearing-system-proprietary": copy.deepcopy(self.base),
            "other-id": copy.deepcopy(self.base),
            "other-scheme-proprietary": copy.deepcopy(self.base),
            "other-issuer": copy.deepcopy(self.base),
        }
        self.variants["clearing-system-proprietary"]["financial_institution"][
            "clearing_system_member"
        ]["clearing_system"]["proprietary"] = "DE-CLEARING-NET"
        self.variants["other-id"]["financial_institution"]["other"]["id"] = (
            "DE-BANK-ALT-002"
        )
        self.variants["other-scheme-proprietary"]["financial_institution"]["other"][
            "scheme"
        ]["proprietary"] = "NATIONAL_BANK_ID"
        self.variants["other-issuer"]["financial_institution"]["other"]["issuer"] = (
            "BaFin Registry"
        )

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
            "                    <Othr>\n",
            "                    <Othr>\n                      \n",
            1,
        )
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

    def test_agent_scheme_and_alternate_identifiers_are_material(self) -> None:
        """Clearing-system and GenericFinancialIdentification1 fields stay material."""
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

    def test_xml_layout_does_not_change_agent_scheme_identity(self) -> None:
        """Whitespace inside Othr changes source bytes, not semantic identity."""
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

    def test_changed_agent_scheme_identity_requires_explicit_correction(self) -> None:
        """Accepted scheme provenance cannot be replaced by silent replay."""
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

    def test_buyer_read_preserves_agent_scheme_identity_without_changing_amount(self) -> None:
        """Tenant reads expose complete scheme evidence while 25000 KRW stays fixed."""
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
        """Pin accounting truth independently from bank-reported agent schemes."""
        if getattr(entry, "entry_amount", None) != Decimal("25000.00"):
            raise AssertionError("agent scheme evidence must retain exact 25000.00 entry amount")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("agent scheme evidence must retain KRW entry currency")
        if getattr(detail, "detail_amount", None) != Decimal("25000.00"):
            raise AssertionError("agent scheme evidence must retain exact 25000.00 detail amount")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("agent scheme evidence must retain KRW detail currency")

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

    @staticmethod
    def _trading_party_xml(value: dict[str, object]) -> str:
        """Serialize clearing-system and GenericFinancialIdentification1 evidence."""
        if value.get("choice") != "agent":
            raise AssertionError("agent-scheme RED requires the Agt branch")
        financial_institution = value.get("financial_institution")
        branch = value.get("branch")
        if not isinstance(financial_institution, dict) or not isinstance(branch, dict):
            raise AssertionError("trading-agent identity must provide institution and branch")
        clearing = financial_institution.get("clearing_system_member")
        other = financial_institution.get("other")
        if not isinstance(clearing, dict) or not isinstance(other, dict):
            raise AssertionError("institution must provide clearing and alternate identities")
        clearing_system = clearing.get("clearing_system")
        scheme = other.get("scheme")
        if not isinstance(clearing_system, dict) or not isinstance(scheme, dict):
            raise AssertionError("scheme evidence must use structured choice projections")

        return (
            "              <TradgPty>\n"
            "                <Agt>\n"
            "                  <FinInstnId>\n"
            f"                    <BICFI>{financial_institution['bicfi']}</BICFI>\n"
            "                    <ClrSysMmbId>\n"
            "                      <ClrSysId>\n"
            f"                        <Prtry>{clearing_system['proprietary']}</Prtry>\n"
            "                      </ClrSysId>\n"
            f"                      <MmbId>{clearing['member_id']}</MmbId>\n"
            "                    </ClrSysMmbId>\n"
            f"                    <LEI>{financial_institution['lei']}</LEI>\n"
            f"                    <Nm>{financial_institution['name']}</Nm>\n"
            "                    <Othr>\n"
            f"                      <Id>{other['id']}</Id>\n"
            "                      <SchmeNm>\n"
            f"                        <Prtry>{scheme['proprietary']}</Prtry>\n"
            "                      </SchmeNm>\n"
            f"                      <Issr>{other['issuer']}</Issr>\n"
            "                    </Othr>\n"
            "                  </FinInstnId>\n"
            "                  <BrnchId>\n"
            f"                    <Id>{branch['id']}</Id>\n"
            f"                    <LEI>{branch['lei']}</LEI>\n"
            f"                    <Nm>{branch['name']}</Nm>\n"
            "                  </BrnchId>\n"
            "                </Agt>\n"
            "              </TradgPty>\n"
        )

    @classmethod
    def _with_trading_party(
        cls, fixture: str, marker: str, value: dict[str, object]
    ) -> bytes:
        """Insert complete agent scheme identity after debtor evidence."""
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
                f"detail-trading-agent-scheme-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
