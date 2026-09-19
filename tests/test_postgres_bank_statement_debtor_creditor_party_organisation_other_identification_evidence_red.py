"""PostgreSQL REDs for direct debtor/creditor organisation Othr evidence in camt.053."""

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
    accept_bank_account_record,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
    lookup_bank_statement_entries,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDebtorCreditorPartyOrganisationOtherIdentificationEvidenceRedTests(
    unittest.TestCase
):
    """Retain direct-party GenericOrganisationIdentification3 as private evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare role-specific Othr variants and a safe PostgreSQL fixture."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.party_name = "Direct Organisation Other Party"
        self.base_identifiers = [
            {
                "id": "DIRECT-ORG-PRIMARY-001",
                "scheme": {"code": "BANK"},
                "issuer": "Direct Relationship Registry",
            },
            {
                "id": "DIRECT-ORG-ADDITIONAL-001",
                "scheme": {"code": "BANK"},
                "issuer": "Direct Tax Registry",
            },
        ]
        self.variants = self._variants(self.base_identifiers)

    def test_other_identification_fields_and_population_are_material_to_identity(self) -> None:
        """Othr ID, scheme choice/value, issuer, and repeated population change evidence."""
        for role in ("debtor", "creditor"):
            target_index = self._target_entry_index(role)
            untouched_index = 1 - target_index
            baseline = parse_bank_statement_payload(
                self._with_role_identifiers(role, self.base_identifiers),
                CAMT053_MESSAGE_DEFINITION,
            )
            baseline_entry = baseline.entries[target_index]
            baseline_detail = baseline_entry.entry_details[0]
            self._assert_financial_truth(role, baseline_entry, baseline_detail)
            for value in (
                baseline_entry.counterparty_evidence_hash,
                baseline_detail.source_detail_hash,
                baseline_entry.source_entry_hash,
                baseline.normalized_payload_hash,
                baseline.account_identifier_hash,
                baseline.entries[untouched_index].source_entry_hash,
            ):
                self._assert_sha256(value)

            for semantic, identifiers in self.variants.items():
                with self.subTest(role=role, semantic=semantic):
                    changed = parse_bank_statement_payload(
                        self._with_role_identifiers(role, identifiers),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    changed_entry = changed.entries[target_index]
                    changed_detail = changed_entry.entry_details[0]
                    self._assert_financial_truth(role, changed_entry, changed_detail)
                    for value in (
                        changed_entry.counterparty_evidence_hash,
                        changed_detail.source_detail_hash,
                        changed_entry.source_entry_hash,
                        changed.normalized_payload_hash,
                        changed.account_identifier_hash,
                        changed.entries[untouched_index].source_entry_hash,
                    ):
                        self._assert_sha256(value)

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

    def test_scheme_choice_discriminator_is_material_for_same_scalar(self) -> None:
        """Cd=BANK and Prtry=BANK remain distinct OrganisationIdentificationSchemeName1Choice values."""
        for role in ("debtor", "creditor"):
            for position in (0, 1):
                with self.subTest(role=role, position=position):
                    coded = copy.deepcopy(self.base_identifiers)
                    proprietary = copy.deepcopy(self.base_identifiers)
                    coded[position]["scheme"] = {"code": "BANK"}
                    proprietary[position]["scheme"] = {"proprietary": "BANK"}
                    left = parse_bank_statement_payload(
                        self._with_role_identifiers(role, coded),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    right = parse_bank_statement_payload(
                        self._with_role_identifiers(role, proprietary),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    target_index = self._target_entry_index(role)
                    left_entry = left.entries[target_index]
                    right_entry = right.entries[target_index]
                    left_detail = left_entry.entry_details[0]
                    right_detail = right_entry.entry_details[0]
                    self._assert_financial_truth(role, left_entry, left_detail)
                    self._assert_financial_truth(role, right_entry, right_detail)
                    for value in (
                        left_entry.counterparty_evidence_hash,
                        right_entry.counterparty_evidence_hash,
                        left_detail.source_detail_hash,
                        right_detail.source_detail_hash,
                        left_entry.source_entry_hash,
                        right_entry.source_entry_hash,
                        left.normalized_payload_hash,
                        right.normalized_payload_hash,
                    ):
                        self._assert_sha256(value)
                    self.assertNotEqual(
                        left_entry.counterparty_evidence_hash,
                        right_entry.counterparty_evidence_hash,
                    )
                    self.assertNotEqual(
                        left_detail.source_detail_hash,
                        right_detail.source_detail_hash,
                    )
                    self.assertNotEqual(
                        left_entry.source_entry_hash,
                        right_entry.source_entry_hash,
                    )
                    self.assertNotEqual(left.normalized_payload_hash, right.normalized_payload_hash)

    def test_other_identification_xml_layout_is_representation_only(self) -> None:
        """Whitespace inside SchmeNm changes source bytes, not admitted semantics."""
        baseline = self._with_role_identifiers("debtor", self.base_identifiers)
        formatted = baseline.replace(
            b"                        <SchmeNm>\n",
            b"                        <SchmeNm>\n                          \n",
            1,
        )
        self.assertNotEqual(baseline, formatted)

        left = parse_bank_statement_payload(baseline, CAMT053_MESSAGE_DEFINITION)
        right = parse_bank_statement_payload(formatted, CAMT053_MESSAGE_DEFINITION)
        left_entry = left.entries[0]
        right_entry = right.entries[0]
        left_detail = left_entry.entry_details[0]
        right_detail = right_entry.entry_details[0]
        for value in (
            left_entry.counterparty_evidence_hash,
            right_entry.counterparty_evidence_hash,
            left_detail.source_detail_hash,
            right_detail.source_detail_hash,
            left_entry.source_entry_hash,
            right_entry.source_entry_hash,
            left.normalized_payload_hash,
            right.normalized_payload_hash,
        ):
            self._assert_sha256(value)

        self._assert_sha256(left.source_artifact_hash)
        self._assert_sha256(right.source_artifact_hash)
        self.assertNotEqual(left.source_artifact_hash, right.source_artifact_hash)
        self.assertEqual(
            left_entry.counterparty_evidence_hash,
            right_entry.counterparty_evidence_hash,
        )
        self.assertEqual(left_detail.source_detail_hash, right_detail.source_detail_hash)
        self.assertEqual(left_entry.source_entry_hash, right_entry.source_entry_hash)
        self.assertEqual(left.normalized_payload_hash, right.normalized_payload_hash)

    def test_other_identification_changes_reach_complete_correction_boundary(self) -> None:
        """Accepted direct-party Othr evidence cannot be silently replaced by replay."""
        for role in ("debtor", "creditor"):
            baseline = self._with_role_identifiers(role, self.base_identifiers)
            reference = self._register_statement_account(baseline)
            store = MemoryArtifactStore()
            accept_bank_statement_evidence(
                self._command(baseline, reference, f"{role}-organisation-other-baseline"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=store,
            )
            for semantic, identifiers in self.variants.items():
                with self.subTest(role=role, semantic=semantic):
                    changed = self._with_role_identifiers(role, identifiers)
                    with self.assertRaisesRegex(
                        AccountingValidationError, _CORRECTION_ERROR
                    ):
                        accept_bank_statement_evidence(
                            self._command(
                                changed,
                                reference,
                                f"{role}-organisation-other-{semantic}",
                            ),
                            posting.DATABASE_URL,
                            self.case.policy.tenant_reference,
                            artifact_store=store,
                        )

    def test_buyer_projection_keeps_other_identification_non_reversible(self) -> None:
        """Rich Othr provenance changes internal evidence without leaking source identifiers."""
        privacy_identifiers = copy.deepcopy(self.base_identifiers)
        privacy_identifiers[0]["id"] = "DIRECT-PRIVATE-ID-7781"
        privacy_identifiers[0]["scheme"] = {
            "proprietary": "DIRECT_PRIVATE_SCHEME_ALPHA"
        }
        privacy_identifiers[0]["issuer"] = "Direct Private Registry Alpha"
        privacy_identifiers[1]["id"] = "DIRECT-PRIVATE-ID-7782"
        privacy_identifiers[1]["scheme"] = {
            "proprietary": "DIRECT_PRIVATE_SCHEME_BETA"
        }
        privacy_identifiers[1]["issuer"] = "Direct Private Registry Beta"

        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                baseline = self._ingest_and_read_target_entry(
                    role,
                    self._with_role_identifiers(role, self.base_identifiers),
                    f"{role}-organisation-other-public-baseline",
                )
                private = self._ingest_and_read_target_entry(
                    role,
                    self._with_role_identifiers(role, privacy_identifiers),
                    f"{role}-organisation-other-private",
                )
                for projection in (baseline, private):
                    self._assert_sha256(projection["counterparty_evidence_hash"])
                    self._assert_sha256(projection["source_entry_hash"])
                    self.assertEqual(
                        Decimal(str(projection["entry_amount"])),
                        self._expected_entry_amount(role),
                    )
                    self.assertEqual(projection["entry_currency_code"], "KRW")
                    first_detail = projection["entry_details"][0]
                    self._assert_sha256(first_detail["source_detail_hash"])
                    self.assertEqual(
                        Decimal(str(first_detail["detail_amount"])),
                        self._expected_detail_amount(role),
                    )
                    self.assertEqual(first_detail["detail_currency_code"], "KRW")

                self.assertNotEqual(
                    baseline["counterparty_evidence_hash"],
                    private["counterparty_evidence_hash"],
                )
                self.assertNotEqual(
                    baseline["entry_details"][0]["source_detail_hash"],
                    private["entry_details"][0]["source_detail_hash"],
                )
                self.assertNotEqual(
                    baseline["source_entry_hash"], private["source_entry_hash"]
                )
                self.assertEqual(
                    self._public_projection(baseline),
                    self._public_projection(private),
                )

                serialized = json.dumps(private, sort_keys=True, default=str)
                for source_value in (
                    "DIRECT-PRIVATE-ID-7781",
                    "DIRECT_PRIVATE_SCHEME_ALPHA",
                    "Direct Private Registry Alpha",
                    "DIRECT-PRIVATE-ID-7782",
                    "DIRECT_PRIVATE_SCHEME_BETA",
                    "Direct Private Registry Beta",
                ):
                    self.assertNotIn(source_value, serialized)

    @classmethod
    def _variants(
        cls, base: list[dict[str, object]]
    ) -> dict[str, list[dict[str, object]]]:
        """Return independent first/repeated Othr identifier, scheme, issuer, and population variants."""
        variants: dict[str, list[dict[str, object]]] = {}

        first_id = copy.deepcopy(base)
        first_id[0]["id"] = "DIRECT-ORG-PRIMARY-002"
        variants["first-id-value"] = first_id

        first_scheme_value = copy.deepcopy(base)
        first_scheme_value[0]["scheme"] = {"code": "DUNS"}
        variants["first-scheme-value"] = first_scheme_value

        first_scheme_choice = copy.deepcopy(base)
        first_scheme_choice[0]["scheme"] = {"proprietary": "BANK"}
        variants["first-scheme-choice"] = first_scheme_choice

        first_scheme_proprietary = copy.deepcopy(base)
        first_scheme_proprietary[0]["scheme"] = {
            "proprietary": "DIRECT_ORG_SCHEME_ALPHA"
        }
        variants["first-scheme-proprietary-value"] = first_scheme_proprietary

        first_scheme_absent = copy.deepcopy(base)
        first_scheme_absent[0].pop("scheme")
        variants["first-scheme-absent"] = first_scheme_absent

        first_issuer_value = copy.deepcopy(base)
        first_issuer_value[0]["issuer"] = "Alternate Direct Relationship Registry"
        variants["first-issuer-value"] = first_issuer_value

        first_issuer_absent = copy.deepcopy(base)
        first_issuer_absent[0].pop("issuer")
        variants["first-issuer-absent"] = first_issuer_absent

        additional_id = copy.deepcopy(base)
        additional_id[1]["id"] = "DIRECT-ORG-ADDITIONAL-002"
        variants["additional-id-value"] = additional_id

        additional_scheme_value = copy.deepcopy(base)
        additional_scheme_value[1]["scheme"] = {"code": "DUNS"}
        variants["additional-scheme-value"] = additional_scheme_value

        additional_scheme_choice = copy.deepcopy(base)
        additional_scheme_choice[1]["scheme"] = {"proprietary": "BANK"}
        variants["additional-scheme-choice"] = additional_scheme_choice

        additional_scheme_proprietary = copy.deepcopy(base)
        additional_scheme_proprietary[1]["scheme"] = {
            "proprietary": "DIRECT_ORG_SCHEME_BETA"
        }
        variants["additional-scheme-proprietary-value"] = additional_scheme_proprietary

        additional_scheme_absent = copy.deepcopy(base)
        additional_scheme_absent[1].pop("scheme")
        variants["additional-scheme-absent"] = additional_scheme_absent

        additional_issuer_value = copy.deepcopy(base)
        additional_issuer_value[1]["issuer"] = "Alternate Direct Tax Registry"
        variants["additional-issuer-value"] = additional_issuer_value

        additional_issuer_absent = copy.deepcopy(base)
        additional_issuer_absent[1].pop("issuer")
        variants["additional-issuer-absent"] = additional_issuer_absent

        additional_removed = copy.deepcopy(base)
        additional_removed.pop()
        variants["additional-identifier-removed"] = additional_removed
        return variants

    def _with_role_identifiers(
        self,
        role: str,
        identifiers: list[dict[str, object]],
    ) -> bytes:
        """Return valid direct Pty/OrgId evidence with repeated GenericOrganisationIdentification3."""
        party_body = self._party_xml(identifiers)
        if role == "debtor":
            marker = (
                "              <Dbtr>\n"
                "                <Pty>\n"
                "                  <Nm>Counterparty One</Nm>\n"
                "                </Pty>\n"
                "              </Dbtr>"
            )
            self.assertEqual(self.fixture.count(marker), 1)
            replacement = (
                "              <Dbtr>\n"
                f"{party_body}\n"
                "              </Dbtr>"
            )
        elif role == "creditor":
            marker = (
                "            <AmtDtls>\n"
                "              <TxAmt>\n"
                '                <Amt Ccy="KRW">6000.00</Amt>\n'
                "              </TxAmt>\n"
                "            </AmtDtls>\n"
                "            <RmtInf>"
            )
            self.assertEqual(self.fixture.count(marker), 1)
            replacement = (
                "            <AmtDtls>\n"
                "              <TxAmt>\n"
                '                <Amt Ccy="KRW">6000.00</Amt>\n'
                "              </TxAmt>\n"
                "            </AmtDtls>\n"
                "            <RltdPties>\n"
                "              <Cdtr>\n"
                f"{party_body}\n"
                "              </Cdtr>\n"
                "            </RltdPties>\n"
                "            <RmtInf>"
            )
        else:
            raise AssertionError(f"unsupported role: {role}")
        return self.fixture.replace(marker, replacement, 1).encode("utf-8")

    def _party_xml(self, identifiers: list[dict[str, object]]) -> str:
        """Serialize PartyIdentification272/OrgId/Othr in schema order."""
        if not identifiers:
            raise AssertionError("organisation Othr RED requires at least one identifier")
        other_xml = "".join(self._other_xml(value) for value in identifiers)
        return (
            "                <Pty>\n"
            f"                  <Nm>{self.party_name}</Nm>\n"
            "                  <Id>\n"
            "                    <OrgId>\n"
            + other_xml
            + "                    </OrgId>\n"
            "                  </Id>\n"
            "                </Pty>"
        )

    @classmethod
    def _other_xml(cls, value: dict[str, object]) -> str:
        """Serialize one GenericOrganisationIdentification3 in Id/SchmeNm/Issr order."""
        identifier = str(value["id"])
        scheme = value.get("scheme")
        issuer = value.get("issuer")
        scheme_xml = "" if scheme is None else cls._scheme_xml(scheme)
        issuer_xml = (
            "" if issuer is None else f"                        <Issr>{issuer}</Issr>\n"
        )
        return (
            "                      <Othr>\n"
            f"                        <Id>{identifier}</Id>\n"
            + scheme_xml
            + issuer_xml
            + "                      </Othr>\n"
        )

    @staticmethod
    def _scheme_xml(value: object) -> str:
        """Serialize OrganisationIdentificationSchemeName1Choice without erasing its branch."""
        if not isinstance(value, dict):
            raise AssertionError("organisation identification scheme must be a mapping")
        if set(value) == {"code"}:
            choice = f"                          <Cd>{value['code']}</Cd>\n"
        elif set(value) == {"proprietary"}:
            choice = f"                          <Prtry>{value['proprietary']}</Prtry>\n"
        else:
            raise AssertionError("scheme must contain exactly one Cd or Prtry branch")
        return (
            "                        <SchmeNm>\n"
            + choice
            + "                        </SchmeNm>\n"
        )

    def _register_statement_account(self, payload: bytes) -> str:
        """Register only the statement-owner account required by supported ingest."""
        parsed = parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
        reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": reference,
                "account_currency_code": parsed.account_currency_code,
                "account_identifier_hash": parsed.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        return reference

    def _ingest_and_read_target_entry(
        self,
        role: str,
        payload: bytes,
        suffix: str,
    ) -> dict[str, object]:
        """Ingest on an isolated owner account and return the role's entry projection."""
        reference = self._register_statement_account(payload)
        accepted = accept_bank_statement_evidence(
            self._command(payload, reference, suffix),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=MemoryArtifactStore(),
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        return document["bank_statement_entries"][self._target_entry_index(role)]

    @staticmethod
    def _public_projection(entry: dict[str, object]) -> dict[str, object]:
        """Remove only server/internal evidence identifiers before buyer comparison."""
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

    def _command(
        self,
        payload: bytes,
        bank_account_reference: str,
        suffix: str,
    ) -> dict[str, object]:
        """Return one supported ingest command with a unique tenant-scoped replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": bank_account_reference,
            "ingestion_idempotency_key": (
                f"direct-party-organisation-other-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

    @staticmethod
    def _target_entry_index(role: str) -> int:
        """Map debtor evidence to CRDT entry and creditor evidence to DBIT entry."""
        if role == "debtor":
            return 0
        if role == "creditor":
            return 1
        raise AssertionError(f"unsupported role: {role}")

    @staticmethod
    def _expected_entry_amount(role: str) -> Decimal:
        """Return canonical entry amount for the targeted fixture role."""
        return Decimal("25000.00") if role == "debtor" else Decimal("10000.00")

    @staticmethod
    def _expected_detail_amount(role: str) -> Decimal:
        """Return canonical first-detail amount for the targeted fixture role."""
        return Decimal("25000.00") if role == "debtor" else Decimal("6000.00")

    def _assert_financial_truth(self, role: str, entry: object, detail: object) -> None:
        """Keep organisation identifier provenance outside accounting measurement truth."""
        self.assertEqual(entry.entry_amount, self._expected_entry_amount(role))
        self.assertEqual(entry.entry_currency_code, "KRW")
        self.assertEqual(detail.detail_amount, self._expected_detail_amount(role))
        self.assertEqual(detail.detail_currency_code, "KRW")

    def _assert_sha256(self, value: object) -> None:
        """Require one canonical SHA-256 evidence identity."""
        self.assertIsInstance(value, str)
        self.assertRegex(value, _HASH_PATTERN)
