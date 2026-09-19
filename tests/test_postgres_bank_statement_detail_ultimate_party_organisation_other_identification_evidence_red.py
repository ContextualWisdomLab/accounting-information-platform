"""PostgreSQL REDs for ultimate-party organisation Othr evidence in camt.053."""

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


class BankStatementDetailUltimatePartyOrganisationOtherIdentificationEvidenceRedTests(
    unittest.TestCase
):
    """Retain ultimate-party GenericOrganisationIdentification3 as private evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare repeated Othr variants and one isolated PostgreSQL fixture."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.base_identifiers = [
            {
                "id": "ULTIMATE-ORG-PRIMARY-001",
                "scheme": {"code": "BANK"},
                "issuer": "Ultimate Relationship Registry",
            },
            {
                "id": "ULTIMATE-ORG-ADDITIONAL-001",
                "scheme": {"code": "BANK"},
                "issuer": "Ultimate Tax Registry",
            },
        ]
        self.variants = self._variants(self.base_identifiers)

    def test_other_identification_fields_and_population_are_material_to_identity(self) -> None:
        """Othr ID, scheme choice/value, issuer, and population change evidence."""
        for role in self._roles():
            baseline = self._parsed(role, self.base_identifiers)
            base_entry, base_detail = baseline.entries[0], baseline.entries[0].entry_details[0]
            key = self._evidence_key(role)
            self._assert_financial_truth(base_entry, base_detail)
            self._assert_semantic_hashes(baseline, key)

            for semantic, identifiers in self.variants.items():
                with self.subTest(role=role, semantic=semantic):
                    changed = self._parsed(role, identifiers)
                    entry, detail = changed.entries[0], changed.entries[0].entry_details[0]
                    self._assert_financial_truth(entry, detail)
                    self._assert_semantic_hashes(changed, key)
                    self.assertNotEqual(getattr(base_detail, key), getattr(detail, key))
                    self.assertNotEqual(base_detail.source_detail_hash, detail.source_detail_hash)
                    self.assertNotEqual(base_entry.source_entry_hash, entry.source_entry_hash)
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

    def test_scheme_choice_discriminator_is_material_for_same_scalar(self) -> None:
        """Cd=BANK and Prtry=BANK remain distinct scheme-name choice values."""
        for role in self._roles():
            for position in (0, 1):
                with self.subTest(role=role, position=position):
                    coded = copy.deepcopy(self.base_identifiers)
                    proprietary = copy.deepcopy(self.base_identifiers)
                    coded[position]["scheme"] = {"code": "BANK"}
                    proprietary[position]["scheme"] = {"proprietary": "BANK"}
                    left = self._parsed(role, coded)
                    right = self._parsed(role, proprietary)
                    key = self._evidence_key(role)
                    self._assert_semantic_hashes(left, key)
                    self._assert_semantic_hashes(right, key)
                    left_detail = left.entries[0].entry_details[0]
                    right_detail = right.entries[0].entry_details[0]
                    self.assertNotEqual(
                        getattr(left_detail, key),
                        getattr(right_detail, key),
                    )
                    self.assertNotEqual(
                        left_detail.source_detail_hash,
                        right_detail.source_detail_hash,
                    )
                    self.assertNotEqual(
                        left.entries[0].source_entry_hash,
                        right.entries[0].source_entry_hash,
                    )
                    self.assertNotEqual(
                        left.normalized_payload_hash,
                        right.normalized_payload_hash,
                    )

    def test_other_identification_xml_layout_is_representation_only(self) -> None:
        """Whitespace inside SchmeNm changes source bytes, not semantic identity."""
        for role in self._roles():
            with self.subTest(role=role):
                baseline = self._payload(role, self.base_identifiers)
                needle = b"                        <SchmeNm>\n"
                self.assertEqual(baseline.count(needle), 2)
                formatted = baseline.replace(
                    needle,
                    needle + b"                          \n",
                    1,
                )
                left = parse_bank_statement_payload(baseline, CAMT053_MESSAGE_DEFINITION)
                right = parse_bank_statement_payload(formatted, CAMT053_MESSAGE_DEFINITION)
                key = self._evidence_key(role)
                self._assert_semantic_hashes(left, key)
                self._assert_semantic_hashes(right, key)
                self._assert_sha256(left.source_artifact_hash)
                self._assert_sha256(right.source_artifact_hash)
                self.assertNotEqual(left.source_artifact_hash, right.source_artifact_hash)
                self.assertEqual(
                    getattr(left.entries[0].entry_details[0], key),
                    getattr(right.entries[0].entry_details[0], key),
                )
                self.assertEqual(
                    left.entries[0].entry_details[0].source_detail_hash,
                    right.entries[0].entry_details[0].source_detail_hash,
                )
                self.assertEqual(
                    left.entries[0].source_entry_hash,
                    right.entries[0].source_entry_hash,
                )
                self.assertEqual(
                    left.normalized_payload_hash,
                    right.normalized_payload_hash,
                )

    def test_other_identification_changes_reach_complete_correction_boundary(self) -> None:
        """Accepted ultimate-party Othr evidence cannot be silently replaced."""
        for role in self._roles():
            baseline = self._payload(role, self.base_identifiers)
            reference = self._register_statement_account(baseline)
            store = MemoryArtifactStore()
            accept_bank_statement_evidence(
                self._command(baseline, reference, f"{role}-baseline"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=store,
            )
            for semantic, identifiers in self.variants.items():
                with self.subTest(role=role, semantic=semantic):
                    with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                        accept_bank_statement_evidence(
                            self._command(
                                self._payload(role, identifiers),
                                reference,
                                f"{role}-{semantic}",
                            ),
                            posting.DATABASE_URL,
                            self.case.policy.tenant_reference,
                            artifact_store=store,
                        )

    def test_buyer_projection_keeps_other_identification_non_reversible(self) -> None:
        """Rich Othr provenance changes internal evidence without leaking source IDs."""
        private_ids = copy.deepcopy(self.base_identifiers)
        private_ids[0] = {
            "id": "ULTIMATE-PRIVATE-ID-7781",
            "scheme": {"proprietary": "ULTIMATE_PRIVATE_SCHEME_ALPHA"},
            "issuer": "Ultimate Private Registry Alpha",
        }
        private_ids[1] = {
            "id": "ULTIMATE-PRIVATE-ID-7782",
            "scheme": {"proprietary": "ULTIMATE_PRIVATE_SCHEME_BETA"},
            "issuer": "Ultimate Private Registry Beta",
        }
        private_values = (
            "ULTIMATE-PRIVATE-ID-7781",
            "ULTIMATE_PRIVATE_SCHEME_ALPHA",
            "Ultimate Private Registry Alpha",
            "ULTIMATE-PRIVATE-ID-7782",
            "ULTIMATE_PRIVATE_SCHEME_BETA",
            "Ultimate Private Registry Beta",
        )

        for role in self._roles():
            with self.subTest(role=role):
                baseline = self._ingest_and_read_first_detail(
                    self._payload(role, self.base_identifiers),
                    f"{role}-public-baseline",
                )
                private = self._ingest_and_read_first_detail(
                    self._payload(role, private_ids),
                    f"{role}-private",
                )
                key = self._evidence_key(role)
                for projection in (baseline, private):
                    self._assert_sha256(projection[key])
                    self._assert_sha256(projection["source_detail_hash"])
                    self.assertEqual(
                        Decimal(str(projection["detail_amount"])), Decimal("25000.00")
                    )
                    self.assertEqual(projection["detail_currency_code"], "KRW")
                self.assertNotEqual(baseline[key], private[key])
                self.assertNotEqual(
                    baseline["source_detail_hash"], private["source_detail_hash"]
                )
                self.assertEqual(
                    self._public_projection(baseline, key),
                    self._public_projection(private, key),
                )
                serialized = json.dumps(private, sort_keys=True, default=str)
                for source_value in private_values:
                    self.assertNotIn(source_value, serialized)

    @classmethod
    def _variants(
        cls, base: list[dict[str, object]]
    ) -> dict[str, list[dict[str, object]]]:
        """Return independent first/repeated Othr value, choice, and presence variants."""
        specs = (
            ("first-id-value", 0, "id", "ULTIMATE-ORG-PRIMARY-002"),
            ("first-scheme-value", 0, "scheme", {"code": "DUNS"}),
            ("first-scheme-choice", 0, "scheme", {"proprietary": "BANK"}),
            (
                "first-scheme-proprietary-value",
                0,
                "scheme",
                {"proprietary": "ULTIMATE_ORG_SCHEME_ALPHA"},
            ),
            (
                "first-issuer-value",
                0,
                "issuer",
                "Alternate Ultimate Relationship Registry",
            ),
            ("additional-id-value", 1, "id", "ULTIMATE-ORG-ADDITIONAL-002"),
            ("additional-scheme-value", 1, "scheme", {"code": "DUNS"}),
            ("additional-scheme-choice", 1, "scheme", {"proprietary": "BANK"}),
            (
                "additional-scheme-proprietary-value",
                1,
                "scheme",
                {"proprietary": "ULTIMATE_ORG_SCHEME_BETA"},
            ),
            (
                "additional-issuer-value",
                1,
                "issuer",
                "Alternate Ultimate Tax Registry",
            ),
        )
        variants: dict[str, list[dict[str, object]]] = {}
        for name, position, field, value in specs:
            changed = copy.deepcopy(base)
            changed[position][field] = value
            variants[name] = changed
        for position, prefix in ((0, "first"), (1, "additional")):
            for field in ("scheme", "issuer"):
                changed = copy.deepcopy(base)
                changed[position].pop(field)
                variants[f"{prefix}-{field}-absent"] = changed
        removed = copy.deepcopy(base)
        removed.pop()
        variants["additional-identifier-removed"] = removed
        return variants

    def _parsed(self, role: str, identifiers: list[dict[str, object]]) -> object:
        """Parse one role-specific ultimate-party Othr fixture."""
        return parse_bank_statement_payload(
            self._payload(role, identifiers), CAMT053_MESSAGE_DEFINITION
        )

    def _payload(self, role: str, identifiers: list[dict[str, object]]) -> bytes:
        """Insert one ultimate role with repeated GenericOrganisationIdentification3."""
        marker = "              </Dbtr>\n            </RltdPties>"
        self.assertEqual(self.fixture.count(marker), 1)
        tag = self._xml_tag(role)
        other_xml = "".join(self._other_xml(identifier) for identifier in identifiers)
        replacement = (
            "              </Dbtr>\n"
            f"              <{tag}>\n"
            "                <Pty>\n"
            "                  <Nm>Ultimate Organisation Other Party</Nm>\n"
            "                  <Id>\n"
            "                    <OrgId>\n"
            + other_xml
            + "                    </OrgId>\n"
            "                  </Id>\n"
            "                </Pty>\n"
            f"              </{tag}>\n"
            "            </RltdPties>"
        )
        return self.fixture.replace(marker, replacement, 1).encode("utf-8")

    @staticmethod
    def _other_xml(identifier: dict[str, object]) -> str:
        """Serialize one GenericOrganisationIdentification3 in schema order."""
        lines = [
            "                      <Othr>",
            f"                        <Id>{identifier['id']}</Id>",
        ]
        scheme = identifier.get("scheme")
        if scheme is not None:
            if not isinstance(scheme, dict):
                raise AssertionError(f"scheme must be a mapping: {scheme!r}")
            lines.append("                        <SchmeNm>")
            if "code" in scheme:
                lines.append(f"                          <Cd>{scheme['code']}</Cd>")
            elif "proprietary" in scheme:
                lines.append(f"                          <Prtry>{scheme['proprietary']}</Prtry>")
            else:
                raise AssertionError(f"unsupported scheme: {scheme!r}")
            lines.append("                        </SchmeNm>")
        issuer = identifier.get("issuer")
        if issuer is not None:
            lines.append(f"                        <Issr>{issuer}</Issr>")
        lines.append("                      </Othr>")
        return "\n".join(lines) + "\n"

    def _register_statement_account(self, payload: bytes) -> str:
        """Register the statement-owner account required by supported ingest."""
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
        self, payload: bytes, suffix: str
    ) -> dict[str, object]:
        """Ingest on an isolated account and return the first transaction detail."""
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
        """Remove only internal evidence identities before buyer comparison."""
        projection = dict(detail)
        projection.pop(evidence_key)
        projection.pop("source_detail_hash")
        return projection

    def _command(
        self, payload: bytes, bank_account_reference: str, suffix: str
    ) -> dict[str, object]:
        """Return one supported ingest command with a unique replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": bank_account_reference,
            "ingestion_idempotency_key": (
                f"ultimate-party-org-other-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

    @staticmethod
    def _roles() -> tuple[str, str]:
        """Return the transaction-detail ultimate-party roles owned by this RED."""
        return ("ultimate_debtor", "ultimate_creditor")

    @staticmethod
    def _evidence_key(role: str) -> str:
        """Return the existing role-specific ultimate-party digest field."""
        if role == "ultimate_debtor":
            return "ultimate_debtor_evidence_hash"
        if role == "ultimate_creditor":
            return "ultimate_creditor_evidence_hash"
        raise AssertionError(f"unsupported ultimate role: {role}")

    @staticmethod
    def _xml_tag(role: str) -> str:
        """Map the test role to the registered camt.053 element."""
        if role == "ultimate_debtor":
            return "UltmtDbtr"
        if role == "ultimate_creditor":
            return "UltmtCdtr"
        raise AssertionError(f"unsupported ultimate role: {role}")

    def _assert_semantic_hashes(self, parsed: object, evidence_key: str) -> None:
        """Require canonical semantic identities before relational comparisons."""
        entry = parsed.entries[0]
        detail = entry.entry_details[0]
        for value in (
            getattr(detail, evidence_key),
            detail.source_detail_hash,
            entry.source_entry_hash,
            parsed.normalized_payload_hash,
            parsed.account_identifier_hash,
            parsed.entries[1].source_entry_hash,
        ):
            self._assert_sha256(value)

    def _assert_financial_truth(self, entry: object, detail: object) -> None:
        """Keep organisation identifiers independent from exact accounting facts."""
        self.assertEqual(getattr(entry, "entry_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(entry, "entry_currency_code"), "KRW")
        self.assertEqual(getattr(detail, "detail_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(detail, "detail_currency_code"), "KRW")

    def _assert_sha256(self, value: object) -> None:
        """Require one canonical SHA-256 identity before equality comparisons."""
        self.assertIsInstance(value, str)
        self.assertRegex(value, _HASH_PATTERN)


if __name__ == "__main__":
    unittest.main()
