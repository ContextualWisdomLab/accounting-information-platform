"""PostgreSQL REDs for direct debtor/creditor PartyIdentification272 Contact13 evidence."""

from __future__ import annotations

import copy
import json
import unittest
import uuid
from decimal import Decimal

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_statement_evidence,
    parse_bank_statement_payload,
)
from tests import (
    test_postgres_bank_statement_debtor_creditor_party_choice_evidence_red as party_choice,
)
from tests import test_postgres_posting as posting

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDebtorCreditorPartyContactDetailsEvidenceRedTests(unittest.TestCase):
    """Retain direct-party Contact13 as purpose-bound non-reversible evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        party_choice.BankStatementDebtorCreditorPartyChoiceEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one direct-party helper with exception-safe nested cleanup."""
        self.case = party_choice.BankStatementDebtorCreditorPartyChoiceEvidenceRedTests(
            "setUp"
        )
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.party_name = "Direct Party Contact Evidence"
        self.base_contact = self._complete_contact()
        self.variants = self._contact_variants(self.base_contact)

    def test_contact_fields_presence_population_and_order_are_material(self) -> None:
        """Every admitted Contact13 field and repeated Othr population changes identity."""
        for role in ("debtor", "creditor"):
            target_index = self.case._target_entry_index(role)
            untouched_index = 1 - target_index
            baseline = parse_bank_statement_payload(
                self._with_role_contact(role, self.base_contact),
                CAMT053_MESSAGE_DEFINITION,
            )
            baseline_entry = baseline.entries[target_index]
            baseline_detail = baseline_entry.entry_details[0]
            self._assert_financial_truth(role, baseline_entry, baseline_detail)
            self._assert_semantic_hashes(
                baseline,
                baseline_entry,
                baseline_detail,
                baseline.entries[untouched_index],
            )

            for semantic, contact in self.variants.items():
                with self.subTest(role=role, semantic=semantic):
                    changed = parse_bank_statement_payload(
                        self._with_role_contact(role, contact),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    changed_entry = changed.entries[target_index]
                    changed_detail = changed_entry.entry_details[0]
                    self._assert_financial_truth(role, changed_entry, changed_detail)
                    self._assert_semantic_hashes(
                        changed,
                        changed_entry,
                        changed_detail,
                        changed.entries[untouched_index],
                    )

                    self.assertNotEqual(
                        baseline_entry.counterparty_evidence_hash,
                        changed_entry.counterparty_evidence_hash,
                    )
                    self.assertNotEqual(
                        baseline_detail.source_detail_hash,
                        changed_detail.source_detail_hash,
                    )
                    self.assertNotEqual(
                        baseline_entry.source_entry_hash,
                        changed_entry.source_entry_hash,
                    )
                    self.assertNotEqual(
                        baseline.normalized_payload_hash,
                        changed.normalized_payload_hash,
                    )
                    self.assertEqual(
                        baseline.account_identifier_hash,
                        changed.account_identifier_hash,
                    )
                    self.assertEqual(
                        baseline.entries[untouched_index].source_entry_hash,
                        changed.entries[untouched_index].source_entry_hash,
                    )

    def test_contact_xml_layout_is_representation_only_for_both_roles(self) -> None:
        """Whitespace inside CtctDtls changes raw bytes without changing semantics."""
        needle = b"                  <CtctDtls>\n"
        whitespace = b"                    \n"

        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                baseline_payload = self._with_role_contact(role, self.base_contact)
                self.assertEqual(baseline_payload.count(needle), 1)
                formatted_payload = baseline_payload.replace(
                    needle,
                    needle + whitespace,
                    1,
                )
                self.assertNotEqual(baseline_payload, formatted_payload)

                left = parse_bank_statement_payload(
                    baseline_payload,
                    CAMT053_MESSAGE_DEFINITION,
                )
                right = parse_bank_statement_payload(
                    formatted_payload,
                    CAMT053_MESSAGE_DEFINITION,
                )
                target_index = self.case._target_entry_index(role)
                left_entry = left.entries[target_index]
                right_entry = right.entries[target_index]
                left_detail = left_entry.entry_details[0]
                right_detail = right_entry.entry_details[0]
                self._assert_financial_truth(role, left_entry, left_detail)
                self._assert_financial_truth(role, right_entry, right_detail)

                for value in (
                    left.source_artifact_hash,
                    right.source_artifact_hash,
                    left_entry.counterparty_evidence_hash,
                    right_entry.counterparty_evidence_hash,
                    left_detail.source_detail_hash,
                    right_detail.source_detail_hash,
                    left_entry.source_entry_hash,
                    right_entry.source_entry_hash,
                    left.normalized_payload_hash,
                    right.normalized_payload_hash,
                ):
                    self.case._assert_sha256(value)

                self.assertNotEqual(left.source_artifact_hash, right.source_artifact_hash)
                self.assertEqual(
                    left_entry.counterparty_evidence_hash,
                    right_entry.counterparty_evidence_hash,
                )
                self.assertEqual(
                    left_detail.source_detail_hash,
                    right_detail.source_detail_hash,
                )
                self.assertEqual(left_entry.source_entry_hash, right_entry.source_entry_hash)
                self.assertEqual(
                    left.normalized_payload_hash,
                    right.normalized_payload_hash,
                )

    def test_all_contact_changes_reach_complete_correction_boundary(self) -> None:
        """Accepted Contact13 provenance cannot be silently replaced by replay."""
        for role in ("debtor", "creditor"):
            baseline_payload = self._with_role_contact(role, self.base_contact)
            reference = self.case._register_statement_account(baseline_payload)
            store = MemoryArtifactStore()
            accepted = accept_bank_statement_evidence(
                self.case._command(
                    baseline_payload,
                    reference,
                    f"{role}-party-contact-baseline",
                ),
                posting.DATABASE_URL,
                self.case.case.policy.tenant_reference,
                artifact_store=store,
            )
            self.assertFalse(accepted["replayed"])

            for semantic, contact in self.variants.items():
                with self.subTest(role=role, semantic=semantic):
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        _CORRECTION_ERROR,
                    ):
                        accept_bank_statement_evidence(
                            self.case._command(
                                self._with_role_contact(role, contact),
                                reference,
                                f"{role}-party-contact-{semantic}",
                            ),
                            posting.DATABASE_URL,
                            self.case.case.policy.tenant_reference,
                            artifact_store=store,
                        )

    def test_buyer_projection_keeps_contact_details_non_reversible(self) -> None:
        """Contact PII changes internal evidence without adding tenant-visible fields."""
        sensitive = copy.deepcopy(self.base_contact)
        sensitive.update(
            {
                "name": "Confidential Treasury Contact",
                "phone_number": "+49-30-555-9911",
                "mobile_number": "+49-171-555-9911",
                "fax_number": "+49-30-555-9912",
                "url_address": "https://sensitive.example.invalid/contact",
                "email_address": "private-contact@example.invalid",
                "email_purpose": "CONFIDENTIAL",
                "job_title": "Restricted Treasury Role",
                "responsibility": "Restricted Cash Operations",
                "department": "Restricted Treasury Unit",
                "other_contacts": [
                    {"channel_type": "CHAT", "identification": "secret-channel-one"},
                    {"channel_type": "SWIF", "identification": "secret-channel-two"},
                ],
            }
        )

        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                baseline = self.case._ingest_and_read_target_entry(
                    role,
                    self._with_role_contact(role, None),
                    f"{role}-party-contact-none-{uuid.uuid4().hex}",
                )
                rich = self.case._ingest_and_read_target_entry(
                    role,
                    self._with_role_contact(role, sensitive),
                    f"{role}-party-contact-rich-{uuid.uuid4().hex}",
                )

                for projection in (baseline, rich):
                    self.case._assert_sha256(projection["counterparty_evidence_hash"])
                    self.case._assert_sha256(projection["source_entry_hash"])
                    self.assertEqual(
                        Decimal(str(projection["entry_amount"])),
                        self.case._expected_entry_amount(role),
                    )
                    self.assertEqual(projection["entry_currency_code"], "KRW")
                    first_detail = projection["entry_details"][0]
                    self.case._assert_sha256(first_detail["source_detail_hash"])
                    self.assertEqual(
                        Decimal(str(first_detail["detail_amount"])),
                        self.case._expected_detail_amount(role),
                    )
                    self.assertEqual(first_detail["detail_currency_code"], "KRW")

                self.assertNotEqual(
                    baseline["counterparty_evidence_hash"],
                    rich["counterparty_evidence_hash"],
                )
                self.assertNotEqual(
                    baseline["entry_details"][0]["source_detail_hash"],
                    rich["entry_details"][0]["source_detail_hash"],
                )
                self.assertNotEqual(baseline["source_entry_hash"], rich["source_entry_hash"])
                self.assertEqual(
                    self._public_projection(baseline),
                    self._public_projection(rich),
                )

                serialized = json.dumps(rich, sort_keys=True, default=str)
                for source_value in (
                    "Confidential Treasury Contact",
                    "+49-30-555-9911",
                    "+49-171-555-9911",
                    "+49-30-555-9912",
                    "sensitive.example.invalid",
                    "private-contact@example.invalid",
                    "CONFIDENTIAL",
                    "Restricted Treasury Role",
                    "Restricted Cash Operations",
                    "Restricted Treasury Unit",
                    "secret-channel-one",
                    "secret-channel-two",
                ):
                    self.assertNotIn(source_value, serialized)

    def _with_role_contact(
        self,
        role: str,
        contact: dict[str, object] | None,
    ) -> bytes:
        """Return one direct debtor/creditor Pty branch with optional Contact13."""
        payload = self.case._with_role_party(role, "Pty", self.party_name)
        if contact is None:
            return payload

        name_marker = f"                  <Nm>{self.party_name}</Nm>\n".encode("utf-8")
        if payload.count(name_marker) != 1:
            raise AssertionError("target direct-party name must be unique")
        return payload.replace(
            name_marker,
            name_marker + self._contact_xml(contact),
            1,
        )

    @staticmethod
    def _complete_contact() -> dict[str, object]:
        """Return one schema-ordered Contact13 value covering every admitted field."""
        return {
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
        }

    @classmethod
    def _contact_variants(
        cls,
        base: dict[str, object],
    ) -> dict[str, dict[str, object] | None]:
        """Change and remove every optional Contact13 field and repeated Othr population."""
        replacements: dict[str, object] = {
            "name_prefix": "MADM",
            "name": "Settlement Operations",
            "phone_number": "+82-2-555-0102",
            "mobile_number": "+82-10-5555-0102",
            "fax_number": "+82-2-555-0198",
            "url_address": "https://example.com/settlement",
            "email_address": "settlement@example.com",
            "email_purpose": "OPERATIONS",
            "job_title": "Settlement Manager",
            "responsibility": "Liquidity Management",
            "department": "Settlement",
            "preferred_method": "PHON",
        }
        variants: dict[str, dict[str, object] | None] = {}
        for field, replacement in replacements.items():
            changed = copy.deepcopy(base)
            changed[field] = replacement
            variants[f"{field}-value"] = changed

        for field in replacements:
            absent = copy.deepcopy(base)
            absent.pop(field)
            variants[f"{field}-absent"] = absent

        channel_changed = copy.deepcopy(base)
        channel_contacts = channel_changed.get("other_contacts")
        if not isinstance(channel_contacts, list) or len(channel_contacts) != 2:
            raise AssertionError("Contact13 RED requires two Othr values")
        channel_contacts[0]["channel_type"] = "TELE"
        variants["other-channel-type-value"] = channel_changed

        identification_changed = copy.deepcopy(base)
        identification_contacts = identification_changed.get("other_contacts")
        if not isinstance(identification_contacts, list) or len(identification_contacts) != 2:
            raise AssertionError("Contact13 RED requires two Othr values")
        identification_contacts[0]["identification"] = "treasury-chat-2"
        variants["other-identification-value"] = identification_changed

        identification_absent = copy.deepcopy(base)
        absent_contacts = identification_absent.get("other_contacts")
        if not isinstance(absent_contacts, list) or len(absent_contacts) != 2:
            raise AssertionError("Contact13 RED requires two Othr values")
        absent_contacts[0].pop("identification")
        variants["other-identification-absent"] = identification_absent

        first_absent = copy.deepcopy(base)
        first_contacts = first_absent.get("other_contacts")
        if not isinstance(first_contacts, list) or len(first_contacts) != 2:
            raise AssertionError("Contact13 RED requires two Othr values")
        first_absent["other_contacts"] = first_contacts[1:]
        variants["other-first-absent"] = first_absent

        second_absent = copy.deepcopy(base)
        second_contacts = second_absent.get("other_contacts")
        if not isinstance(second_contacts, list) or len(second_contacts) != 2:
            raise AssertionError("Contact13 RED requires two Othr values")
        second_absent["other_contacts"] = second_contacts[:1]
        variants["other-second-absent"] = second_absent

        none_present = copy.deepcopy(base)
        none_present["other_contacts"] = []
        variants["other-all-absent"] = none_present

        reordered = copy.deepcopy(base)
        reordered_contacts = reordered.get("other_contacts")
        if not isinstance(reordered_contacts, list) or len(reordered_contacts) != 2:
            raise AssertionError("Contact13 RED requires two Othr values")
        reordered_contacts.reverse()
        variants["other-order"] = reordered

        variants["contact-absent"] = None
        return variants

    @staticmethod
    def _contact_xml(contact: dict[str, object]) -> bytes:
        """Serialize Contact13 in schema order, including zero or more Othr values."""
        lines = ["                  <CtctDtls>\n"]
        for field, tag in (
            ("name_prefix", "NmPrfx"),
            ("name", "Nm"),
            ("phone_number", "PhneNb"),
            ("mobile_number", "MobNb"),
            ("fax_number", "FaxNb"),
            ("url_address", "URLAdr"),
            ("email_address", "EmailAdr"),
            ("email_purpose", "EmailPurp"),
            ("job_title", "JobTitl"),
            ("responsibility", "Rspnsblty"),
            ("department", "Dept"),
        ):
            value = contact.get(field)
            if value is not None:
                lines.append(f"                    <{tag}>{value}</{tag}>\n")

        other_contacts = contact.get("other_contacts")
        if not isinstance(other_contacts, list):
            raise AssertionError("Contact13 other_contacts must be a list")
        for other in other_contacts:
            if not isinstance(other, dict):
                raise AssertionError("Contact13 Othr must be a mapping")
            lines.append("                    <Othr>\n")
            lines.append(f"                      <ChanlTp>{other['channel_type']}</ChanlTp>\n")
            identification = other.get("identification")
            if identification is not None:
                lines.append(f"                      <Id>{identification}</Id>\n")
            lines.append("                    </Othr>\n")

        preferred_method = contact.get("preferred_method")
        if preferred_method is not None:
            lines.append(f"                    <PrefrdMtd>{preferred_method}</PrefrdMtd>\n")
        lines.append("                  </CtctDtls>\n")
        return "".join(lines).encode("utf-8")

    def _assert_financial_truth(self, role: str, entry: object, detail: object) -> None:
        """Pin source contact provenance away from authoritative amount and currency truth."""
        self.assertEqual(entry.entry_amount, self.case._expected_entry_amount(role))
        self.assertEqual(entry.entry_currency_code, "KRW")
        self.assertEqual(detail.detail_amount, self.case._expected_detail_amount(role))
        self.assertEqual(detail.detail_currency_code, "KRW")

    def _assert_semantic_hashes(
        self,
        statement: object,
        entry: object,
        detail: object,
        untouched_entry: object,
    ) -> None:
        """Require canonical evidence identities before any equality or inequality claim."""
        for value in (
            statement.source_artifact_hash,
            statement.account_identifier_hash,
            statement.normalized_payload_hash,
            entry.counterparty_evidence_hash,
            entry.source_entry_hash,
            detail.source_detail_hash,
            untouched_entry.source_entry_hash,
        ):
            self.case._assert_sha256(value)

    @staticmethod
    def _public_projection(entry: dict[str, object]) -> dict[str, object]:
        """Remove only server/internal evidence identifiers before privacy comparison."""
        projection = dict(entry)
        projection.pop("bank_statement_entry_id")
        projection.pop("counterparty_evidence_hash")
        projection.pop("source_entry_hash")
        projection["entry_details"] = [
            {
                key: value
                for key, value in dict(detail).items()
                if key != "source_detail_hash"
            }
            for detail in projection["entry_details"]
        ]
        return projection


if __name__ == "__main__":
    unittest.main()
