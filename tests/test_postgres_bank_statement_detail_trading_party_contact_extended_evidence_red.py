"""PostgreSQL REDs for extended trading-party Contact13 evidence."""

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


class BankStatementDetailTradingPartyExtendedContactEvidenceRedTests(unittest.TestCase):
    """Retain the remaining Contact13 fields as evidence, never accounting authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare extended Contact13 value and representation-control variants."""
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
            "contact_details": {
                "name_prefix": "MIST",
                "name": "Treasury Operations",
                "phone_number": "+82-2-555-0101",
                "mobile_number": "+82-10-5555-0101",
                "fax_number": "+82-2-555-0199",
                "url_address": "https://example.com/treasury",
                "email_address": "treasury@example.com",
                "email_purpose": "SETTLEMENT",
                "job_title": "Treasury Manager",
                "responsibility": "Cash Management",
                "department": "Treasury",
                "other_contacts": [
                    {"channel_type": "CHAT", "identification": "treasury-chat"},
                    {"channel_type": "SWIF", "identification": "swift-ops"},
                ],
                "preferred_method": "MAIL",
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
            "                    <Othr>\n",
            "                    <Othr>\n                      \n",
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

    def test_extended_contact_values_are_material_to_evidence_identity(self) -> None:
        """Every admitted Contact13 field independently changes retained evidence identity."""
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
                entry = statement.entries[0]
                detail = entry.entry_details[0]
                expected_hash = variant_hashes[name]
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

    def test_xml_layout_does_not_change_extended_contact_semantics(self) -> None:
        """Whitespace inside OtherContact1 changes raw bytes, not semantic identity."""
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
        self.assertEqual(base_detail.source_detail_hash, reformatted_detail.source_detail_hash)
        self.assertEqual(base_entry.source_entry_hash, reformatted_entry.source_entry_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_extended_contact_requires_explicit_statement_correction(self) -> None:
        """Accepted extended contact evidence cannot be replaced by silent replay."""
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

    def test_buyer_read_preserves_extended_contact_without_changing_amount_truth(self) -> None:
        """Tenant reads expose complete Contact13 evidence while 25000 KRW stays fixed."""
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
    def _variants(base: dict[str, object]) -> dict[str, dict[str, object]]:
        """Return one-field Contact13 variants, including repeated OtherContact1 evidence."""
        replacements: dict[str, object] = {
            "name_prefix": "MADM",
            "mobile_number": "+82-10-5555-0102",
            "fax_number": "+82-2-555-0198",
            "url_address": "https://example.com/settlement",
            "email_purpose": "OPERATIONS",
            "job_title": "Settlement Manager",
            "responsibility": "Liquidity Management",
            "department": "Settlement",
            "preferred_method": "PHON",
        }
        variants: dict[str, dict[str, object]] = {}
        for field, replacement in replacements.items():
            value = copy.deepcopy(base)
            contact = value.get("contact_details")
            if not isinstance(contact, dict):
                raise AssertionError("contact_details must be a mapping")
            contact[field] = replacement
            variants[field.replace("_", "-")] = value

        channel_value = copy.deepcopy(base)
        channel_contact = channel_value.get("contact_details")
        if not isinstance(channel_contact, dict):
            raise AssertionError("contact_details must be a mapping")
        channel_other = channel_contact.get("other_contacts")
        if not isinstance(channel_other, list) or not channel_other:
            raise AssertionError("other_contacts must contain evidence")
        channel_other[0]["channel_type"] = "TELE"
        variants["other-channel-type"] = channel_value

        id_value = copy.deepcopy(base)
        id_contact = id_value.get("contact_details")
        if not isinstance(id_contact, dict):
            raise AssertionError("contact_details must be a mapping")
        id_other = id_contact.get("other_contacts")
        if not isinstance(id_other, list) or not id_other:
            raise AssertionError("other_contacts must contain evidence")
        id_other[0]["identification"] = "treasury-chat-2"
        variants["other-identification"] = id_value

        id_absent = copy.deepcopy(base)
        absent_contact = id_absent.get("contact_details")
        if not isinstance(absent_contact, dict):
            raise AssertionError("contact_details must be a mapping")
        absent_other = absent_contact.get("other_contacts")
        if not isinstance(absent_other, list) or not absent_other:
            raise AssertionError("other_contacts must contain evidence")
        absent_other[0].pop("identification")
        variants["other-identification-absent"] = id_absent

        repeated_absent = copy.deepcopy(base)
        repeated_contact = repeated_absent.get("contact_details")
        if not isinstance(repeated_contact, dict):
            raise AssertionError("contact_details must be a mapping")
        repeated_other = repeated_contact.get("other_contacts")
        if not isinstance(repeated_other, list) or len(repeated_other) != 2:
            raise AssertionError("base other_contacts must contain two values")
        repeated_contact["other_contacts"] = repeated_other[:1]
        variants["other-second-absent"] = repeated_absent

        return variants

    @staticmethod
    def _assert_accounting_truth(entry: object, detail: object) -> None:
        """Pin bank-reported contact evidence away from authoritative accounting amount."""
        if getattr(entry, "entry_amount", None) != Decimal("25000.00"):
            raise AssertionError("trading-party contact evidence must not alter entry amount")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("trading-party contact evidence must not alter entry currency")
        if getattr(detail, "detail_amount", None) != Decimal("25000.00"):
            raise AssertionError("trading-party contact evidence must not alter detail amount")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("trading-party contact evidence must not alter detail currency")

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
                "canonical entry projection must carry the exact trading_party_evidence_hash"
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
        """Digest the admitted PartyIdentification272 extended-contact projection."""
        preimage = json.dumps(
            {
                "evidence_type": _TRADING_PARTY_PURPOSE,
                "trading_party": value,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _trading_party_xml(value: dict[str, object]) -> str:
        """Serialize one PartyIdentification272 with the complete tested Contact13 subset."""
        if value.get("choice") != "party":
            raise AssertionError("extended-contact RED requires the Pty branch")
        name = str(value["name"])
        contact = value.get("contact_details")
        if not isinstance(contact, dict):
            raise AssertionError("contact_details must be a mapping")
        other_contacts = contact.get("other_contacts")
        if not isinstance(other_contacts, list) or not other_contacts:
            raise AssertionError("other_contacts must contain evidence")

        other_xml = ""
        for other in other_contacts:
            if not isinstance(other, dict):
                raise AssertionError("OtherContact1 must be a mapping")
            identification = other.get("identification")
            identification_xml = (
                f"                      <Id>{identification}</Id>\n"
                if identification is not None
                else ""
            )
            other_xml += (
                "                    <Othr>\n"
                f"                      <ChanlTp>{other['channel_type']}</ChanlTp>\n"
                + identification_xml
                + "                    </Othr>\n"
            )

        contact_xml = (
            "                  <CtctDtls>\n"
            f"                    <NmPrfx>{contact['name_prefix']}</NmPrfx>\n"
            f"                    <Nm>{contact['name']}</Nm>\n"
            f"                    <PhneNb>{contact['phone_number']}</PhneNb>\n"
            f"                    <MobNb>{contact['mobile_number']}</MobNb>\n"
            f"                    <FaxNb>{contact['fax_number']}</FaxNb>\n"
            f"                    <URLAdr>{contact['url_address']}</URLAdr>\n"
            f"                    <EmailAdr>{contact['email_address']}</EmailAdr>\n"
            f"                    <EmailPurp>{contact['email_purpose']}</EmailPurp>\n"
            f"                    <JobTitl>{contact['job_title']}</JobTitl>\n"
            f"                    <Rspnsblty>{contact['responsibility']}</Rspnsblty>\n"
            f"                    <Dept>{contact['department']}</Dept>\n"
            + other_xml
            + f"                    <PrefrdMtd>{contact['preferred_method']}</PrefrdMtd>\n"
            "                  </CtctDtls>\n"
        )
        return (
            "              <TradgPty>\n"
            "                <Pty>\n"
            f"                  <Nm>{name}</Nm>\n"
            + contact_xml
            + "                </Pty>\n"
            "              </TradgPty>\n"
        )

    @classmethod
    def _with_trading_party(
        cls,
        fixture: str,
        marker: str,
        value: dict[str, object],
    ) -> bytes:
        """Insert Contact13-bearing trading-party evidence after fixture debtor evidence."""
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
                f"detail-trading-party-contact-extended-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
