"""PostgreSQL REDs for direct debtor/creditor PartyIdentification272 postal evidence."""

from __future__ import annotations

import copy
import json
import re
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

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDebtorCreditorPartyPostalAddressEvidenceRedTests(unittest.TestCase):
    """Retain direct-party PostalAddress27 as purpose-bound non-reversible evidence."""

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
        self.party_name = "Direct Party Postal Evidence"
        self.base_postal = self._complete_postal_address()
        self.variants = self._postal_variants(self.base_postal)

    def test_complete_postal_address_fields_and_population_are_material(self) -> None:
        """Every admitted PostalAddress27 field, line order, and presence changes identity."""
        for role in ("debtor", "creditor"):
            target_index = self.case._target_entry_index(role)
            untouched_index = 1 - target_index
            baseline = parse_bank_statement_payload(
                self._with_role_postal(role, self.base_postal),
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

            for semantic, postal in self.variants.items():
                with self.subTest(role=role, semantic=semantic):
                    changed = parse_bank_statement_payload(
                        self._with_role_postal(role, postal),
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

    def test_postal_xml_layout_is_representation_only_for_both_roles(self) -> None:
        """Whitespace inside PstlAdr changes raw bytes without changing admitted semantics."""
        needle = b"                  <PstlAdr>\n"
        whitespace = b"                    \n"

        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                baseline_payload = self._with_role_postal(role, self.base_postal)
                self.assertEqual(baseline_payload.count(needle), 1)
                formatted_payload = baseline_payload.replace(
                    needle,
                    needle + whitespace,
                    1,
                )
                self.assertNotEqual(baseline_payload, formatted_payload)

                left = parse_bank_statement_payload(
                    baseline_payload, CAMT053_MESSAGE_DEFINITION
                )
                right = parse_bank_statement_payload(
                    formatted_payload, CAMT053_MESSAGE_DEFINITION
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

                self.assertNotEqual(
                    left.source_artifact_hash,
                    right.source_artifact_hash,
                )
                self.assertEqual(
                    left_entry.counterparty_evidence_hash,
                    right_entry.counterparty_evidence_hash,
                )
                self.assertEqual(
                    left_detail.source_detail_hash,
                    right_detail.source_detail_hash,
                )
                self.assertEqual(
                    left_entry.source_entry_hash,
                    right_entry.source_entry_hash,
                )
                self.assertEqual(
                    left.normalized_payload_hash,
                    right.normalized_payload_hash,
                )

    def test_postal_changes_reach_complete_correction_boundary(self) -> None:
        """Accepted direct-party postal provenance cannot be silently replaced by replay."""
        for role in ("debtor", "creditor"):
            baseline_payload = self._with_role_postal(role, self.base_postal)
            reference = self.case._register_statement_account(baseline_payload)
            store = MemoryArtifactStore()
            accepted = accept_bank_statement_evidence(
                self.case._command(
                    baseline_payload,
                    reference,
                    f"{role}-party-postal-baseline",
                ),
                posting.DATABASE_URL,
                self.case.case.policy.tenant_reference,
                artifact_store=store,
            )
            self.assertFalse(accepted["replayed"])

            for semantic, postal in self.variants.items():
                with self.subTest(role=role, semantic=semantic):
                    changed_payload = self._with_role_postal(role, postal)
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        _CORRECTION_ERROR,
                    ):
                        accept_bank_statement_evidence(
                            self.case._command(
                                changed_payload,
                                reference,
                                f"{role}-party-postal-{semantic}",
                            ),
                            posting.DATABASE_URL,
                            self.case.case.policy.tenant_reference,
                            artifact_store=store,
                        )

    def test_buyer_projection_keeps_postal_address_non_reversible(self) -> None:
        """Postal PII changes internal evidence without adding reversible tenant fields."""
        sensitive_postal = copy.deepcopy(self.base_postal)
        sensitive_postal.update(
            {
                "care_of": "Confidential Treasury Recipient",
                "street_name": "Sensitive Direct Party Street",
                "building_number": "77",
                "building_name": "Sensitive Direct Party Tower",
                "unit_number": "1901",
                "post_box": "PBOX-7788",
                "room": "Private Treasury Room",
                "post_code": "04567",
                "town_name": "Busan",
                "town_location_name": "Jungang-dong",
                "district_name": "Jung-gu",
                "country_subdivision": "26",
                "country": "DE",
                "address_lines": [
                    "Sensitive Direct Party Line One",
                    "Sensitive Direct Party Line Two",
                ],
            }
        )

        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                baseline = self.case._ingest_and_read_target_entry(
                    role,
                    self._with_role_postal(role, None),
                    f"{role}-party-postal-none-{uuid.uuid4().hex}",
                )
                rich = self.case._ingest_and_read_target_entry(
                    role,
                    self._with_role_postal(role, sensitive_postal),
                    f"{role}-party-postal-rich-{uuid.uuid4().hex}",
                )

                for projection in (baseline, rich):
                    self.case._assert_sha256(
                        projection["counterparty_evidence_hash"]
                    )
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
                self.assertNotEqual(
                    baseline["source_entry_hash"],
                    rich["source_entry_hash"],
                )
                self.assertEqual(
                    self._public_projection(baseline),
                    self._public_projection(rich),
                )

                serialized = json.dumps(rich, sort_keys=True, default=str)
                for source_value in (
                    "Confidential Treasury Recipient",
                    "Sensitive Direct Party Street",
                    "Sensitive Direct Party Tower",
                    "PBOX-7788",
                    "Private Treasury Room",
                    "Busan",
                    "Jungang-dong",
                    "Jung-gu",
                    "Sensitive Direct Party Line One",
                    "Sensitive Direct Party Line Two",
                ):
                    self.assertNotIn(source_value, serialized)

    def _with_role_postal(
        self,
        role: str,
        postal: dict[str, object] | None,
    ) -> bytes:
        """Return one direct debtor/creditor Pty branch with optional PostalAddress27."""
        payload = self.case._with_role_party(role, "Pty", self.party_name)
        if postal is None:
            return payload

        name_marker = f"                  <Nm>{self.party_name}</Nm>\n".encode(
            "utf-8"
        )
        if payload.count(name_marker) != 1:
            raise AssertionError("target direct-party name must be unique")
        return payload.replace(
            name_marker,
            name_marker + self._postal_xml(postal),
            1,
        )

    @staticmethod
    def _complete_postal_address() -> dict[str, object]:
        """Return one schema-ordered PostalAddress27 value covering all fields."""
        return {
            "address_type": {"code": "BIZZ"},
            "care_of": "Treasury Operations",
            "department": "Corporate Treasury",
            "sub_department": "Bank Reconciliation",
            "street_name": "Teheran-ro",
            "building_number": "152",
            "building_name": "Finance Center",
            "floor": "12",
            "unit_number": "1201",
            "post_box": "1001",
            "room": "Treasury",
            "post_code": "06236",
            "town_name": "Seoul",
            "town_location_name": "Gangnam",
            "district_name": "Gangnam-gu",
            "country_subdivision": "11",
            "country": "KR",
            "address_lines": [
                "152 Teheran-ro",
                "Gangnam-gu, Seoul 06236",
            ],
        }

    @classmethod
    def _postal_variants(
        cls,
        base: dict[str, object],
    ) -> dict[str, dict[str, object] | None]:
        """Change every postal scalar, line semantics, and whole-container presence."""
        replacements: dict[str, object] = {
            "address_type": {"code": "ADDR"},
            "care_of": "Alternate Treasury Recipient",
            "department": "Alternate Department",
            "sub_department": "Alternate Sub Department",
            "street_name": "Eulji-ro",
            "building_number": "99",
            "building_name": "Alternate Finance Center",
            "floor": "9",
            "unit_number": "901",
            "post_box": "9090",
            "room": "Alternate Treasury Room",
            "post_code": "04538",
            "town_name": "Busan",
            "town_location_name": "Jungang-dong",
            "district_name": "Jung-gu",
            "country_subdivision": "26",
            "country": "DE",
        }
        variants: dict[str, dict[str, object] | None] = {}
        for field, replacement in replacements.items():
            variant = copy.deepcopy(base)
            variant[field] = copy.deepcopy(replacement)
            variants[f"{field}-value"] = variant

        first_line = copy.deepcopy(base)
        lines = first_line.get("address_lines")
        if not isinstance(lines, list) or len(lines) != 2:
            raise AssertionError("PostalAddress27 RED requires two ordered AdrLine values")
        lines[0] = "Alternate direct-party address line"
        variants["address-line-value"] = first_line

        line_order = copy.deepcopy(base)
        ordered_lines = line_order.get("address_lines")
        if not isinstance(ordered_lines, list) or len(ordered_lines) != 2:
            raise AssertionError("PostalAddress27 RED requires two ordered AdrLine values")
        ordered_lines.reverse()
        variants["address-line-order"] = line_order

        variants["postal-address-absent"] = None
        return variants

    @staticmethod
    def _postal_xml(address: dict[str, object]) -> bytes:
        """Serialize PostalAddress27 in schema sequence order."""
        address_type = address.get("address_type")
        if not isinstance(address_type, dict) or set(address_type) != {"code"}:
            raise AssertionError("focused postal RED requires coded AddressType3Choice")
        lines = [
            "                  <PstlAdr>\n",
            "                    <AdrTp>\n",
            f"                      <Cd>{address_type['code']}</Cd>\n",
            "                    </AdrTp>\n",
        ]
        for field, tag in (
            ("care_of", "CareOf"),
            ("department", "Dept"),
            ("sub_department", "SubDept"),
            ("street_name", "StrtNm"),
            ("building_number", "BldgNb"),
            ("building_name", "BldgNm"),
            ("floor", "Flr"),
            ("unit_number", "UnitNb"),
            ("post_box", "PstBx"),
            ("room", "Room"),
            ("post_code", "PstCd"),
            ("town_name", "TwnNm"),
            ("town_location_name", "TwnLctnNm"),
            ("district_name", "DstrctNm"),
            ("country_subdivision", "CtrySubDvsn"),
            ("country", "Ctry"),
        ):
            value = address.get(field)
            if value is not None:
                lines.append(f"                    <{tag}>{value}</{tag}>\n")

        address_lines = address.get("address_lines")
        if not isinstance(address_lines, list):
            raise AssertionError("PostalAddress27 AdrLine values must preserve source order")
        for value in address_lines:
            lines.append(f"                    <AdrLine>{value}</AdrLine>\n")
        lines.append("                  </PstlAdr>\n")
        return "".join(lines).encode("utf-8")

    def _assert_semantic_hashes(
        self,
        statement: object,
        entry: object,
        detail: object,
        untouched_entry: object,
    ) -> None:
        """Require canonical retained identities before materiality comparisons."""
        for value in (
            getattr(entry, "counterparty_evidence_hash", None),
            getattr(detail, "source_detail_hash", None),
            getattr(entry, "source_entry_hash", None),
            getattr(statement, "normalized_payload_hash", None),
            getattr(statement, "account_identifier_hash", None),
            getattr(untouched_entry, "source_entry_hash", None),
        ):
            self.case._assert_sha256(value)

    def _assert_financial_truth(
        self,
        role: str,
        entry: object,
        detail: object,
    ) -> None:
        """Keep exact accounting amounts and currency independent from postal provenance."""
        self.assertEqual(
            getattr(entry, "entry_amount", None),
            self.case._expected_entry_amount(role),
        )
        self.assertEqual(getattr(entry, "entry_currency_code", None), "KRW")
        self.assertEqual(
            getattr(detail, "detail_amount", None),
            self.case._expected_detail_amount(role),
        )
        self.assertEqual(getattr(detail, "detail_currency_code", None), "KRW")

    @staticmethod
    def _public_projection(entry: dict[str, object]) -> dict[str, object]:
        """Remove only server/internal evidence identifiers from one buyer projection."""
        public = copy.deepcopy(entry)
        public.pop("bank_statement_entry_id")
        public.pop("counterparty_evidence_hash")
        public.pop("source_entry_hash")
        details = public.get("entry_details")
        if not isinstance(details, list):
            raise AssertionError("buyer entry projection must contain entry_details")
        public["entry_details"] = [
            {
                key: value
                for key, value in dict(detail).items()
                if key != "source_detail_hash"
            }
            for detail in details
        ]
        return public


if __name__ == "__main__":
    unittest.main()
