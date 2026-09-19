"""PostgreSQL REDs for initiating-party agent PostalAddress27 evidence."""

from __future__ import annotations

import copy
import re
import unittest
import uuid
from decimal import Decimal

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    parse_bank_statement_payload,
)
from tests import test_postgres_bank_statement_detail_initiating_party_choice_evidence_red as choice
from tests import test_postgres_bank_statement_detail_initiating_party_evidence_red as initiating
from tests import test_postgres_posting as posting

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)

_SIMPLE_ADDRESS_FIELDS: tuple[tuple[str, str], ...] = (
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
)


def _address(prefix: str) -> dict[str, object]:
    """Return a full PostalAddress27 fixture with two ordered address lines."""
    return {
        "address_type_code": "BIZZ",
        "care_of": f"{prefix} Care Of",
        "department": f"{prefix} Treasury",
        "sub_department": f"{prefix} Settlement",
        "street_name": f"{prefix} Street",
        "building_number": "41",
        "building_name": f"{prefix} House",
        "floor": "14",
        "unit_number": "1407",
        "post_box": "4101",
        "room": "1407A",
        "post_code": "10119",
        "town_name": f"{prefix} City",
        "town_location_name": f"{prefix} Centre",
        "district_name": f"{prefix} District",
        "country_subdivision": f"{prefix} State",
        "country": "DE",
        "address_lines": [
            f"{prefix} Street 41, Floor 14",
            f"10119 {prefix} City",
        ],
    }


_INSTITUTION_ADDRESS = _address("Initiating Institution")
_BRANCH_ADDRESS = _address("Initiating Branch")


class BankStatementDetailInitiatingPartyAgentPostalAddressEvidenceRedTests(
    unittest.TestCase
):
    """Retain institution and branch postal provenance for InitgPty/Agt."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL initiating-party fixture."""
        choice.BankStatementDetailInitiatingPartyChoiceEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare isolated initiating-party state and deep agent identity."""
        self.case = choice.BankStatementDetailInitiatingPartyChoiceEvidenceRedTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.identity = {
            "bicfi": "DEUTDEFF",
            "lei": "7LTWFZYICNSX8D621K86",
            "name": "Initiating Financial Institution",
            "branch_id": "INIT-BR-001",
            "branch_lei": "529900Z6KVD8Y83D7K60",
            "branch_name": "Initiating Branch Identity",
        }

    def test_postal_values_presence_and_order_are_material_by_placement(self) -> None:
        """Every PostalAddress27 scalar, optional child, and AdrLine order changes evidence."""
        baseline = self._statement(
            self._payload(
                institution_address=_INSTITUTION_ADDRESS,
                branch_address=_BRANCH_ADDRESS,
            )
        )
        baseline_entry = baseline.entries[0]
        baseline_detail = baseline_entry.entry_details[0]
        self._assert_statement_truth(baseline, baseline_entry, baseline_detail)

        for placement, base_address in (
            ("institution", _INSTITUTION_ADDRESS),
            ("branch", _BRANCH_ADDRESS),
        ):
            variants = self._address_variants(base_address)
            variants["postal-container-absent"] = None
            for semantic, changed_address in variants.items():
                with self.subTest(placement=placement, semantic=semantic):
                    changed = self._statement(
                        self._payload_for_variant(placement, changed_address)
                    )
                    changed_entry = changed.entries[0]
                    changed_detail = changed_entry.entry_details[0]
                    self._assert_statement_truth(changed, changed_entry, changed_detail)

                    self.assertNotEqual(
                        baseline_detail.initiating_party_evidence_hash,
                        changed_detail.initiating_party_evidence_hash,
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

    def test_postal_layout_is_representation_only_for_both_placements(self) -> None:
        """Whitespace inside either PstlAdr changes bytes without semantic drift."""
        payload = self._payload(
            institution_address=_INSTITUTION_ADDRESS,
            branch_address=_BRANCH_ADDRESS,
        )
        needle = b"                    <PstlAdr>\n"
        offsets = [
            index
            for index in range(len(payload))
            if payload.startswith(needle, index)
        ]
        self.assertEqual(len(offsets), 2)

        for placement, target_index in (
            ("institution", offsets[0]),
            ("branch", offsets[1]),
        ):
            with self.subTest(placement=placement):
                changed_payload = (
                    payload[: target_index + len(needle)]
                    + b"                      \n"
                    + payload[target_index + len(needle) :]
                )
                baseline = self._statement(payload)
                changed = self._statement(changed_payload)
                baseline_entry = baseline.entries[0]
                changed_entry = changed.entries[0]
                baseline_detail = baseline_entry.entry_details[0]
                changed_detail = changed_entry.entry_details[0]
                self._assert_statement_truth(baseline, baseline_entry, baseline_detail)
                self._assert_statement_truth(changed, changed_entry, changed_detail)
                self._assert_sha256(baseline.source_artifact_hash)
                self._assert_sha256(changed.source_artifact_hash)

                self.assertNotEqual(
                    baseline.source_artifact_hash,
                    changed.source_artifact_hash,
                )
                self.assertEqual(
                    baseline_detail.initiating_party_evidence_hash,
                    changed_detail.initiating_party_evidence_hash,
                )
                self.assertEqual(
                    baseline_detail.source_detail_hash,
                    changed_detail.source_detail_hash,
                )
                self.assertEqual(
                    baseline_entry.source_entry_hash,
                    changed_entry.source_entry_hash,
                )
                self.assertEqual(
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

    def test_every_postal_variant_reaches_complete_correction_boundary(self) -> None:
        """Accepted initiating-agent postal evidence cannot change on ordinary replay."""
        baseline_payload = self._payload(
            institution_address=_INSTITUTION_ADDRESS,
            branch_address=_BRANCH_ADDRESS,
        )
        for placement, base_address in (
            ("institution", _INSTITUTION_ADDRESS),
            ("branch", _BRANCH_ADDRESS),
        ):
            variants = self._address_variants(base_address)
            variants["postal-container-absent"] = None
            for semantic, changed_address in variants.items():
                with self.subTest(placement=placement, semantic=semantic):
                    reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
                    self.case.helper._register_bank_account(reference)
                    store = MemoryArtifactStore()
                    accepted = initiating.accept_bank_statement_evidence(
                        self.case.helper._command(
                            baseline_payload,
                            f"initiating-agent-postal-{placement}-{semantic}-baseline",
                            reference,
                        ),
                        posting.DATABASE_URL,
                        self.case.helper.case.policy.tenant_reference,
                        artifact_store=store,
                    )
                    self.assertFalse(accepted["replayed"])
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        _CORRECTION_ERROR,
                    ):
                        initiating.accept_bank_statement_evidence(
                            self.case.helper._command(
                                self._payload_for_variant(placement, changed_address),
                                f"initiating-agent-postal-{placement}-{semantic}-changed",
                                reference,
                            ),
                            posting.DATABASE_URL,
                            self.case.helper.case.policy.tenant_reference,
                            artifact_store=store,
                        )

    def test_postal_source_values_remain_non_reversible_on_buyer_projection(self) -> None:
        """Postal facts affect internal evidence without adding buyer-visible fields."""
        for placement, source_address in (
            ("institution", _INSTITUTION_ADDRESS),
            ("branch", _BRANCH_ADDRESS),
        ):
            with self.subTest(placement=placement):
                rich_payload = self._payload(
                    institution_address=(
                        source_address if placement == "institution" else None
                    ),
                    branch_address=source_address if placement == "branch" else None,
                )
                absent_payload = self._payload(
                    institution_address=None,
                    branch_address=None,
                )
                rich_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
                absent_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
                self.case.helper._register_bank_account(rich_reference)
                self.case.helper._register_bank_account(absent_reference)
                rich_entry, rich_detail = self.case._ingest_and_read_first_entry_and_detail(
                    rich_payload,
                    rich_reference,
                    f"initiating-agent-postal-{placement}-rich-{uuid.uuid4().hex}",
                )
                absent_entry, absent_detail = (
                    self.case._ingest_and_read_first_entry_and_detail(
                        absent_payload,
                        absent_reference,
                        f"initiating-agent-postal-{placement}-absent-{uuid.uuid4().hex}",
                    )
                )

                for entry, detail in (
                    (rich_entry, rich_detail),
                    (absent_entry, absent_detail),
                ):
                    self._assert_uuid(entry["bank_statement_entry_id"])
                    self._assert_sha256(entry["source_entry_hash"])
                    self._assert_sha256(detail["initiating_party_evidence_hash"])
                    self._assert_sha256(detail["source_detail_hash"])
                    self.assertEqual(
                        Decimal(str(entry["entry_amount"])),
                        Decimal("25000.00"),
                    )
                    self.assertEqual(entry["entry_currency_code"], "KRW")
                    self.assertEqual(
                        Decimal(str(detail["detail_amount"])),
                        Decimal("25000.00"),
                    )
                    self.assertEqual(detail["detail_currency_code"], "KRW")

                self.assertNotEqual(
                    rich_detail["initiating_party_evidence_hash"],
                    absent_detail["initiating_party_evidence_hash"],
                )
                self.assertNotEqual(
                    rich_detail["source_detail_hash"],
                    absent_detail["source_detail_hash"],
                )
                self.assertNotEqual(
                    rich_entry["source_entry_hash"],
                    absent_entry["source_entry_hash"],
                )

                rich_public = self._public_entry_projection(rich_entry)
                absent_public = self._public_entry_projection(absent_entry)
                self.assertEqual(rich_public, absent_public)
                source_values = self._privacy_source_values(source_address)
                buyer_values = set(self._scalar_leaves(rich_public))
                self.assertTrue(source_values.isdisjoint(buyer_values))

    def _payload_for_variant(
        self,
        placement: str,
        changed_address: dict[str, object] | None,
    ) -> bytes:
        """Change one postal placement while retaining the other baseline address."""
        if placement == "institution":
            return self._payload(
                institution_address=changed_address,
                branch_address=_BRANCH_ADDRESS,
            )
        if placement == "branch":
            return self._payload(
                institution_address=_INSTITUTION_ADDRESS,
                branch_address=changed_address,
            )
        raise AssertionError(f"unsupported postal placement: {placement}")

    def _payload(
        self,
        *,
        institution_address: dict[str, object] | None,
        branch_address: dict[str, object] | None,
    ) -> bytes:
        """Insert initiating-agent institution and branch PostalAddress27 in schema order."""
        return self.case._with_choice(
            self._agent_xml(
                institution_address=institution_address,
                branch_address=branch_address,
            )
        )

    def _agent_xml(
        self,
        *,
        institution_address: dict[str, object] | None,
        branch_address: dict[str, object] | None,
    ) -> str:
        """Serialize BranchAndFinancialInstitutionIdentification8 with optional addresses."""
        identity = self.identity
        lines = [
            "                <Agt>",
            "                  <FinInstnId>",
            f"                    <BICFI>{identity['bicfi']}</BICFI>",
            f"                    <LEI>{identity['lei']}</LEI>",
            f"                    <Nm>{identity['name']}</Nm>",
        ]
        if institution_address is not None:
            lines.extend(self._address_xml(institution_address))
        lines.extend(
            [
                "                  </FinInstnId>",
                "                  <BrnchId>",
                f"                    <Id>{identity['branch_id']}</Id>",
                f"                    <LEI>{identity['branch_lei']}</LEI>",
                f"                    <Nm>{identity['branch_name']}</Nm>",
            ]
        )
        if branch_address is not None:
            lines.extend(self._address_xml(branch_address))
        lines.extend(
            [
                "                  </BrnchId>",
                "                </Agt>",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _address_xml(address: dict[str, object]) -> list[str]:
        """Serialize PostalAddress27 source semantics in V14 sequence order."""
        lines = ["                    <PstlAdr>"]
        address_type_code = address.get("address_type_code")
        if address_type_code is not None:
            if not isinstance(address_type_code, str) or not address_type_code:
                raise AssertionError("AdrTp/Cd must be non-empty text")
            lines.extend(
                [
                    "                      <AdrTp>",
                    f"                        <Cd>{address_type_code}</Cd>",
                    "                      </AdrTp>",
                ]
            )

        for field, tag in _SIMPLE_ADDRESS_FIELDS:
            value = address.get(field)
            if value is not None:
                if not isinstance(value, str):
                    raise AssertionError(f"PostalAddress27 {field} must be text")
                lines.append(f"                      <{tag}>{value}</{tag}>")

        address_lines = address.get("address_lines", [])
        if not isinstance(address_lines, list):
            raise AssertionError("PostalAddress27 AdrLine values must remain ordered")
        for line in address_lines:
            if not isinstance(line, str):
                raise AssertionError("PostalAddress27 AdrLine must be text")
            lines.append(f"                      <AdrLine>{line}</AdrLine>")
        lines.append("                    </PstlAdr>")
        return lines

    @classmethod
    def _address_variants(
        cls,
        address: dict[str, object],
    ) -> dict[str, dict[str, object]]:
        """Return independent scalar, presence, and ordered-line changes."""
        variants: dict[str, dict[str, object]] = {}

        address_type_changed = copy.deepcopy(address)
        address_type_changed["address_type_code"] = "HOME"
        variants["address-type-code-value"] = address_type_changed

        address_type_absent = copy.deepcopy(address)
        address_type_absent.pop("address_type_code")
        variants["address-type-absent"] = address_type_absent

        alternatives = {
            "care_of": "Alternate Initiating Care Of",
            "department": "Alternate Initiating Treasury",
            "sub_department": "Alternate Initiating Settlement",
            "street_name": "Alternate Initiating Street",
            "building_number": "42",
            "building_name": "Alternate Initiating House",
            "floor": "15",
            "unit_number": "1508",
            "post_box": "4201",
            "room": "1508B",
            "post_code": "10120",
            "town_name": "Alternate Initiating City",
            "town_location_name": "Alternate Initiating Centre",
            "district_name": "Alternate Initiating District",
            "country_subdivision": "Alternate Initiating State",
            "country": "FR",
        }
        for field, alternative in alternatives.items():
            changed = copy.deepcopy(address)
            changed[field] = alternative
            variants[f"{field}-value"] = changed

            absent = copy.deepcopy(address)
            absent.pop(field)
            variants[f"{field}-absent"] = absent

        address_lines = address.get("address_lines")
        if not isinstance(address_lines, list) or len(address_lines) != 2:
            raise AssertionError("focused PostalAddress27 fixture requires two AdrLine values")

        first_changed = copy.deepcopy(address)
        first_lines = first_changed.get("address_lines")
        if not isinstance(first_lines, list):
            raise AssertionError("AdrLine fixture must remain a list")
        first_lines[0] = "Alternate first initiating address line"
        variants["address-line-first-value"] = first_changed

        second_changed = copy.deepcopy(address)
        second_lines = second_changed.get("address_lines")
        if not isinstance(second_lines, list):
            raise AssertionError("AdrLine fixture must remain a list")
        second_lines[1] = "Alternate second initiating address line"
        variants["address-line-second-value"] = second_changed

        reversed_lines = copy.deepcopy(address)
        reversed_lines["address_lines"] = list(reversed(address_lines))
        variants["address-line-order"] = reversed_lines

        first_absent = copy.deepcopy(address)
        first_absent["address_lines"] = [address_lines[1]]
        variants["address-line-first-absent"] = first_absent

        second_absent = copy.deepcopy(address)
        second_absent["address_lines"] = [address_lines[0]]
        variants["address-line-second-absent"] = second_absent

        all_lines_absent = copy.deepcopy(address)
        all_lines_absent["address_lines"] = []
        variants["address-lines-all-absent"] = all_lines_absent
        return variants

    @staticmethod
    def _statement(payload: bytes) -> object:
        """Parse one V14 statement through the supported normalization boundary."""
        return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    def _assert_statement_truth(self, statement: object, entry: object, detail: object) -> None:
        """Require canonical evidence identities and preserve exact transaction facts."""
        for value in (
            getattr(detail, "initiating_party_evidence_hash"),
            getattr(detail, "source_detail_hash"),
            getattr(entry, "source_entry_hash"),
            getattr(statement, "normalized_payload_hash"),
            getattr(statement, "account_identifier_hash"),
            getattr(statement.entries[1], "source_entry_hash"),
        ):
            self._assert_sha256(value)
        self.assertEqual(getattr(entry, "entry_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(entry, "entry_currency_code"), "KRW")
        self.assertEqual(getattr(detail, "detail_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(detail, "detail_currency_code"), "KRW")

    @staticmethod
    def _assert_sha256(value: object) -> None:
        """Require canonical lowercase SHA-256 evidence text."""
        if not isinstance(value, str) or _HASH_PATTERN.fullmatch(value) is None:
            raise AssertionError(f"expected canonical sha256 digest, got {value!r}")

    @staticmethod
    def _assert_uuid(value: object) -> None:
        """Require canonical lowercase hyphenated UUID text before hiding server identity."""
        if not isinstance(value, str):
            raise AssertionError(f"expected UUID text, got {value!r}")
        try:
            parsed = uuid.UUID(value)
        except (ValueError, AttributeError) as exc:
            raise AssertionError(f"expected canonical UUID text, got {value!r}") from exc
        if str(parsed) != value:
            raise AssertionError(f"expected canonical UUID text, got {value!r}")

    @staticmethod
    def _public_entry_projection(entry: dict[str, object]) -> dict[str, object]:
        """Remove only server identity and initiating evidence hashes before comparison."""
        projection = copy.deepcopy(entry)
        projection.pop("bank_statement_entry_id")
        projection.pop("source_entry_hash")
        details = projection.get("entry_details")
        if not isinstance(details, list):
            raise AssertionError("expected entry_details to be a list")
        for detail in details:
            if not isinstance(detail, dict):
                raise AssertionError("expected buyer detail mapping")
            detail.pop("initiating_party_evidence_hash")
            detail.pop("source_detail_hash")
        return projection

    @classmethod
    def _privacy_source_values(cls, address: dict[str, object]) -> set[str]:
        """Collect exact source postal scalar values for non-reversibility checks."""
        return set(cls._scalar_leaves(address))

    @classmethod
    def _scalar_leaves(cls, value: object) -> list[str]:
        """Collect exact scalar leaves recursively without substring heuristics."""
        leaves: list[str] = []
        if isinstance(value, dict):
            for child in value.values():
                leaves.extend(cls._scalar_leaves(child))
        elif isinstance(value, (list, tuple)):
            for child in value:
                leaves.extend(cls._scalar_leaves(child))
        elif value is not None:
            leaves.append(str(value))
        return leaves


if __name__ == "__main__":
    unittest.main()
