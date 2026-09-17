"""PostgreSQL REDs for camt.053 trading-agent direct-field optionality evidence."""

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


class BankStatementDetailTradingPartyAgentDirectOptionalityEvidenceRedTests(
    unittest.TestCase
):
    """Retain direct agent-field presence without promoting it to accounting truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare direct institution/branch presence and value variants."""
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
                "clearing_system_member": {"member_id": "DE-CLEAR-001"},
                "lei": "7LTWFZYICNSX8D621K86",
                "name": "Trading Party Agent",
            },
            "branch": {
                "id": "TRADING-BRANCH-001",
                "lei": "529900Z6KVD8Y83D7K60",
                "name": "Frankfurt Custody Branch",
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
            "                  <FinInstnId>\n",
            "                  <FinInstnId>\n                    \n",
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

    def test_direct_agent_field_presence_and_name_value_are_material(self) -> None:
        """Optional direct fields and institution name independently change evidence identity."""
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

    def test_xml_layout_does_not_change_direct_agent_semantics(self) -> None:
        """Whitespace inside FinInstnId changes raw bytes, not admitted agent semantics."""
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
            getattr(reformatted_detail, "trading_party_evidence_hash", None),
            expected_hash,
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

    def test_changed_direct_agent_presence_requires_explicit_correction(self) -> None:
        """Accepted direct agent provenance cannot be silently replaced."""
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

    def test_buyer_read_preserves_direct_agent_fields_without_changing_amount(self) -> None:
        """Tenant reads expose direct agent fields while exact 25000 KRW remains fixed."""
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
        """Return independent institution, branch, and container-presence variants."""
        variants: dict[str, dict[str, object]] = {}

        bicfi_absent = copy.deepcopy(base)
        cls._institution(bicfi_absent).pop("bicfi")
        variants["bicfi-absent"] = bicfi_absent

        clearing_member_absent = copy.deepcopy(base)
        cls._institution(clearing_member_absent).pop("clearing_system_member")
        variants["clearing-member-absent"] = clearing_member_absent

        institution_lei_absent = copy.deepcopy(base)
        cls._institution(institution_lei_absent).pop("lei")
        variants["institution-lei-absent"] = institution_lei_absent

        institution_name_value = copy.deepcopy(base)
        cls._institution(institution_name_value)["name"] = "Trading Party Settlement Agent"
        variants["institution-name-value"] = institution_name_value

        institution_name_absent = copy.deepcopy(base)
        cls._institution(institution_name_absent).pop("name")
        variants["institution-name-absent"] = institution_name_absent

        branch_id_absent = copy.deepcopy(base)
        cls._branch(branch_id_absent).pop("id")
        variants["branch-id-absent"] = branch_id_absent

        branch_lei_absent = copy.deepcopy(base)
        cls._branch(branch_lei_absent).pop("lei")
        variants["branch-lei-absent"] = branch_lei_absent

        branch_name_absent = copy.deepcopy(base)
        cls._branch(branch_name_absent).pop("name")
        variants["branch-name-absent"] = branch_name_absent

        branch_absent = copy.deepcopy(base)
        branch_absent.pop("branch")
        variants["branch-container-absent"] = branch_absent
        return variants

    @staticmethod
    def _institution(value: dict[str, object]) -> dict[str, object]:
        institution = value.get("financial_institution")
        if not isinstance(institution, dict):
            raise AssertionError("trading-agent RED requires financial_institution")
        return institution

    @staticmethod
    def _branch(value: dict[str, object]) -> dict[str, object]:
        branch = value.get("branch")
        if not isinstance(branch, dict):
            raise AssertionError("trading-agent RED requires branch for this variant")
        return branch

    @staticmethod
    def _assert_accounting_truth(entry: object, detail: object) -> None:
        """Keep direct agent provenance outside accounting measurement truth."""
        if getattr(entry, "entry_amount", None) != Decimal("25000.00"):
            raise AssertionError("agent provenance must not alter entry amount")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("agent provenance must not alter entry currency")
        if getattr(detail, "detail_amount", None) != Decimal("25000.00"):
            raise AssertionError("agent provenance must not alter detail amount")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("agent provenance must not alter detail currency")

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
                "source_entry_hash must digest canonical entry projection containing "
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
    def _trading_party_xml(cls, value: dict[str, object]) -> str:
        """Serialize optional direct fields in schema order for Party50Choice/Agt."""
        if value.get("choice") != "agent":
            raise AssertionError("direct-optionality RED requires the Agt branch")
        institution = cls._institution(value)
        lines = [
            "              <TradgPty>",
            "                <Agt>",
            "                  <FinInstnId>",
        ]
        bicfi = institution.get("bicfi")
        if bicfi is not None:
            lines.append(f"                    <BICFI>{bicfi}</BICFI>")
        clearing_member = institution.get("clearing_system_member")
        if clearing_member is not None:
            if not isinstance(clearing_member, dict):
                raise AssertionError("clearing_system_member must be a mapping")
            lines.extend(
                [
                    "                    <ClrSysMmbId>",
                    f"                      <MmbId>{clearing_member['member_id']}</MmbId>",
                    "                    </ClrSysMmbId>",
                ]
            )
        lei = institution.get("lei")
        if lei is not None:
            lines.append(f"                    <LEI>{lei}</LEI>")
        name = institution.get("name")
        if name is not None:
            lines.append(f"                    <Nm>{name}</Nm>")
        lines.append("                  </FinInstnId>")

        branch = value.get("branch")
        if branch is not None:
            if not isinstance(branch, dict):
                raise AssertionError("branch must be a mapping")
            lines.append("                  <BrnchId>")
            branch_id = branch.get("id")
            if branch_id is not None:
                lines.append(f"                    <Id>{branch_id}</Id>")
            branch_lei = branch.get("lei")
            if branch_lei is not None:
                lines.append(f"                    <LEI>{branch_lei}</LEI>")
            branch_name = branch.get("name")
            if branch_name is not None:
                lines.append(f"                    <Nm>{branch_name}</Nm>")
            lines.append("                  </BrnchId>")

        lines.extend(
            [
                "                </Agt>",
                "              </TradgPty>",
            ]
        )
        return "\n".join(lines) + "\n"

    @classmethod
    def _with_trading_party(
        cls, fixture: str, marker: str, value: dict[str, object]
    ) -> bytes:
        """Insert direct-optionality trading-agent evidence inside RltdPties."""
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
                f"detail-trading-agent-direct-optionality-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
