"""PostgreSQL REDs for TransactionParties12 repeated proprietary-party evidence."""

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
_PROPRIETARY_PARTIES_PURPOSE = (
    "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/RltdPties/Prtry"
)
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailProprietaryPartiesEvidenceRedTests(unittest.TestCase):
    """Retain repeated ProprietaryParty6 evidence without promoting accounting authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare value, choice, order, presence, and representation controls."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "              </Dbtr>\n            </RltdPties>"
        self.assertEqual(fixture.count(marker), 1)

        self.base = [
            {
                "type": "BROKER",
                "party": {"choice": "party", "name": "Execution Broker Alpha"},
            },
            {
                "type": "CUSTODIAN",
                "party": {"choice": "party", "name": "Custodian Alpha"},
            },
        ]
        self.variants = self._variants(self.base)
        self.base_payload = self._with_proprietary_parties(fixture, marker, self.base)
        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.variant_payloads = {
            name: self._with_proprietary_parties(fixture, marker, value)
            for name, value in self.variants.items()
        }
        self.variant_statements = {
            name: parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
            for name, payload in self.variant_payloads.items()
        }
        self.absent_payload = self._with_proprietary_parties(fixture, marker, [])
        self.absent_statement = parse_bank_statement_payload(
            self.absent_payload, CAMT053_MESSAGE_DEFINITION
        )

        proprietary_xml = self._proprietary_parties_xml(self.base)
        self.assertEqual(self.base_payload.count(proprietary_xml.encode("utf-8")), 1)
        reformatted_xml = proprietary_xml.replace(
            "              <Prtry>\n",
            "              <Prtry>\n                \n",
            1,
        )
        self.assertNotEqual(reformatted_xml, proprietary_xml)
        self.reformatted_payload = self.base_payload.replace(
            proprietary_xml.encode("utf-8"),
            reformatted_xml.encode("utf-8"),
            1,
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

    def test_proprietary_party_value_choice_and_order_are_material(self) -> None:
        """Required fields, nested Party50Choice, population, and order stay material."""
        base_hash = self._expected_hash(self.base)
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        self.assertRegex(base_hash, _HASH_PATTERN)
        self.assertEqual(
            getattr(base_detail, "proprietary_parties_evidence_hash", None),
            base_hash,
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
                    getattr(detail, "proprietary_parties_evidence_hash", None),
                    expected_hash,
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

    def test_whole_proprietary_party_population_absence_is_material(self) -> None:
        """Removing all optional Prtry elements removes evidence and changes source identity."""
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        absent_entry = self.absent_statement.entries[0]
        absent_detail = absent_entry.entry_details[0]

        self.assertIsNone(
            getattr(absent_detail, "proprietary_parties_evidence_hash", None)
        )
        self._assert_exact_accounting_amount(absent_entry, absent_detail)
        self.assertEqual(
            self.base_statement.account_identifier_hash,
            self.absent_statement.account_identifier_hash,
        )
        self.assertNotEqual(base_detail.source_detail_hash, absent_detail.source_detail_hash)
        self.assertNotEqual(base_entry.source_entry_hash, absent_entry.source_entry_hash)
        self.assertNotEqual(
            self.base_statement.normalized_payload_hash,
            self.absent_statement.normalized_payload_hash,
        )
        self.assertEqual(
            self.base_statement.entries[1].source_entry_hash,
            self.absent_statement.entries[1].source_entry_hash,
        )

    def test_xml_layout_does_not_change_proprietary_party_semantics(self) -> None:
        """Whitespace inside repeated Prtry changes raw bytes, not admitted semantics."""
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
            getattr(base_detail, "proprietary_parties_evidence_hash", None),
            expected,
        )
        self.assertEqual(
            getattr(reformatted_detail, "proprietary_parties_evidence_hash", None),
            expected,
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

    def test_changed_proprietary_parties_require_explicit_correction(self) -> None:
        """Accepted proprietary-party provenance cannot be replaced by silent replay."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        payloads = dict(self.variant_payloads)
        payloads["population-absent"] = self.absent_payload
        for name, payload in payloads.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                    accept_bank_statement_evidence(
                        self._command(payload, name),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_buyer_read_preserves_proprietary_parties_without_changing_amount(self) -> None:
        """Tenant reads expose proprietary-party provenance while 25000 KRW stays authoritative."""
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

        self.assertEqual(
            detail.get("proprietary_parties_evidence_hash"),
            expected_hash,
        )
        self.assertEqual(detail.get("proprietary_parties"), self.base)
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    @staticmethod
    def _variants(
        base: list[dict[str, object]],
    ) -> dict[str, list[dict[str, object]]]:
        """Change type, nested choice, repeated population, and source order independently."""
        variants: dict[str, list[dict[str, object]]] = {}

        first_type = copy.deepcopy(base)
        first_type[0]["type"] = "EXECUTION_BROKER"
        variants["first-type"] = first_type

        first_name = copy.deepcopy(base)
        first_party = first_name[0].get("party")
        if not isinstance(first_party, dict):
            raise AssertionError("first proprietary party requires Party50Choice")
        first_party["name"] = "Execution Broker Beta"
        variants["first-party-name"] = first_name

        same_name_agent = copy.deepcopy(base)
        first_party = same_name_agent[0].get("party")
        if not isinstance(first_party, dict):
            raise AssertionError("choice variant requires Party50Choice")
        first_party["choice"] = "agent"
        variants["first-choice-same-name"] = same_name_agent

        second_name = copy.deepcopy(base)
        second_party = second_name[1].get("party")
        if not isinstance(second_party, dict):
            raise AssertionError("second proprietary party requires Party50Choice")
        second_party["name"] = "Custodian Beta"
        variants["second-party-name"] = second_name

        second_removed = copy.deepcopy(base)
        second_removed.pop()
        variants["second-item-removed"] = second_removed

        reversed_order = copy.deepcopy(base)
        reversed_order.reverse()
        variants["source-order"] = reversed_order
        return variants

    @staticmethod
    def _assert_exact_accounting_amount(entry: object, detail: object) -> None:
        """Pin accounting truth independently from proprietary reconciliation provenance."""
        if getattr(entry, "entry_amount", None) != Decimal("25000.00"):
            raise AssertionError(
                "proprietary-party evidence must retain exact 25000.00 entry amount"
            )
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("proprietary-party evidence must retain KRW entry currency")
        if getattr(detail, "detail_amount", None) != Decimal("25000.00"):
            raise AssertionError(
                "proprietary-party evidence must retain exact 25000.00 detail amount"
            )
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("proprietary-party evidence must retain KRW detail currency")

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Require source_entry_hash to digest the proprietary-parties evidence hash."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        first_detail = details[0]
        if not isinstance(first_detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if first_detail.get("proprietary_parties_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry exact proprietary_parties_evidence_hash"
            )
        preimage = json.dumps(
            projection, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if getattr(entry, "source_entry_hash", None) != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must digest the canonical entry projection containing "
                "proprietary_parties_evidence_hash"
            )

    @staticmethod
    def _expected_hash(value: list[dict[str, object]]) -> str:
        """Digest the complete source-ordered ProprietaryParty6 population."""
        preimage = json.dumps(
            {
                "evidence_type": _PROPRIETARY_PARTIES_PURPOSE,
                "proprietary_parties": value,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @classmethod
    def _proprietary_parties_xml(cls, value: list[dict[str, object]]) -> str:
        """Serialize source-ordered TransactionParties12/Prtry elements."""
        return "".join(cls._proprietary_party_xml(item) for item in value)

    @staticmethod
    def _proprietary_party_xml(value: dict[str, object]) -> str:
        """Serialize one valid ProprietaryParty6 with required Tp and Party50Choice."""
        party_type = value.get("type")
        party = value.get("party")
        if not isinstance(party_type, str) or not party_type:
            raise AssertionError("ProprietaryParty6 requires Tp")
        if not isinstance(party, dict):
            raise AssertionError("ProprietaryParty6 requires Pty/Party50Choice")
        choice = party.get("choice")
        name = party.get("name")
        if not isinstance(name, str) or not name:
            raise AssertionError("test Party50Choice requires a non-empty name")

        if choice == "party":
            choice_xml = (
                "                  <Pty>\n"
                f"                    <Nm>{name}</Nm>\n"
                "                  </Pty>\n"
            )
        elif choice == "agent":
            choice_xml = (
                "                  <Agt>\n"
                "                    <FinInstnId>\n"
                f"                      <Nm>{name}</Nm>\n"
                "                    </FinInstnId>\n"
                "                  </Agt>\n"
            )
        else:
            raise AssertionError(f"unsupported Party50Choice branch: {choice}")

        return (
            "              <Prtry>\n"
            f"                <Tp>{party_type}</Tp>\n"
            "                <Pty>\n"
            + choice_xml
            + "                </Pty>\n"
            "              </Prtry>\n"
        )

    @classmethod
    def _with_proprietary_parties(
        cls,
        fixture: str,
        marker: str,
        value: list[dict[str, object]],
    ) -> bytes:
        """Insert repeated Prtry after the fixture debtor and before RltdPties closes."""
        return fixture.replace(
            marker,
            "              </Dbtr>\n"
            + cls._proprietary_parties_xml(value)
            + "            </RltdPties>",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"detail-proprietary-parties-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
