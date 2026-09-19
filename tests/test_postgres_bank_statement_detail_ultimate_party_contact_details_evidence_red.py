"""PostgreSQL REDs for ultimate-party Contact13 evidence in camt.053 details."""

from __future__ import annotations

import copy
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
    test_postgres_bank_statement_debtor_creditor_party_contact_details_evidence_red
    as direct_contact,
)
from tests import (
    test_postgres_bank_statement_detail_ultimate_party_identification_evidence_red
    as ultimate_identity,
)
from tests import test_postgres_posting as posting

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailUltimatePartyContactDetailsEvidenceRedTests(unittest.TestCase):
    """Retain ultimate-party Contact13 as purpose-bound non-reversible evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        (
            ultimate_identity.BankStatementDetailUltimatePartyIdentificationEvidenceRedTests
            .setUpClass()
        )

    def setUp(self) -> None:
        """Prepare one nested ultimate-party fixture with exception-safe cleanup."""
        self.case = (
            ultimate_identity.BankStatementDetailUltimatePartyIdentificationEvidenceRedTests(
                "setUp"
            )
        )
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.base_contact = (
            direct_contact.BankStatementDebtorCreditorPartyContactDetailsEvidenceRedTests
            ._complete_contact()
        )
        self.variants = self._contact_variants(self.base_contact)

    def test_contact_values_presence_population_and_order_are_material_for_each_role(
        self,
    ) -> None:
        """Every Contact13 scalar, optional child, repeated Othr value and order is material."""
        for role in ("ultimate_debtor", "ultimate_creditor"):
            baseline = parse_bank_statement_payload(
                self._with_role_contact(role, self.base_contact),
                CAMT053_MESSAGE_DEFINITION,
            )
            baseline_entry = baseline.entries[0]
            baseline_detail = baseline_entry.entry_details[0]
            evidence_key = self.case._evidence_key(role)
            self.case._assert_financial_truth(baseline_entry, baseline_detail)
            self._assert_semantic_hashes(
                baseline,
                baseline_entry,
                baseline_detail,
                baseline.entries[1],
                evidence_key,
            )

            for semantic, contact in self.variants.items():
                with self.subTest(role=role, semantic=semantic):
                    changed = parse_bank_statement_payload(
                        self._with_role_contact(role, contact),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    changed_entry = changed.entries[0]
                    changed_detail = changed_entry.entry_details[0]
                    self.case._assert_financial_truth(changed_entry, changed_detail)
                    self._assert_semantic_hashes(
                        changed,
                        changed_entry,
                        changed_detail,
                        changed.entries[1],
                        evidence_key,
                    )

                    self.assertNotEqual(
                        getattr(baseline_detail, evidence_key),
                        getattr(changed_detail, evidence_key),
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
                        baseline.entries[1].source_entry_hash,
                        changed.entries[1].source_entry_hash,
                    )

    def test_contact_xml_layout_is_representation_only_for_each_role(self) -> None:
        """Whitespace inside CtctDtls changes raw bytes without changing semantics."""
        needle = b"                  <CtctDtls>\n"
        whitespace = b"                    \n"

        for role in ("ultimate_debtor", "ultimate_creditor"):
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
                left_entry = left.entries[0]
                right_entry = right.entries[0]
                left_detail = left_entry.entry_details[0]
                right_detail = right_entry.entry_details[0]
                evidence_key = self.case._evidence_key(role)
                self.case._assert_financial_truth(left_entry, left_detail)
                self.case._assert_financial_truth(right_entry, right_detail)

                for value in (
                    left.source_artifact_hash,
                    right.source_artifact_hash,
                    getattr(left_detail, evidence_key),
                    getattr(right_detail, evidence_key),
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
                    getattr(left_detail, evidence_key),
                    getattr(right_detail, evidence_key),
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

    def test_all_contact_changes_reach_complete_correction_boundary_for_each_role(
        self,
    ) -> None:
        """Accepted ultimate-party Contact13 provenance cannot be silently replaced."""
        for role in ("ultimate_debtor", "ultimate_creditor"):
            baseline_payload = self._with_role_contact(role, self.base_contact)
            reference = self.case._register_statement_account(baseline_payload)
            store = MemoryArtifactStore()
            accepted = accept_bank_statement_evidence(
                self.case._command(
                    baseline_payload,
                    reference,
                    f"{role}-contact-baseline",
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
                                f"{role}-contact-{semantic}",
                            ),
                            posting.DATABASE_URL,
                            self.case.case.policy.tenant_reference,
                            artifact_store=store,
                        )

    def test_buyer_projection_keeps_all_contact_values_non_reversible(self) -> None:
        """Contact PII changes internal evidence without source-value disclosure."""
        sensitive = copy.deepcopy(self.base_contact)
        sensitive.update(
            {
                "name_prefix": "MADM",
                "name": "Ultimate Confidential Treasury Contact",
                "phone_number": "+49-30-555-9911",
                "mobile_number": "+49-171-555-9911",
                "fax_number": "+49-30-555-9912",
                "url_address": "https://ultimate-sensitive.example.invalid/contact",
                "email_address": "ultimate-private@example.invalid",
                "email_purpose": "CONFIDENTIAL",
                "job_title": "Ultimate Restricted Treasury Role",
                "responsibility": "Ultimate Restricted Cash Operations",
                "department": "Ultimate Restricted Treasury Unit",
                "other_contacts": [
                    {"channel_type": "TELE", "identification": "ultimate-secret-one"},
                    {"channel_type": "SWIF", "identification": "ultimate-secret-two"},
                ],
                "preferred_method": "PHON",
            }
        )
        source_values = self._source_scalar_values(sensitive)

        for role in ("ultimate_debtor", "ultimate_creditor"):
            with self.subTest(role=role):
                baseline = self.case._ingest_and_read_first_detail(
                    self._with_role_contact(role, None),
                    f"{role}-contact-none-{uuid.uuid4().hex}",
                )
                rich = self.case._ingest_and_read_first_detail(
                    self._with_role_contact(role, sensitive),
                    f"{role}-contact-rich-{uuid.uuid4().hex}",
                )
                evidence_key = self.case._evidence_key(role)

                for projection in (baseline, rich):
                    self.case._assert_sha256(projection[evidence_key])
                    self.case._assert_sha256(projection["source_detail_hash"])
                    self.assertEqual(
                        Decimal(str(projection["detail_amount"])),
                        Decimal("25000.00"),
                    )
                    self.assertEqual(projection["detail_currency_code"], "KRW")

                self.assertNotEqual(baseline[evidence_key], rich[evidence_key])
                self.assertNotEqual(
                    baseline["source_detail_hash"],
                    rich["source_detail_hash"],
                )
                baseline_public = self.case._public_projection(baseline, evidence_key)
                rich_public = self.case._public_projection(rich, evidence_key)
                self.assertEqual(baseline_public, rich_public)
                buyer_scalars = self._scalar_values(rich_public)
                for source_value in source_values:
                    with self.subTest(role=role, source_value=source_value):
                        self.assertNotIn(source_value, buyer_scalars)

    def _with_role_contact(
        self,
        role: str,
        contact: dict[str, object] | None,
    ) -> bytes:
        """Return one ultimate Pty branch with optional Contact13 after Id."""
        payload = self.case._with_ultimate_party(
            role,
            "organisation",
            self.case.base_identifier,
        )
        if contact is None:
            return payload

        marker = b"                  </Id>\n                </Pty>"
        if payload.count(marker) != 1:
            raise AssertionError("target ultimate-party Id/Pty sequence must be unique")
        contact_xml = (
            direct_contact.BankStatementDebtorCreditorPartyContactDetailsEvidenceRedTests
            ._contact_xml(contact)
        )
        return payload.replace(
            marker,
            b"                  </Id>\n" + contact_xml + b"                </Pty>",
            1,
        )

    @classmethod
    def _contact_variants(
        cls,
        base: dict[str, object],
    ) -> dict[str, dict[str, object] | None]:
        """Extend canonical Contact13 variants through later repeated-Othr scalars."""
        variants = (
            direct_contact.BankStatementDebtorCreditorPartyContactDetailsEvidenceRedTests
            ._contact_variants(base)
        )

        second_channel_changed = copy.deepcopy(base)
        second_channel_contacts = second_channel_changed.get("other_contacts")
        if not isinstance(second_channel_contacts, list) or len(second_channel_contacts) != 2:
            raise AssertionError("Contact13 RED requires two Othr values")
        second_channel = second_channel_contacts[1]
        if not isinstance(second_channel, dict):
            raise AssertionError("second Contact13 Othr must be a mapping")
        second_channel["channel_type"] = "TELE"
        variants["second-other-channel-type-value"] = second_channel_changed

        second_identification_changed = copy.deepcopy(base)
        second_identification_contacts = second_identification_changed.get("other_contacts")
        if not isinstance(second_identification_contacts, list) or len(second_identification_contacts) != 2:
            raise AssertionError("Contact13 RED requires two Othr values")
        second_identification = second_identification_contacts[1]
        if not isinstance(second_identification, dict):
            raise AssertionError("second Contact13 Othr must be a mapping")
        second_identification["identification"] = "swift-ops-2"
        variants["second-other-identification-value"] = second_identification_changed

        second_identification_absent = copy.deepcopy(base)
        second_absent_contacts = second_identification_absent.get("other_contacts")
        if not isinstance(second_absent_contacts, list) or len(second_absent_contacts) != 2:
            raise AssertionError("Contact13 RED requires two Othr values")
        second_absent = second_absent_contacts[1]
        if not isinstance(second_absent, dict):
            raise AssertionError("second Contact13 Othr must be a mapping")
        second_absent.pop("identification")
        variants["second-other-identification-absent"] = second_identification_absent
        return variants

    def _assert_semantic_hashes(
        self,
        statement: object,
        entry: object,
        detail: object,
        sibling: object,
        evidence_key: str,
    ) -> None:
        """Require canonical identities before materiality or stability comparisons."""
        for value in (
            getattr(detail, evidence_key),
            getattr(detail, "source_detail_hash"),
            getattr(entry, "source_entry_hash"),
            getattr(statement, "normalized_payload_hash"),
            getattr(statement, "account_identifier_hash"),
            getattr(sibling, "source_entry_hash"),
        ):
            self.case._assert_sha256(value)

    @classmethod
    def _source_scalar_values(cls, value: object) -> set[str]:
        """Collect every admitted source scalar from the rich Contact13 value."""
        if isinstance(value, dict):
            collected: set[str] = set()
            for nested in value.values():
                collected.update(cls._source_scalar_values(nested))
            return collected
        if isinstance(value, list):
            collected = set()
            for nested in value:
                collected.update(cls._source_scalar_values(nested))
            return collected
        if value is None:
            return set()
        if not isinstance(value, str):
            raise AssertionError("Contact13 RED source scalar must be a string")
        return {value}

    @classmethod
    def _scalar_values(cls, value: object) -> set[str]:
        """Collect exact buyer-visible scalar leaves for privacy assertions."""
        if isinstance(value, dict):
            collected: set[str] = set()
            for nested in value.values():
                collected.update(cls._scalar_values(nested))
            return collected
        if isinstance(value, list):
            collected = set()
            for nested in value:
                collected.update(cls._scalar_values(nested))
            return collected
        if value is None:
            return set()
        return {str(value)}


if __name__ == "__main__":
    unittest.main()
