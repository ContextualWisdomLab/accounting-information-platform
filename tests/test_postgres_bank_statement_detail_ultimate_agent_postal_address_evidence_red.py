"""PostgreSQL REDs for ultimate-agent PostalAddress27 evidence."""

from __future__ import annotations

import copy
import re
import unittest
import uuid
from decimal import Decimal

from accounting_information_platform import AccountingValidationError, MemoryArtifactStore
from tests import (
    test_postgres_bank_statement_detail_ultimate_agent_deep_identity_evidence_red as deep,
)

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
    """Return a full PostalAddress27 source fixture with ordered address lines."""
    return {
        "address_type": {"code": "BIZZ"},
        "care_of": f"{prefix} Care Of",
        "department": f"{prefix} Treasury",
        "sub_department": f"{prefix} Settlement",
        "street_name": f"{prefix} Street",
        "building_number": "17",
        "building_name": f"{prefix} House",
        "floor": "12",
        "unit_number": "1204",
        "post_box": "1701",
        "room": "1204A",
        "post_code": "10117",
        "town_name": f"{prefix} City",
        "town_location_name": f"{prefix} Centre",
        "district_name": f"{prefix} District",
        "country_subdivision": f"{prefix} State",
        "country": "DE",
        "address_lines": [
            f"{prefix} Street 17, Floor 12",
            f"10117 {prefix} City",
        ],
    }


_INSTITUTION_ADDRESS = _address("Ultimate Institution")
_BRANCH_ADDRESS = _address("Ultimate Branch")


class BankStatementDetailUltimateAgentPostalAddressEvidenceRedTests(unittest.TestCase):
    """Retain institution and branch PostalAddress27 by ultimate-agent role."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        deep.BankStatementDetailUltimateAgentDeepIdentityEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare isolated PostgreSQL state and ultimate-agent source fixtures."""
        self.case = deep.BankStatementDetailUltimateAgentDeepIdentityEvidenceRedTests(
            "setUp"
        )
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def test_every_postal_value_and_presence_fact_is_material_by_role_and_placement(
        self,
    ) -> None:
        """PostalAddress27 values, optional children, and AdrLine order affect evidence."""
        for role, identity in self.case.identities.items():
            baseline = self._statement(
                self._payload(
                    role,
                    identity,
                    institution_address=_INSTITUTION_ADDRESS,
                    branch_address=_BRANCH_ADDRESS,
                )
            )
            baseline_entry = baseline.entries[0]
            baseline_detail = baseline_entry.entry_details[0]
            evidence_key = self.case._evidence_key(role)
            self._assert_statement_evidence(
                baseline,
                baseline_entry,
                baseline_detail,
                evidence_key,
            )

            for placement, base_address in (
                ("institution", _INSTITUTION_ADDRESS),
                ("branch", _BRANCH_ADDRESS),
            ):
                for semantic, changed_address in self._address_variants(
                    base_address
                ).items():
                    with self.subTest(
                        role=role,
                        placement=placement,
                        semantic=semantic,
                    ):
                        changed = self._statement(
                            self._payload_for_variant(
                                role,
                                identity,
                                placement,
                                changed_address,
                            )
                        )
                        changed_entry = changed.entries[0]
                        changed_detail = changed_entry.entry_details[0]
                        self._assert_statement_evidence(
                            changed,
                            changed_entry,
                            changed_detail,
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

                with self.subTest(
                    role=role,
                    placement=placement,
                    semantic="postal-container-absent",
                ):
                    absent = self._statement(
                        self._payload_for_variant(role, identity, placement, None)
                    )
                    absent_entry = absent.entries[0]
                    absent_detail = absent_entry.entry_details[0]
                    self._assert_statement_evidence(
                        absent,
                        absent_entry,
                        absent_detail,
                        evidence_key,
                    )
                    self.assertNotEqual(
                        getattr(baseline_detail, evidence_key),
                        getattr(absent_detail, evidence_key),
                    )
                    self.assertNotEqual(
                        baseline_detail.source_detail_hash,
                        absent_detail.source_detail_hash,
                    )
                    self.assertNotEqual(
                        baseline_entry.source_entry_hash,
                        absent_entry.source_entry_hash,
                    )
                    self.assertNotEqual(
                        baseline.normalized_payload_hash,
                        absent.normalized_payload_hash,
                    )
                    self.assertEqual(
                        baseline.account_identifier_hash,
                        absent.account_identifier_hash,
                    )
                    self.assertEqual(
                        baseline.entries[1].source_entry_hash,
                        absent.entries[1].source_entry_hash,
                    )

    def test_proprietary_address_type_nested_facts_and_choice_are_material(self) -> None:
        """AddressType3Choice and GenericIdentification30 retain discriminator semantics."""
        proprietary = copy.deepcopy(_INSTITUTION_ADDRESS)
        proprietary["address_type"] = {
            "proprietary": {
                "id": "BIZZ",
                "issuer": "Ultimate Address Registry",
                "scheme_name": "ADDR-TYPE",
            }
        }
        variants: dict[str, dict[str, object]] = {}

        changed_id = copy.deepcopy(proprietary)
        self._proprietary_address_type(changed_id)["id"] = "HOME"
        variants["proprietary-id-value"] = changed_id

        changed_issuer = copy.deepcopy(proprietary)
        self._proprietary_address_type(changed_issuer)["issuer"] = (
            "Alternate Address Registry"
        )
        variants["proprietary-issuer-value"] = changed_issuer

        changed_scheme = copy.deepcopy(proprietary)
        self._proprietary_address_type(changed_scheme)["scheme_name"] = "ALT-TYPE"
        variants["proprietary-scheme-value"] = changed_scheme

        scheme_absent = copy.deepcopy(proprietary)
        self._proprietary_address_type(scheme_absent).pop("scheme_name")
        variants["proprietary-scheme-absent"] = scheme_absent

        coded_same_id = copy.deepcopy(proprietary)
        coded_same_id["address_type"] = {"code": "BIZZ"}
        variants["same-id-choice-discriminator"] = coded_same_id

        for role, identity in self.case.identities.items():
            baseline = self._statement(
                self._payload(
                    role,
                    identity,
                    institution_address=proprietary,
                    branch_address=_BRANCH_ADDRESS,
                )
            )
            baseline_entry = baseline.entries[0]
            baseline_detail = baseline_entry.entry_details[0]
            evidence_key = self.case._evidence_key(role)
            self._assert_statement_evidence(
                baseline,
                baseline_entry,
                baseline_detail,
                evidence_key,
            )

            for semantic, changed_address in variants.items():
                with self.subTest(role=role, semantic=semantic):
                    changed = self._statement(
                        self._payload(
                            role,
                            identity,
                            institution_address=changed_address,
                            branch_address=_BRANCH_ADDRESS,
                        )
                    )
                    changed_entry = changed.entries[0]
                    changed_detail = changed_entry.entry_details[0]
                    self._assert_statement_evidence(
                        changed,
                        changed_entry,
                        changed_detail,
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

    def test_postal_layout_is_representation_only_for_both_ultimate_roles(self) -> None:
        """Whitespace inside either PstlAdr changes bytes without semantic drift."""
        needle = b"                    <PstlAdr>\n"
        whitespace = b"                      \n"

        for role, identity in self.case.identities.items():
            payload = self._payload(
                role,
                identity,
                institution_address=_INSTITUTION_ADDRESS,
                branch_address=_BRANCH_ADDRESS,
            )
            occurrences = [
                index
                for index in range(len(payload))
                if payload.startswith(needle, index)
            ]
            self.assertEqual(len(occurrences), 2)

            for placement, target_index in (
                ("institution", occurrences[0]),
                ("branch", occurrences[1]),
            ):
                with self.subTest(role=role, placement=placement):
                    changed_payload = (
                        payload[: target_index + len(needle)]
                        + whitespace
                        + payload[target_index + len(needle) :]
                    )
                    baseline = self._statement(payload)
                    changed = self._statement(changed_payload)
                    baseline_entry = baseline.entries[0]
                    changed_entry = changed.entries[0]
                    baseline_detail = baseline_entry.entry_details[0]
                    changed_detail = changed_entry.entry_details[0]
                    evidence_key = self.case._evidence_key(role)
                    for value in (
                        baseline.source_artifact_hash,
                        changed.source_artifact_hash,
                        getattr(baseline_detail, evidence_key),
                        getattr(changed_detail, evidence_key),
                        baseline_detail.source_detail_hash,
                        changed_detail.source_detail_hash,
                        baseline_entry.source_entry_hash,
                        changed_entry.source_entry_hash,
                        baseline.normalized_payload_hash,
                        changed.normalized_payload_hash,
                    ):
                        self._assert_sha256(value)

                    self.assertNotEqual(
                        baseline.source_artifact_hash,
                        changed.source_artifact_hash,
                    )
                    self.assertEqual(
                        getattr(baseline_detail, evidence_key),
                        getattr(changed_detail, evidence_key),
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
                    self._assert_financial_truth(changed_entry, changed_detail)

    def test_every_postal_variant_reaches_complete_correction_boundary(self) -> None:
        """Accepted ultimate-agent postal evidence cannot be silently replaced."""
        for role, identity in self.case.identities.items():
            baseline_payload = self._payload(
                role,
                identity,
                institution_address=_INSTITUTION_ADDRESS,
                branch_address=_BRANCH_ADDRESS,
            )
            reference = self.case._register_statement_account(baseline_payload)
            store = MemoryArtifactStore()
            accepted = deep.accept_bank_statement_evidence(
                self.case._command(
                    baseline_payload,
                    reference,
                    f"{role}-postal-baseline",
                ),
                deep.posting.DATABASE_URL,
                self.case.case.policy.tenant_reference,
                artifact_store=store,
            )
            self.assertFalse(accepted["replayed"])

            for placement, base_address in (
                ("institution", _INSTITUTION_ADDRESS),
                ("branch", _BRANCH_ADDRESS),
            ):
                variants = self._address_variants(base_address)
                variants["postal-container-absent"] = None
                for semantic, changed_address in variants.items():
                    with self.subTest(
                        role=role,
                        placement=placement,
                        semantic=semantic,
                    ):
                        changed_payload = self._payload_for_variant(
                            role,
                            identity,
                            placement,
                            changed_address,
                        )
                        with self.assertRaisesRegex(
                            AccountingValidationError,
                            _CORRECTION_ERROR,
                        ):
                            deep.accept_bank_statement_evidence(
                                self.case._command(
                                    changed_payload,
                                    reference,
                                    f"{role}-postal-{placement}-{semantic}",
                                ),
                                deep.posting.DATABASE_URL,
                                self.case.case.policy.tenant_reference,
                                artifact_store=store,
                            )

    def test_postal_source_values_remain_non_reversible_on_buyer_projection(self) -> None:
        """Postal facts affect digest identity without adding buyer-visible fields."""
        for role, identity in self.case.identities.items():
            for placement in ("institution", "branch"):
                with self.subTest(role=role, placement=placement):
                    rich_payload = self._payload(
                        role,
                        identity,
                        institution_address=(
                            _INSTITUTION_ADDRESS if placement == "institution" else None
                        ),
                        branch_address=(
                            _BRANCH_ADDRESS if placement == "branch" else None
                        ),
                    )
                    absent_payload = self._payload(
                        role,
                        identity,
                        institution_address=None,
                        branch_address=None,
                    )
                    rich = self._ingest_and_read_first_detail(
                        rich_payload,
                        f"{role}-{placement}-postal-rich",
                    )
                    absent = self._ingest_and_read_first_detail(
                        absent_payload,
                        f"{role}-{placement}-postal-absent",
                    )
                    evidence_key = self.case._evidence_key(role)
                    for projection in (rich, absent):
                        self._assert_sha256(projection[evidence_key])
                        self._assert_sha256(projection["source_detail_hash"])
                        self.assertEqual(
                            Decimal(str(projection["detail_amount"])),
                            Decimal("25000.00"),
                        )
                        self.assertEqual(projection["detail_currency_code"], "KRW")

                    self.assertNotEqual(rich[evidence_key], absent[evidence_key])
                    self.assertNotEqual(
                        rich["source_detail_hash"],
                        absent["source_detail_hash"],
                    )
                    rich_public = self._public_projection(rich, evidence_key)
                    absent_public = self._public_projection(absent, evidence_key)
                    self.assertEqual(rich_public, absent_public)

                    source_address = (
                        _INSTITUTION_ADDRESS
                        if placement == "institution"
                        else _BRANCH_ADDRESS
                    )
                    source_values = self._privacy_source_values(source_address)
                    buyer_values = set(self._scalar_leaves(rich_public))
                    self.assertTrue(source_values.isdisjoint(buyer_values))

    @classmethod
    def _address_variants(
        cls,
        address: dict[str, object],
    ) -> dict[str, dict[str, object]]:
        """Return independent value, presence, and ordered-line changes."""
        variants: dict[str, dict[str, object]] = {}

        address_type_value = copy.deepcopy(address)
        address_type = cls._address_type(address_type_value)
        address_type["code"] = "HOME"
        variants["address-type-code-value"] = address_type_value

        address_type_absent = copy.deepcopy(address)
        address_type_absent.pop("address_type")
        variants["address-type-absent"] = address_type_absent

        alternatives = {
            "care_of": "Alternate Care Of",
            "department": "Alternate Treasury",
            "sub_department": "Alternate Settlement",
            "street_name": "Alternate Ultimate Street",
            "building_number": "18",
            "building_name": "Alternate Ultimate House",
            "floor": "13",
            "unit_number": "1305",
            "post_box": "1801",
            "room": "1305B",
            "post_code": "10118",
            "town_name": "Alternate Ultimate City",
            "town_location_name": "Alternate Ultimate Centre",
            "district_name": "Alternate Ultimate District",
            "country_subdivision": "Alternate Ultimate State",
            "country": "FR",
        }
        for field, alternative in alternatives.items():
            changed = copy.deepcopy(address)
            changed[field] = alternative
            variants[f"{field}-value"] = changed

            absent = copy.deepcopy(address)
            absent.pop(field)
            variants[f"{field}-absent"] = absent

        lines = address.get("address_lines")
        if not isinstance(lines, list) or len(lines) != 2:
            raise AssertionError("focused PostalAddress27 fixture requires two AdrLine values")

        first_changed = copy.deepcopy(address)
        first_changed["address_lines"][0] = "Alternate first address line"
        variants["address-line-first-value"] = first_changed

        second_changed = copy.deepcopy(address)
        second_changed["address_lines"][1] = "Alternate second address line"
        variants["address-line-second-value"] = second_changed

        reversed_lines = copy.deepcopy(address)
        reversed_lines["address_lines"] = list(reversed(lines))
        variants["address-line-order"] = reversed_lines

        first_absent = copy.deepcopy(address)
        first_absent["address_lines"] = [lines[1]]
        variants["address-line-first-absent"] = first_absent

        second_absent = copy.deepcopy(address)
        second_absent["address_lines"] = [lines[0]]
        variants["address-line-second-absent"] = second_absent

        all_lines_absent = copy.deepcopy(address)
        all_lines_absent["address_lines"] = []
        variants["address-lines-all-absent"] = all_lines_absent
        return variants

    @staticmethod
    def _address_type(address: dict[str, object]) -> dict[str, object]:
        """Return a structured AddressType3Choice fixture or fail closed."""
        value = address.get("address_type")
        if not isinstance(value, dict):
            raise AssertionError("focused PostalAddress27 fixture requires AdrTp")
        return value

    @classmethod
    def _proprietary_address_type(
        cls,
        address: dict[str, object],
    ) -> dict[str, object]:
        """Return GenericIdentification30 under AdrTp/Prtry or fail closed."""
        address_type = cls._address_type(address)
        proprietary = address_type.get("proprietary")
        if not isinstance(proprietary, dict):
            raise AssertionError("focused AddressType3Choice requires Prtry")
        return proprietary

    def _payload_for_variant(
        self,
        role: str,
        identity: dict[str, str],
        placement: str,
        changed_address: dict[str, object] | None,
    ) -> bytes:
        """Change one institution or branch postal container while holding the other."""
        if placement == "institution":
            return self._payload(
                role,
                identity,
                institution_address=changed_address,
                branch_address=_BRANCH_ADDRESS,
            )
        if placement == "branch":
            return self._payload(
                role,
                identity,
                institution_address=_INSTITUTION_ADDRESS,
                branch_address=changed_address,
            )
        raise AssertionError(f"unsupported postal placement: {placement}")

    def _payload(
        self,
        role: str,
        identity: dict[str, str],
        *,
        institution_address: dict[str, object] | None,
        branch_address: dict[str, object] | None,
    ) -> bytes:
        """Insert institution and branch PostalAddress27 in schema sequence order."""
        payload = self.case._with_ultimate_agent(role, identity)

        if institution_address is not None:
            institution_needle = (
                f"                    <Nm>{identity['name']}</Nm>\n"
            ).encode("utf-8")
            if payload.count(institution_needle) != 1:
                raise AssertionError("ultimate institution name must be unique")
            payload = payload.replace(
                institution_needle,
                institution_needle + self._address_xml(institution_address),
                1,
            )

        if branch_address is not None:
            branch_needle = (
                f"                    <Nm>{identity['branch_name']}</Nm>\n"
            ).encode("utf-8")
            if payload.count(branch_needle) != 1:
                raise AssertionError("ultimate branch name must be unique")
            payload = payload.replace(
                branch_needle,
                branch_needle + self._address_xml(branch_address),
                1,
            )
        return payload

    @classmethod
    def _address_xml(cls, address: dict[str, object]) -> bytes:
        """Serialize full PostalAddress27 source semantics in V14 sequence order."""
        lines = ["                    <PstlAdr>\n"]
        address_type = address.get("address_type")
        if address_type is not None:
            if not isinstance(address_type, dict):
                raise AssertionError("AdrTp must be a structured choice")
            lines.append("                      <AdrTp>\n")
            code = address_type.get("code")
            proprietary = address_type.get("proprietary")
            if isinstance(code, str) and proprietary is None:
                lines.append(f"                        <Cd>{code}</Cd>\n")
            elif isinstance(proprietary, dict) and code is None:
                identifier = proprietary.get("id")
                issuer = proprietary.get("issuer")
                if not isinstance(identifier, str) or not isinstance(issuer, str):
                    raise AssertionError("AdrTp/Prtry requires Id and Issr")
                lines.extend(
                    [
                        "                        <Prtry>\n",
                        f"                          <Id>{identifier}</Id>\n",
                        f"                          <Issr>{issuer}</Issr>\n",
                    ]
                )
                scheme_name = proprietary.get("scheme_name")
                if scheme_name is not None:
                    if not isinstance(scheme_name, str):
                        raise AssertionError("AdrTp/Prtry/SchmeNm must be text")
                    lines.append(
                        f"                          <SchmeNm>{scheme_name}</SchmeNm>\n"
                    )
                lines.append("                        </Prtry>\n")
            else:
                raise AssertionError("AdrTp requires exactly one Cd or Prtry branch")
            lines.append("                      </AdrTp>\n")

        for field, tag in _SIMPLE_ADDRESS_FIELDS:
            value = address.get(field)
            if value is not None:
                if not isinstance(value, str):
                    raise AssertionError(f"PostalAddress27 {field} must be text")
                lines.append(f"                      <{tag}>{value}</{tag}>\n")

        address_lines = address.get("address_lines", [])
        if not isinstance(address_lines, list):
            raise AssertionError("PostalAddress27 AdrLine values must remain ordered")
        for line in address_lines:
            if not isinstance(line, str):
                raise AssertionError("PostalAddress27 AdrLine must be text")
            lines.append(f"                      <AdrLine>{line}</AdrLine>\n")
        lines.append("                    </PstlAdr>\n")
        return "".join(lines).encode("utf-8")

    @staticmethod
    def _statement(payload: bytes) -> object:
        """Parse one V14 statement through the supported normalization boundary."""
        return deep.parse_bank_statement_payload(
            payload,
            deep.CAMT053_MESSAGE_DEFINITION,
        )

    def _ingest_and_read_first_detail(
        self,
        payload: bytes,
        suffix: str,
    ) -> dict[str, object]:
        """Ingest one isolated statement and return its first buyer detail."""
        reference = self.case._register_statement_account(payload)
        accepted = deep.accept_bank_statement_evidence(
            self.case._command(payload, reference, f"{suffix}-{uuid.uuid4().hex}"),
            deep.posting.DATABASE_URL,
            self.case.case.policy.tenant_reference,
            artifact_store=MemoryArtifactStore(),
        )
        document = deep.lookup_bank_statement_entries(
            deep.posting.DATABASE_URL,
            self.case.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        return document["bank_statement_entries"][0]["entry_details"][0]

    @staticmethod
    def _public_projection(
        detail: dict[str, object],
        evidence_key: str,
    ) -> dict[str, object]:
        """Remove only server-owned evidence identities before buyer comparison."""
        projection = copy.deepcopy(detail)
        projection.pop(evidence_key, None)
        projection.pop("source_detail_hash", None)
        return projection

    @classmethod
    def _privacy_source_values(cls, address: dict[str, object]) -> set[str]:
        """Return unique source strings whose buyer disclosure would be reversible."""
        values = set(cls._scalar_leaves(address))
        values.discard("BIZZ")
        values.discard("DE")
        values.discard("17")
        values.discard("12")
        return values

    @classmethod
    def _scalar_leaves(cls, value: object) -> list[str]:
        """Return exact scalar leaves without substring-based privacy heuristics."""
        leaves: list[str] = []
        if isinstance(value, dict):
            for item in value.values():
                leaves.extend(cls._scalar_leaves(item))
        elif isinstance(value, list):
            for item in value:
                leaves.extend(cls._scalar_leaves(item))
        elif value is not None:
            leaves.append(str(value))
        return leaves

    def _assert_statement_evidence(
        self,
        statement: object,
        entry: object,
        detail: object,
        evidence_key: str,
    ) -> None:
        """Require canonical evidence identity and exact accounting facts."""
        for value in (
            getattr(detail, evidence_key),
            getattr(detail, "source_detail_hash"),
            getattr(entry, "source_entry_hash"),
            getattr(statement, "normalized_payload_hash"),
            getattr(statement, "account_identifier_hash"),
            getattr(statement, "entries")[1].source_entry_hash,
        ):
            self._assert_sha256(value)
        self._assert_financial_truth(entry, detail)

    @staticmethod
    def _assert_sha256(value: object) -> None:
        """Require one canonical SHA-256 identity."""
        if not isinstance(value, str) or _HASH_PATTERN.fullmatch(value) is None:
            raise AssertionError(f"expected canonical SHA-256 identity, got {value!r}")

    def _assert_financial_truth(self, entry: object, detail: object) -> None:
        """Keep postal provenance independent from exact accounting truth."""
        self.assertEqual(getattr(entry, "entry_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(entry, "entry_currency_code"), "KRW")
        self.assertEqual(getattr(detail, "detail_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(detail, "detail_currency_code"), "KRW")


if __name__ == "__main__":
    unittest.main()
