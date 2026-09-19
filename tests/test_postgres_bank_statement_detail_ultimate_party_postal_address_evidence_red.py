"""PostgreSQL REDs for ultimate-party PostalAddress27 evidence in camt.053 details."""

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
    test_postgres_bank_statement_debtor_creditor_party_postal_address_evidence_red as direct_postal,
)
from tests import (
    test_postgres_bank_statement_detail_ultimate_party_identification_evidence_red
    as ultimate_identity,
)
from tests import test_postgres_posting as posting

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailUltimatePartyPostalAddressEvidenceRedTests(unittest.TestCase):
    """Retain ultimate-party PostalAddress27 as purpose-bound non-reversible evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        (
            ultimate_identity.BankStatementDetailUltimatePartyIdentificationEvidenceRedTests
            .setUpClass()
        )

    def setUp(self) -> None:
        """Prepare one ultimate-party helper with exception-safe nested cleanup."""
        self.case = (
            ultimate_identity.BankStatementDetailUltimatePartyIdentificationEvidenceRedTests(
                "setUp"
            )
        )
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.base_postal = (
            direct_postal.BankStatementDebtorCreditorPartyPostalAddressEvidenceRedTests
            ._complete_postal_address()
        )
        self.variants = self._postal_variants(self.base_postal)

    def test_postal_values_presence_and_order_are_material_for_each_ultimate_role(
        self,
    ) -> None:
        """Every postal value, presence bit, and AdrLine order changes identity."""
        for role in ("ultimate_debtor", "ultimate_creditor"):
            baseline = parse_bank_statement_payload(
                self._with_role_postal(role, self.base_postal),
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

            for semantic, postal in self.variants.items():
                with self.subTest(role=role, semantic=semantic):
                    changed = parse_bank_statement_payload(
                        self._with_role_postal(role, postal),
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

    def test_postal_xml_layout_is_representation_only_for_each_ultimate_role(
        self,
    ) -> None:
        """Whitespace inside PstlAdr changes raw bytes without changing admitted semantics."""
        needle = b"                  <PstlAdr>\n"
        whitespace = b"                    \n"

        for role in ("ultimate_debtor", "ultimate_creditor"):
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

                self.assertNotEqual(
                    left.source_artifact_hash,
                    right.source_artifact_hash,
                )
                self.assertEqual(
                    getattr(left_detail, evidence_key),
                    getattr(right_detail, evidence_key),
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

    def test_postal_changes_reach_complete_correction_boundary_for_each_ultimate_role(
        self,
    ) -> None:
        """Accepted ultimate-party postal provenance cannot be silently replaced by replay."""
        for role in ("ultimate_debtor", "ultimate_creditor"):
            baseline_payload = self._with_role_postal(role, self.base_postal)
            reference = self.case._register_statement_account(baseline_payload)
            store = MemoryArtifactStore()
            accepted = accept_bank_statement_evidence(
                self.case._command(
                    baseline_payload,
                    reference,
                    f"{role}-postal-baseline",
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
                                f"{role}-postal-{semantic}",
                            ),
                            posting.DATABASE_URL,
                            self.case.case.policy.tenant_reference,
                            artifact_store=store,
                        )

    def test_buyer_projection_keeps_ultimate_postal_address_non_reversible(
        self,
    ) -> None:
        """Postal PII changes internal evidence without becoming buyer identity fields."""
        sensitive_postal = copy.deepcopy(self.base_postal)
        sensitive_postal.update(
            {
                "care_of": "Confidential Ultimate Treasury Recipient",
                "street_name": "Sensitive Ultimate Party Street",
                "building_number": "77",
                "building_name": "Sensitive Ultimate Party Tower",
                "unit_number": "1901",
                "post_box": "ULTIMATE-PBOX-7788",
                "room": "Private Ultimate Treasury Room",
                "post_code": "04567",
                "town_name": "Busan",
                "town_location_name": "Jungang-dong",
                "district_name": "Jung-gu",
                "country_subdivision": "26",
                "country": "DE",
                "address_lines": [
                    "Sensitive Ultimate Party Line One",
                    "Sensitive Ultimate Party Line Two",
                ],
            }
        )

        for role in ("ultimate_debtor", "ultimate_creditor"):
            with self.subTest(role=role):
                baseline = self.case._ingest_and_read_first_detail(
                    self._with_role_postal(role, None),
                    f"{role}-postal-none-{uuid.uuid4().hex}",
                )
                rich = self.case._ingest_and_read_first_detail(
                    self._with_role_postal(role, sensitive_postal),
                    f"{role}-postal-rich-{uuid.uuid4().hex}",
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

                self.assertNotEqual(
                    baseline[evidence_key],
                    rich[evidence_key],
                )
                self.assertNotEqual(
                    baseline["source_detail_hash"],
                    rich["source_detail_hash"],
                )
                self.assertEqual(
                    self.case._public_projection(baseline, evidence_key),
                    self.case._public_projection(rich, evidence_key),
                )

                serialized = json.dumps(rich, sort_keys=True, default=str)
                for source_value in (
                    "Confidential Ultimate Treasury Recipient",
                    "Sensitive Ultimate Party Street",
                    "Sensitive Ultimate Party Tower",
                    "ULTIMATE-PBOX-7788",
                    "Private Ultimate Treasury Room",
                    "Busan",
                    "Jungang-dong",
                    "Jung-gu",
                    "Sensitive Ultimate Party Line One",
                    "Sensitive Ultimate Party Line Two",
                ):
                    self.assertNotIn(source_value, serialized)

    def _with_role_postal(
        self,
        role: str,
        postal: dict[str, object] | None,
    ) -> bytes:
        """Return one ultimate-party Pty branch with optional PostalAddress27."""
        payload = self.case._with_ultimate_party(
            role,
            "organisation",
            self.case.base_identifier,
        )
        if postal is None:
            return payload

        name_marker = f"                  <Nm>{self.case.party_name}</Nm>\n".encode(
            "utf-8"
        )
        id_marker = b"                  <Id>\n"
        insertion_marker = name_marker + id_marker
        if payload.count(insertion_marker) != 1:
            raise AssertionError("target ultimate-party name/Id sequence must be unique")
        return payload.replace(
            insertion_marker,
            name_marker + self._postal_xml(postal) + id_marker,
            1,
        )

    @classmethod
    def _postal_variants(
        cls,
        base: dict[str, object],
    ) -> dict[str, dict[str, object] | None]:
        """Build value, optional-presence, repeated-line, order, and container variants."""
        variants = (
            direct_postal.BankStatementDebtorCreditorPartyPostalAddressEvidenceRedTests
            ._postal_variants(
                base
            )
        )

        for field in (
            "address_type",
            "care_of",
            "department",
            "sub_department",
            "street_name",
            "building_number",
            "building_name",
            "floor",
            "unit_number",
            "post_box",
            "room",
            "post_code",
            "town_name",
            "town_location_name",
            "district_name",
            "country_subdivision",
            "country",
        ):
            variant = copy.deepcopy(base)
            if field not in variant:
                raise AssertionError(f"missing PostalAddress27 field in RED: {field}")
            variant.pop(field)
            variants[f"{field}-absent"] = variant

        first_line_absent = copy.deepcopy(base)
        first_lines = first_line_absent.get("address_lines")
        if not isinstance(first_lines, list) or len(first_lines) != 2:
            raise AssertionError("PostalAddress27 RED requires two ordered AdrLine values")
        first_lines.pop(0)
        variants["first-address-line-absent"] = first_line_absent

        second_line_absent = copy.deepcopy(base)
        second_lines = second_line_absent.get("address_lines")
        if not isinstance(second_lines, list) or len(second_lines) != 2:
            raise AssertionError("PostalAddress27 RED requires two ordered AdrLine values")
        second_lines.pop(1)
        variants["second-address-line-absent"] = second_line_absent

        all_lines_absent = copy.deepcopy(base)
        all_lines_absent["address_lines"] = []
        variants["all-address-lines-absent"] = all_lines_absent
        return variants

    @staticmethod
    def _postal_xml(address: dict[str, object]) -> bytes:
        """Serialize PostalAddress27 in schema sequence order with optional children."""
        lines = ["                  <PstlAdr>\n"]

        address_type = address.get("address_type")
        if address_type is not None:
            if not isinstance(address_type, dict) or set(address_type) != {"code"}:
                raise AssertionError("focused postal RED requires coded AddressType3Choice")
            lines.extend(
                [
                    "                    <AdrTp>\n",
                    f"                      <Cd>{address_type['code']}</Cd>\n",
                    "                    </AdrTp>\n",
                ]
            )

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
            raise AssertionError("PostalAddress27 address_lines must be a list")
        for line in address_lines:
            lines.append(f"                    <AdrLine>{line}</AdrLine>\n")

        lines.append("                  </PstlAdr>\n")
        return "".join(lines).encode("utf-8")

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


if __name__ == "__main__":
    unittest.main()
