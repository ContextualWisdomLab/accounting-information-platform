"""PostgreSQL REDs for complete direct debtor/creditor private-party identification evidence."""

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


class BankStatementDebtorCreditorPartyPrivateIdentificationEvidenceRedTests(
    unittest.TestCase
):
    """Retain direct PersonIdentification18 as purpose-bound non-reversible evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare role-specific private-person variants and a safe PostgreSQL fixture."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.party_name = "Direct Private Person"
        self.base_private = {
            "date_and_place_of_birth": {
                "birth_date": "1980-05-17",
                "province_of_birth": "Seoul",
                "city_of_birth": "Seoul",
                "country_of_birth": "KR",
            },
            "identifiers": [
                {
                    "id": "DIRECT-PERSON-PRIMARY-001",
                    "scheme": {"code": "CCPT"},
                    "issuer": "Passport Authority",
                },
                {
                    "id": "DIRECT-PERSON-ADDITIONAL-001",
                    "scheme": {"code": "NIDN"},
                    "issuer": "National Identity Registry",
                },
            ],
        }
        self.variants = self._variants(self.base_private)

    def test_private_identification_fields_and_population_are_material_to_identity(self) -> None:
        """Birth and repeated person identifiers independently change evidence identity."""
        for role in ("debtor", "creditor"):
            target_index = self._target_entry_index(role)
            untouched_index = 1 - target_index
            baseline = parse_bank_statement_payload(
                self._with_role_private(role, self.base_private),
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

            for semantic, private in self.variants.items():
                with self.subTest(role=role, semantic=semantic):
                    changed = parse_bank_statement_payload(
                        self._with_role_private(role, private),
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

    def test_person_scheme_choice_discriminator_is_material_for_same_scalar(self) -> None:
        """Cd=CCPT and Prtry=CCPT remain distinct PersonIdentificationSchemeName1Choice values."""
        for role in ("debtor", "creditor"):
            for position in (0, 1):
                with self.subTest(role=role, position=position):
                    coded = copy.deepcopy(self.base_private)
                    proprietary = copy.deepcopy(self.base_private)
                    self._identifiers(coded)[position]["scheme"] = {"code": "CCPT"}
                    self._identifiers(proprietary)[position]["scheme"] = {
                        "proprietary": "CCPT"
                    }
                    left = parse_bank_statement_payload(
                        self._with_role_private(role, coded),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    right = parse_bank_statement_payload(
                        self._with_role_private(role, proprietary),
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
                    self.assertNotEqual(
                        left.normalized_payload_hash,
                        right.normalized_payload_hash,
                    )

    def test_private_identification_xml_layout_is_representation_only(self) -> None:
        """Whitespace inside person SchmeNm changes raw bytes, not admitted semantics."""
        baseline = self._with_role_private("debtor", self.base_private)
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
            self._assert_sha256(value)

        self.assertNotEqual(left.source_artifact_hash, right.source_artifact_hash)
        self.assertEqual(
            left_entry.counterparty_evidence_hash,
            right_entry.counterparty_evidence_hash,
        )
        self.assertEqual(left_detail.source_detail_hash, right_detail.source_detail_hash)
        self.assertEqual(left_entry.source_entry_hash, right_entry.source_entry_hash)
        self.assertEqual(left.normalized_payload_hash, right.normalized_payload_hash)

    def test_private_identification_changes_reach_complete_correction_boundary(self) -> None:
        """Accepted direct-person provenance cannot be silently replaced by replay."""
        for role in ("debtor", "creditor"):
            baseline = self._with_role_private(role, self.base_private)
            reference = self._register_statement_account(baseline)
            store = MemoryArtifactStore()
            accept_bank_statement_evidence(
                self._command(baseline, reference, f"{role}-private-baseline"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=store,
            )
            for semantic, private in self.variants.items():
                with self.subTest(role=role, semantic=semantic):
                    changed = self._with_role_private(role, private)
                    with self.assertRaisesRegex(
                        AccountingValidationError, _CORRECTION_ERROR
                    ):
                        accept_bank_statement_evidence(
                            self._command(
                                changed,
                                reference,
                                f"{role}-private-{semantic}",
                            ),
                            posting.DATABASE_URL,
                            self.case.policy.tenant_reference,
                            artifact_store=store,
                        )

    def test_buyer_projection_keeps_private_identification_non_reversible(self) -> None:
        """Private-person provenance changes internal identity without leaking source PII."""
        private_variant = copy.deepcopy(self.base_private)
        private_variant["date_and_place_of_birth"] = {
            "birth_date": "1977-11-03",
            "province_of_birth": "Busan",
            "city_of_birth": "Busan",
            "country_of_birth": "KR",
        }
        private_identifiers = self._identifiers(private_variant)
        private_identifiers[0]["id"] = "DIRECT-PRIVATE-PASSPORT-7781"
        private_identifiers[0]["scheme"] = {
            "proprietary": "DIRECT_PRIVATE_SCHEME_ALPHA"
        }
        private_identifiers[0]["issuer"] = "Direct Private Passport Registry"
        private_identifiers[1]["id"] = "DIRECT-PRIVATE-NATIONAL-7782"
        private_identifiers[1]["scheme"] = {
            "proprietary": "DIRECT_PRIVATE_SCHEME_BETA"
        }
        private_identifiers[1]["issuer"] = "Direct Private National Registry"

        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                baseline = self._ingest_and_read_target_entry(
                    role,
                    self._with_role_private(role, self.base_private),
                    f"{role}-private-public-baseline",
                )
                private = self._ingest_and_read_target_entry(
                    role,
                    self._with_role_private(role, private_variant),
                    f"{role}-private-sensitive",
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
                    baseline["source_entry_hash"],
                    private["source_entry_hash"],
                )
                self.assertEqual(
                    self._public_projection(baseline),
                    self._public_projection(private),
                )

                serialized = json.dumps(private, sort_keys=True, default=str)
                for source_value in (
                    "1977-11-03",
                    "Busan",
                    "DIRECT-PRIVATE-PASSPORT-7781",
                    "DIRECT_PRIVATE_SCHEME_ALPHA",
                    "Direct Private Passport Registry",
                    "DIRECT-PRIVATE-NATIONAL-7782",
                    "DIRECT_PRIVATE_SCHEME_BETA",
                    "Direct Private National Registry",
                ):
                    self.assertNotIn(source_value, serialized)

    @classmethod
    def _variants(
        cls, base: dict[str, object]
    ) -> dict[str, dict[str, object]]:
        """Return independent birth, identifier, scheme, issuer, and repetition variants."""
        variants: dict[str, dict[str, object]] = {}

        birth_date = copy.deepcopy(base)
        cls._birth(birth_date)["birth_date"] = "1981-05-17"
        variants["birth-date-value"] = birth_date

        province = copy.deepcopy(base)
        cls._birth(province)["province_of_birth"] = "Busan"
        variants["province-of-birth-value"] = province

        province_absent = copy.deepcopy(base)
        cls._birth(province_absent).pop("province_of_birth")
        variants["province-of-birth-absent"] = province_absent

        city = copy.deepcopy(base)
        cls._birth(city)["city_of_birth"] = "Busan"
        variants["city-of-birth-value"] = city

        country = copy.deepcopy(base)
        cls._birth(country)["country_of_birth"] = "DE"
        variants["country-of-birth-value"] = country

        birth_absent = copy.deepcopy(base)
        birth_absent.pop("date_and_place_of_birth")
        variants["date-and-place-of-birth-absent"] = birth_absent

        first_id = copy.deepcopy(base)
        cls._identifiers(first_id)[0]["id"] = "DIRECT-PERSON-PRIMARY-002"
        variants["first-id-value"] = first_id

        first_scheme_value = copy.deepcopy(base)
        cls._identifiers(first_scheme_value)[0]["scheme"] = {"code": "NIDN"}
        variants["first-scheme-value"] = first_scheme_value

        first_scheme_choice = copy.deepcopy(base)
        cls._identifiers(first_scheme_choice)[0]["scheme"] = {
            "proprietary": "CCPT"
        }
        variants["first-scheme-choice"] = first_scheme_choice

        first_scheme_absent = copy.deepcopy(base)
        cls._identifiers(first_scheme_absent)[0].pop("scheme")
        variants["first-scheme-absent"] = first_scheme_absent

        first_issuer = copy.deepcopy(base)
        cls._identifiers(first_issuer)[0]["issuer"] = "Alternate Passport Authority"
        variants["first-issuer-value"] = first_issuer

        first_issuer_absent = copy.deepcopy(base)
        cls._identifiers(first_issuer_absent)[0].pop("issuer")
        variants["first-issuer-absent"] = first_issuer_absent

        additional_id = copy.deepcopy(base)
        cls._identifiers(additional_id)[1]["id"] = "DIRECT-PERSON-ADDITIONAL-002"
        variants["additional-id-value"] = additional_id

        additional_scheme_value = copy.deepcopy(base)
        cls._identifiers(additional_scheme_value)[1]["scheme"] = {"code": "CCPT"}
        variants["additional-scheme-value"] = additional_scheme_value

        additional_scheme_choice = copy.deepcopy(base)
        cls._identifiers(additional_scheme_choice)[1]["scheme"] = {
            "proprietary": "NIDN"
        }
        variants["additional-scheme-choice"] = additional_scheme_choice

        additional_scheme_absent = copy.deepcopy(base)
        cls._identifiers(additional_scheme_absent)[1].pop("scheme")
        variants["additional-scheme-absent"] = additional_scheme_absent

        additional_issuer = copy.deepcopy(base)
        cls._identifiers(additional_issuer)[1]["issuer"] = (
            "Alternate National Identity Registry"
        )
        variants["additional-issuer-value"] = additional_issuer

        additional_issuer_absent = copy.deepcopy(base)
        cls._identifiers(additional_issuer_absent)[1].pop("issuer")
        variants["additional-issuer-absent"] = additional_issuer_absent

        additional_removed = copy.deepcopy(base)
        cls._identifiers(additional_removed).pop()
        variants["additional-identifier-removed"] = additional_removed
        return variants

    def _with_role_private(
        self,
        role: str,
        private: dict[str, object],
    ) -> bytes:
        """Return valid direct Pty/PrvtId evidence in schema order."""
        party_body = self._party_xml(private)
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

    def _party_xml(self, private: dict[str, object]) -> str:
        """Serialize PartyIdentification272/Id/PrvtId/PersonIdentification18."""
        birth = private.get("date_and_place_of_birth")
        birth_xml = "" if birth is None else self._birth_xml(birth)
        identifiers = self._identifiers(private)
        if not identifiers:
            raise AssertionError("private-identification RED requires at least one Othr")
        other_xml = "".join(self._other_xml(value) for value in identifiers)
        return (
            "                <Pty>\n"
            f"                  <Nm>{self.party_name}</Nm>\n"
            "                  <Id>\n"
            "                    <PrvtId>\n"
            + birth_xml
            + other_xml
            + "                    </PrvtId>\n"
            "                  </Id>\n"
            "                </Pty>"
        )

    @staticmethod
    def _birth_xml(value: object) -> str:
        """Serialize DateAndPlaceOfBirth1 in canonical element order."""
        if not isinstance(value, dict):
            raise AssertionError("date/place of birth must be a mapping")
        province = value.get("province_of_birth")
        province_xml = (
            ""
            if province is None
            else f"                        <PrvcOfBirth>{province}</PrvcOfBirth>\n"
        )
        return (
            "                      <DtAndPlcOfBirth>\n"
            f"                        <BirthDt>{value['birth_date']}</BirthDt>\n"
            + province_xml
            + f"                        <CityOfBirth>{value['city_of_birth']}</CityOfBirth>\n"
            f"                        <CtryOfBirth>{value['country_of_birth']}</CtryOfBirth>\n"
            "                      </DtAndPlcOfBirth>\n"
        )

    @classmethod
    def _other_xml(cls, value: dict[str, object]) -> str:
        """Serialize one GenericPersonIdentification2 in Id/SchmeNm/Issr order."""
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
        """Serialize PersonIdentificationSchemeName1Choice without erasing its branch."""
        if not isinstance(value, dict):
            raise AssertionError("person identification scheme must be a mapping")
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

    @staticmethod
    def _birth(value: dict[str, object]) -> dict[str, object]:
        """Return mutable date/place-of-birth evidence from one private-party mapping."""
        birth = value.get("date_and_place_of_birth")
        if not isinstance(birth, dict):
            raise AssertionError("private-identification RED requires date/place of birth")
        return birth

    @staticmethod
    def _identifiers(value: dict[str, object]) -> list[dict[str, object]]:
        """Return mutable repeated person identifiers from one private-party mapping."""
        identifiers = value.get("identifiers")
        if not isinstance(identifiers, list):
            raise AssertionError("private-identification RED requires identifier population")
        if any(not isinstance(item, dict) for item in identifiers):
            raise AssertionError("every private identifier must be a mapping")
        return identifiers

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
                f"direct-party-private-identification-{suffix}-{uuid.uuid4().hex}"
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
        """Keep private-party provenance outside accounting measurement truth."""
        self.assertEqual(entry.entry_amount, self._expected_entry_amount(role))
        self.assertEqual(entry.entry_currency_code, "KRW")
        self.assertEqual(detail.detail_amount, self._expected_detail_amount(role))
        self.assertEqual(detail.detail_currency_code, "KRW")

    def _assert_sha256(self, value: object) -> None:
        """Require one canonical SHA-256 evidence identity."""
        self.assertIsInstance(value, str)
        self.assertRegex(value, _HASH_PATTERN)
