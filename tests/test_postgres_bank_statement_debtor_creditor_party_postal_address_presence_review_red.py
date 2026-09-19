"""Focused REDs for direct-party PostalAddress27 optional-child population."""

from __future__ import annotations

import copy
import unittest
import uuid

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_statement_evidence,
    parse_bank_statement_payload,
)
from tests import (
    test_postgres_bank_statement_debtor_creditor_party_postal_address_evidence_red as postal,
)
from tests import test_postgres_posting as posting

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDebtorCreditorPartyPostalAddressPresenceReviewRedTests(
    unittest.TestCase
):
    """Close optional-child and repeated-AdrLine false-GREEN paths in the postal RED."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the source-real PostgreSQL and camt.053 integration fixture."""
        postal.BankStatementDebtorCreditorPartyPostalAddressEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare the postal helper with cleanup registered before nested setup."""
        self.helper = postal.BankStatementDebtorCreditorPartyPostalAddressEvidenceRedTests(
            "setUp"
        )
        self.addCleanup(self.helper.doCleanups)
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)
        self.base_postal = copy.deepcopy(self.helper.base_postal)
        self.variants = self._presence_variants(self.base_postal)

    def test_optional_child_and_address_line_population_are_material(self) -> None:
        """Absent optional children and each AdrLine population change evidence identity."""
        for role in ("debtor", "creditor"):
            target_index = self.helper.case._target_entry_index(role)
            untouched_index = 1 - target_index
            baseline = parse_bank_statement_payload(
                self._with_role_postal(role, self.base_postal),
                CAMT053_MESSAGE_DEFINITION,
            )
            baseline_entry = baseline.entries[target_index]
            baseline_detail = baseline_entry.entry_details[0]
            self.helper._assert_financial_truth(role, baseline_entry, baseline_detail)
            self.helper._assert_semantic_hashes(
                baseline,
                baseline_entry,
                baseline_detail,
                baseline.entries[untouched_index],
            )

            for semantic, variant in self.variants.items():
                with self.subTest(role=role, semantic=semantic):
                    changed = parse_bank_statement_payload(
                        self._with_role_postal(role, variant),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    changed_entry = changed.entries[target_index]
                    changed_detail = changed_entry.entry_details[0]
                    self.helper._assert_financial_truth(
                        role, changed_entry, changed_detail
                    )
                    self.helper._assert_semantic_hashes(
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

    def test_presence_changes_reach_complete_correction_boundary(self) -> None:
        """Accepted postal child populations cannot be silently replaced on replay."""
        for role in ("debtor", "creditor"):
            baseline_payload = self._with_role_postal(role, self.base_postal)
            reference = self.helper.case._register_statement_account(baseline_payload)
            store = MemoryArtifactStore()
            accepted = accept_bank_statement_evidence(
                self.helper.case._command(
                    baseline_payload,
                    reference,
                    f"{role}-party-postal-presence-baseline-{uuid.uuid4().hex}",
                ),
                posting.DATABASE_URL,
                self.helper.case.case.policy.tenant_reference,
                artifact_store=store,
            )
            self.assertFalse(accepted["replayed"])

            for semantic, variant in self.variants.items():
                with self.subTest(role=role, semantic=semantic):
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        _CORRECTION_ERROR,
                    ):
                        accept_bank_statement_evidence(
                            self.helper.case._command(
                                self._with_role_postal(role, variant),
                                reference,
                                f"{role}-party-postal-presence-{semantic}-{uuid.uuid4().hex}",
                            ),
                            posting.DATABASE_URL,
                            self.helper.case.case.policy.tenant_reference,
                            artifact_store=store,
                        )

    def _with_role_postal(
        self,
        role: str,
        postal_address: dict[str, object],
    ) -> bytes:
        """Insert one schema-ordered PostalAddress27 into the selected direct Pty."""
        payload = self.helper.case._with_role_party(
            role,
            "Pty",
            self.helper.party_name,
        )
        name_marker = f"                  <Nm>{self.helper.party_name}</Nm>\n".encode(
            "utf-8"
        )
        if payload.count(name_marker) != 1:
            raise AssertionError("target direct-party name must be unique")
        return payload.replace(
            name_marker,
            name_marker + self._postal_xml(postal_address),
            1,
        )

    @staticmethod
    def _presence_variants(
        base: dict[str, object],
    ) -> dict[str, dict[str, object]]:
        """Remove each optional child and vary the repeated AdrLine population."""
        optional_fields = (
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
        )
        variants: dict[str, dict[str, object]] = {}
        for field in optional_fields:
            variant = copy.deepcopy(base)
            variant.pop(field)
            variants[f"{field}-absent"] = variant

        remove_first = copy.deepcopy(base)
        first_lines = remove_first.get("address_lines")
        if not isinstance(first_lines, list) or len(first_lines) != 2:
            raise AssertionError("focused review RED requires two source AdrLine values")
        first_lines.pop(0)
        variants["address-line-first-absent"] = remove_first

        remove_second = copy.deepcopy(base)
        second_lines = remove_second.get("address_lines")
        if not isinstance(second_lines, list) or len(second_lines) != 2:
            raise AssertionError("focused review RED requires two source AdrLine values")
        second_lines.pop(1)
        variants["address-line-second-absent"] = remove_second

        no_lines = copy.deepcopy(base)
        no_lines["address_lines"] = []
        variants["address-lines-absent"] = no_lines
        return variants

    @staticmethod
    def _postal_xml(address: dict[str, object]) -> bytes:
        """Serialize optional PostalAddress27 children and ordered AdrLine values."""
        lines = ["                  <PstlAdr>\n"]
        address_type = address.get("address_type")
        if address_type is not None:
            if not isinstance(address_type, dict) or set(address_type) != {"code"}:
                raise AssertionError("focused review RED requires coded AdrTp when present")
            lines.extend(
                (
                    "                    <AdrTp>\n",
                    f"                      <Cd>{address_type['code']}</Cd>\n",
                    "                    </AdrTp>\n",
                )
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
            raise AssertionError("AdrLine source population must remain an ordered list")
        for value in address_lines:
            lines.append(f"                    <AdrLine>{value}</AdrLine>\n")
        lines.append("                  </PstlAdr>\n")
        return "".join(lines).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
