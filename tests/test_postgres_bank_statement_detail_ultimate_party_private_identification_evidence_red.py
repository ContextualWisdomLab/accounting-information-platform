"""PostgreSQL REDs for complete ultimate-party private identification evidence."""

from __future__ import annotations

import copy
import re
import unittest
import uuid
from decimal import Decimal
from typing import Iterable

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


class BankStatementDetailUltimatePartyPrivateIdentificationEvidenceRedTests(
    unittest.TestCase
):
    """Retain ultimate PersonIdentification18 as non-reversible evidence only."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one isolated PostgreSQL fixture and rich private-person variants."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.party_name = "Ultimate Private Person"
        self.base_private: dict[str, object] = {
            "date_and_place_of_birth": {
                "birth_date": "1980-05-17",
                "province_of_birth": "Seoul",
                "city_of_birth": "Seoul",
                "country_of_birth": "KR",
            },
            "identifiers": [
                {
                    "id": "ULTIMATE-PERSON-PRIMARY-001",
                    "scheme": {"code": "CCPT"},
                    "issuer": "Ultimate Passport Authority",
                },
                {
                    "id": "ULTIMATE-PERSON-ADDITIONAL-001",
                    "scheme": {"code": "NIDN"},
                    "issuer": "Ultimate National Identity Registry",
                },
            ],
        }
        self.variants = self._variants(self.base_private)

    def test_private_fields_choices_presence_and_repetition_are_material(self) -> None:
        """Birth data and every repeated private identifier semantic change evidence."""
        for role in ("ultimate_debtor", "ultimate_creditor"):
            baseline = parse_bank_statement_payload(
                self._with_ultimate_private(role, self.base_private),
                CAMT053_MESSAGE_DEFINITION,
            )
            baseline_entry = baseline.entries[0]
            baseline_detail = baseline_entry.entry_details[0]
            evidence_key = self._evidence_key(role)
            baseline_role_hash = getattr(baseline_detail, evidence_key)
            self._assert_financial_truth(baseline_entry, baseline_detail)
            for value in (
                baseline_role_hash,
                baseline_detail.source_detail_hash,
                baseline_entry.source_entry_hash,
                baseline.normalized_payload_hash,
                baseline.account_identifier_hash,
                baseline.entries[1].source_entry_hash,
            ):
                self._assert_sha256(value)

            for semantic, private in self.variants.items():
                with self.subTest(role=role, semantic=semantic):
                    changed = parse_bank_statement_payload(
                        self._with_ultimate_private(role, private),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    changed_entry = changed.entries[0]
                    changed_detail = changed_entry.entry_details[0]
                    changed_role_hash = getattr(changed_detail, evidence_key)
                    self._assert_financial_truth(changed_entry, changed_detail)
                    for value in (
                        changed_role_hash,
                        changed_detail.source_detail_hash,
                        changed_entry.source_entry_hash,
                        changed.normalized_payload_hash,
                        changed.account_identifier_hash,
                        changed.entries[1].source_entry_hash,
                    ):
                        self._assert_sha256(value)

                    self.assertNotEqual(baseline_role_hash, changed_role_hash)
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

    def test_private_scheme_whitespace_is_representation_only(self) -> None:
        """Whitespace inside ultimate-person SchmeNm changes bytes, not semantics."""
        for role in ("ultimate_debtor", "ultimate_creditor"):
            with self.subTest(role=role):
                baseline = self._with_ultimate_private(role, self.base_private)
                needle = b"                        <SchmeNm>\n"
                self.assertGreaterEqual(baseline.count(needle), 1)
                formatted = baseline.replace(
                    needle,
                    needle + b"                          \n",
                    1,
                )
                self.assertNotEqual(baseline, formatted)
                left = parse_bank_statement_payload(
                    baseline, CAMT053_MESSAGE_DEFINITION
                )
                right = parse_bank_statement_payload(
                    formatted, CAMT053_MESSAGE_DEFINITION
                )
                left_entry = left.entries[0]
                right_entry = right.entries[0]
                left_detail = left_entry.entry_details[0]
                right_detail = right_entry.entry_details[0]
                evidence_key = self._evidence_key(role)
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
                    self._assert_sha256(value)

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

    def test_every_private_material_variant_reaches_correction_boundary(self) -> None:
        """Accepted ultimate-person provenance cannot be silently replaced by replay."""
        for role in ("ultimate_debtor", "ultimate_creditor"):
            baseline = self._with_ultimate_private(role, self.base_private)
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
                    changed = self._with_ultimate_private(role, private)
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

    def test_buyer_projection_keeps_private_source_values_non_reversible(self) -> None:
        """Private source values affect evidence identity without becoming buyer fields."""
        sensitive = copy.deepcopy(self.base_private)
        birth = self._birth(sensitive)
        birth.update(
            {
                "birth_date": "1977-11-03",
                "province_of_birth": "Canterbury",
                "city_of_birth": "Christchurch",
                "country_of_birth": "NZ",
            }
        )
        identifiers = self._identifiers(sensitive)
        identifiers[0].update(
            {
                "id": "ULTIMATE-PRIVATE-PASSPORT-7781",
                "scheme": {"proprietary": "ULTIMATE_PRIVATE_SCHEME_ALPHA"},
                "issuer": "Ultimate Private Passport Registry",
            }
        )
        identifiers[1].update(
            {
                "id": "ULTIMATE-PRIVATE-NATIONAL-7782",
                "scheme": {"proprietary": "ULTIMATE_PRIVATE_SCHEME_BETA"},
                "issuer": "Ultimate Private National Registry",
            }
        )
        sensitive_values = set(self._scalar_leaves(sensitive))

        for role in ("ultimate_debtor", "ultimate_creditor"):
            with self.subTest(role=role):
                baseline = self._ingest_and_read_first_detail(
                    self._with_ultimate_private(role, self.base_private),
                    f"{role}-private-public-baseline",
                )
                private = self._ingest_and_read_first_detail(
                    self._with_ultimate_private(role, sensitive),
                    f"{role}-private-sensitive",
                )
                evidence_key = self._evidence_key(role)
                for projection in (baseline, private):
                    self._assert_sha256(projection[evidence_key])
                    self._assert_sha256(projection["source_detail_hash"])
                    self.assertEqual(
                        Decimal(str(projection["detail_amount"])),
                        Decimal("25000.00"),
                    )
                    self.assertEqual(projection["detail_currency_code"], "KRW")

                self.assertNotEqual(
                    baseline[evidence_key],
                    private[evidence_key],
                )
                self.assertNotEqual(
                    baseline["source_detail_hash"],
                    private["source_detail_hash"],
                )
                baseline_public = self._public_projection(baseline, evidence_key)
                private_public = self._public_projection(private, evidence_key)
                self.assertEqual(baseline_public, private_public)
                buyer_values = set(self._scalar_leaves(private_public))
                self.assertTrue(sensitive_values.isdisjoint(buyer_values))

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
        cls._identifiers(first_id)[0]["id"] = "ULTIMATE-PERSON-PRIMARY-002"
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
        cls._identifiers(first_issuer)[0]["issuer"] = "Alternate Ultimate Passport Authority"
        variants["first-issuer-value"] = first_issuer

        first_issuer_absent = copy.deepcopy(base)
        cls._identifiers(first_issuer_absent)[0].pop("issuer")
        variants["first-issuer-absent"] = first_issuer_absent

        additional_id = copy.deepcopy(base)
        cls._identifiers(additional_id)[1]["id"] = "ULTIMATE-PERSON-ADDITIONAL-002"
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
            "Alternate Ultimate Identity Registry"
        )
        variants["additional-issuer-value"] = additional_issuer

        additional_issuer_absent = copy.deepcopy(base)
        cls._identifiers(additional_issuer_absent)[1].pop("issuer")
        variants["additional-issuer-absent"] = additional_issuer_absent

        additional_removed = copy.deepcopy(base)
        cls._identifiers(additional_removed).pop()
        variants["additional-identifier-removed"] = additional_removed
        return variants

    def _with_ultimate_private(
        self,
        role: str,
        private: dict[str, object],
    ) -> bytes:
        """Insert one schema-shaped ultimate party carrying PersonIdentification18."""
        marker = "              </Dbtr>\n            </RltdPties>"
        self.assertEqual(self.fixture.count(marker), 1)
        tag = self._xml_tag(role)
        replacement = (
            "              </Dbtr>\n"
            f"              <{tag}>\n"
            "                <Pty>\n"
            f"                  <Nm>{self.party_name}</Nm>\n"
            "                  <Id>\n"
            f"{self._private_xml(private)}"
            "                  </Id>\n"
            "                </Pty>\n"
            f"              </{tag}>\n"
            "            </RltdPties>"
        )
        return self.fixture.replace(marker, replacement, 1).encode("utf-8")

    @classmethod
    def _private_xml(cls, private: dict[str, object]) -> str:
        """Serialize one PersonIdentification18 using schema order and choice semantics."""
        lines = ["                    <PrvtId>"]
        birth = private.get("date_and_place_of_birth")
        if birth is not None:
            if not isinstance(birth, dict):
                raise AssertionError("date/place of birth must be a mapping")
            lines.extend(
                [
                    "                      <DtAndPlcOfBirth>",
                    f"                        <BirthDt>{birth['birth_date']}</BirthDt>",
                ]
            )
            province = birth.get("province_of_birth")
            if province is not None:
                lines.append(f"                        <PrvcOfBirth>{province}</PrvcOfBirth>")
            lines.extend(
                [
                    f"                        <CityOfBirth>{birth['city_of_birth']}</CityOfBirth>",
                    f"                        <CtryOfBirth>{birth['country_of_birth']}</CtryOfBirth>",
                    "                      </DtAndPlcOfBirth>",
                ]
            )

        for identifier in cls._identifiers(private):
            lines.append("                      <Othr>")
            lines.append(f"                        <Id>{identifier['id']}</Id>")
            scheme = identifier.get("scheme")
            if scheme is not None:
                if not isinstance(scheme, dict):
                    raise AssertionError("person identifier scheme must be a mapping")
                lines.append("                        <SchmeNm>")
                if "code" in scheme:
                    lines.append(f"                          <Cd>{scheme['code']}</Cd>")
                elif "proprietary" in scheme:
                    lines.append(
                        f"                          <Prtry>{scheme['proprietary']}</Prtry>"
                    )
                else:
                    raise AssertionError("person scheme must select code or proprietary")
                lines.append("                        </SchmeNm>")
            issuer = identifier.get("issuer")
            if issuer is not None:
                lines.append(f"                        <Issr>{issuer}</Issr>")
            lines.append("                      </Othr>")
        lines.append("                    </PrvtId>")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _identifiers(private: dict[str, object]) -> list[dict[str, object]]:
        """Return the repeated GenericPersonIdentification2 population."""
        identifiers = private.get("identifiers")
        if not isinstance(identifiers, list):
            raise AssertionError("private identification must contain an identifier list")
        for item in identifiers:
            if not isinstance(item, dict):
                raise AssertionError("private identifier must be a mapping")
        return identifiers

    @staticmethod
    def _birth(private: dict[str, object]) -> dict[str, object]:
        """Return the date/place-of-birth mapping for focused variant construction."""
        birth = private.get("date_and_place_of_birth")
        if not isinstance(birth, dict):
            raise AssertionError("private identification must contain date/place of birth")
        return birth

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

    def _ingest_and_read_first_detail(
        self,
        payload: bytes,
        suffix: str,
    ) -> dict[str, object]:
        """Ingest one isolated statement and return its first transaction detail."""
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
        return document["bank_statement_entries"][0]["entry_details"][0]

    @staticmethod
    def _public_projection(
        detail: dict[str, object], evidence_key: str
    ) -> dict[str, object]:
        """Remove only internal evidence identities before buyer-visible comparison."""
        projection = dict(detail)
        projection.pop(evidence_key)
        projection.pop("source_detail_hash")
        return projection

    @classmethod
    def _scalar_leaves(cls, value: object) -> Iterable[str]:
        """Yield exact source/public scalar leaves without substring heuristics."""
        if isinstance(value, dict):
            for item in value.values():
                yield from cls._scalar_leaves(item)
            return
        if isinstance(value, list):
            for item in value:
                yield from cls._scalar_leaves(item)
            return
        if value is not None:
            yield str(value)

    def _command(
        self,
        payload: bytes,
        bank_account_reference: str,
        suffix: str,
    ) -> dict[str, object]:
        """Return one supported ingest command with a unique tenant replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": bank_account_reference,
            "ingestion_idempotency_key": (
                f"ultimate-party-private-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

    @staticmethod
    def _evidence_key(role: str) -> str:
        """Return the role-specific ultimate-party evidence digest field."""
        if role == "ultimate_debtor":
            return "ultimate_debtor_evidence_hash"
        if role == "ultimate_creditor":
            return "ultimate_creditor_evidence_hash"
        raise AssertionError(f"unsupported ultimate role: {role}")

    @staticmethod
    def _xml_tag(role: str) -> str:
        """Map the test role to the camt.053 ultimate-party element name."""
        if role == "ultimate_debtor":
            return "UltmtDbtr"
        if role == "ultimate_creditor":
            return "UltmtCdtr"
        raise AssertionError(f"unsupported ultimate role: {role}")

    def _assert_financial_truth(self, entry: object, detail: object) -> None:
        """Keep private-person provenance independent from exact accounting values."""
        self.assertEqual(getattr(entry, "entry_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(entry, "entry_currency_code"), "KRW")
        self.assertEqual(getattr(detail, "detail_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(detail, "detail_currency_code"), "KRW")

    def _assert_sha256(self, value: object) -> None:
        """Require a canonical SHA-256 identity before equality comparisons."""
        self.assertIsInstance(value, str)
        self.assertRegex(value, _HASH_PATTERN)


if __name__ == "__main__":
    unittest.main()
