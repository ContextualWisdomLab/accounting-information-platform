"""PostgreSQL REDs for ultimate-party organisation identifiers in camt.053 details."""

from __future__ import annotations

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


class BankStatementDetailUltimatePartyOrganisationIdentifiersEvidenceRedTests(
    unittest.TestCase
):
    """Retain ultimate-party AnyBIC/LEI as evidence without identity-master authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one isolated fixture and role-specific organisation-ID variants."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.party_name = "Ultimate Organisation Party"
        self.other_identifier = "ULTIMATE-ORG-ID-001"
        self.base_any_bic = "DEUTDEFFXXX"
        self.changed_any_bic = "COBADEFFXXX"
        self.base_lei = "7LTWFZYICNSX8D621K86"
        self.changed_lei = "529900Z6KVD8Y83D7K60"

    def test_any_bic_and_lei_value_and_presence_are_material_for_each_ultimate_role(
        self,
    ) -> None:
        """OrganisationIdentification39 AnyBIC/LEI semantics change retained evidence."""
        variants = (
            (
                "any-bic-value",
                (self.base_any_bic, self.base_lei),
                (self.changed_any_bic, self.base_lei),
            ),
            (
                "any-bic-presence",
                (self.base_any_bic, self.base_lei),
                (None, self.base_lei),
            ),
            (
                "lei-value",
                (self.base_any_bic, self.base_lei),
                (self.base_any_bic, self.changed_lei),
            ),
            (
                "lei-presence",
                (self.base_any_bic, self.base_lei),
                (self.base_any_bic, None),
            ),
        )
        for role in ("ultimate_debtor", "ultimate_creditor"):
            for semantic, left_ids, right_ids in variants:
                with self.subTest(role=role, semantic=semantic):
                    left = parse_bank_statement_payload(
                        self._with_ultimate_party(role, *left_ids),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    right = parse_bank_statement_payload(
                        self._with_ultimate_party(role, *right_ids),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    left_entry = left.entries[0]
                    right_entry = right.entries[0]
                    left_detail = left_entry.entry_details[0]
                    right_detail = right_entry.entry_details[0]
                    evidence_key = self._evidence_key(role)

                    self._assert_financial_truth(left_entry, left_detail)
                    self._assert_financial_truth(right_entry, right_detail)
                    for value in (
                        getattr(left_detail, evidence_key),
                        getattr(right_detail, evidence_key),
                        left_detail.source_detail_hash,
                        right_detail.source_detail_hash,
                        left_entry.source_entry_hash,
                        right_entry.source_entry_hash,
                        left.normalized_payload_hash,
                        right.normalized_payload_hash,
                        left.account_identifier_hash,
                        right.account_identifier_hash,
                        left.entries[1].source_entry_hash,
                        right.entries[1].source_entry_hash,
                    ):
                        self._assert_sha256(value)

                    self.assertNotEqual(
                        getattr(left_detail, evidence_key),
                        getattr(right_detail, evidence_key),
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
                    self.assertEqual(
                        left.account_identifier_hash,
                        right.account_identifier_hash,
                    )
                    self.assertEqual(
                        left.entries[1].source_entry_hash,
                        right.entries[1].source_entry_hash,
                    )

    def test_direct_identifier_whitespace_is_representation_only_for_each_role(self) -> None:
        """Layout beside AnyBIC changes raw bytes but not semantic evidence identity."""
        for role in ("ultimate_debtor", "ultimate_creditor"):
            with self.subTest(role=role):
                baseline = self._with_ultimate_party(
                    role, self.base_any_bic, self.base_lei
                )
                needle = (
                    f"                      <AnyBIC>{self.base_any_bic}</AnyBIC>\n"
                ).encode("utf-8")
                self.assertEqual(baseline.count(needle), 1)
                formatted = baseline.replace(needle, needle + b"                      \n", 1)
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

    def test_direct_identifier_changes_reach_complete_correction_boundary(self) -> None:
        """Accepted AnyBIC/LEI value or presence cannot be silently replaced."""
        variants = (
            ("any-bic-value", self.changed_any_bic, self.base_lei),
            ("any-bic-absent", None, self.base_lei),
            ("lei-value", self.base_any_bic, self.changed_lei),
            ("lei-absent", self.base_any_bic, None),
        )
        for role in ("ultimate_debtor", "ultimate_creditor"):
            for semantic, any_bic, lei in variants:
                with self.subTest(role=role, semantic=semantic):
                    baseline = self._with_ultimate_party(
                        role, self.base_any_bic, self.base_lei
                    )
                    changed = self._with_ultimate_party(role, any_bic, lei)
                    reference = self._register_statement_account(baseline)
                    store = MemoryArtifactStore()
                    accept_bank_statement_evidence(
                        self._command(
                            baseline,
                            reference,
                            f"{role}-{semantic}-baseline",
                        ),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=store,
                    )
                    with self.assertRaisesRegex(
                        AccountingValidationError, _CORRECTION_ERROR
                    ):
                        accept_bank_statement_evidence(
                            self._command(
                                changed,
                                reference,
                                f"{role}-{semantic}-changed",
                            ),
                            posting.DATABASE_URL,
                            self.case.policy.tenant_reference,
                            artifact_store=store,
                        )

    def test_buyer_projection_keeps_direct_identifiers_non_reversible(self) -> None:
        """AnyBIC/LEI affect purpose digests without becoming buyer identity fields."""
        for role in ("ultimate_debtor", "ultimate_creditor"):
            with self.subTest(role=role):
                baseline = self._ingest_and_read_first_detail(
                    self._with_ultimate_party(
                        role, self.base_any_bic, self.base_lei
                    ),
                    f"{role}-baseline",
                )
                bic_changed = self._ingest_and_read_first_detail(
                    self._with_ultimate_party(
                        role, self.changed_any_bic, self.base_lei
                    ),
                    f"{role}-bic-changed",
                )
                lei_changed = self._ingest_and_read_first_detail(
                    self._with_ultimate_party(
                        role, self.base_any_bic, self.changed_lei
                    ),
                    f"{role}-lei-changed",
                )
                evidence_key = self._evidence_key(role)

                for projection in (baseline, bic_changed, lei_changed):
                    self._assert_sha256(projection[evidence_key])
                    self._assert_sha256(projection["source_detail_hash"])
                    self.assertEqual(
                        Decimal(str(projection["detail_amount"])),
                        Decimal("25000.00"),
                    )
                    self.assertEqual(projection["detail_currency_code"], "KRW")

                for variant in (bic_changed, lei_changed):
                    self.assertNotEqual(
                        baseline[evidence_key],
                        variant[evidence_key],
                    )
                    self.assertNotEqual(
                        baseline["source_detail_hash"],
                        variant["source_detail_hash"],
                    )
                    self.assertEqual(
                        self._public_projection(baseline, evidence_key),
                        self._public_projection(variant, evidence_key),
                    )

                serialized = json.dumps(
                    {
                        "baseline": baseline,
                        "bic_changed": bic_changed,
                        "lei_changed": lei_changed,
                    },
                    sort_keys=True,
                    default=str,
                )
                for raw_value in (
                    self.other_identifier,
                    self.base_any_bic,
                    self.changed_any_bic,
                    self.base_lei,
                    self.changed_lei,
                ):
                    self.assertNotIn(raw_value, serialized)

    def _with_ultimate_party(
        self,
        role: str,
        any_bic: str | None,
        lei: str | None,
    ) -> bytes:
        """Insert one ultimate party with OrganisationIdentification39 evidence."""
        marker = "              </Dbtr>\n            </RltdPties>"
        self.assertEqual(self.fixture.count(marker), 1)
        tag = self._xml_tag(role)
        any_bic_xml = (
            f"                      <AnyBIC>{any_bic}</AnyBIC>\n"
            if any_bic is not None
            else ""
        )
        lei_xml = (
            f"                      <LEI>{lei}</LEI>\n" if lei is not None else ""
        )
        replacement = (
            "              </Dbtr>\n"
            f"              <{tag}>\n"
            "                <Pty>\n"
            f"                  <Nm>{self.party_name}</Nm>\n"
            "                  <Id>\n"
            "                    <OrgId>\n"
            + any_bic_xml
            + lei_xml
            + "                      <Othr>\n"
            f"                        <Id>{self.other_identifier}</Id>\n"
            "                      </Othr>\n"
            "                    </OrgId>\n"
            "                  </Id>\n"
            "                </Pty>\n"
            f"              </{tag}>\n"
            "            </RltdPties>"
        )
        return self.fixture.replace(marker, replacement, 1).encode("utf-8")

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
        """Ingest on an isolated statement-owner account and return its first detail."""
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
                f"ultimate-party-org-identifiers-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

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
        """Map the test role to the registered camt.053 ultimate-party element."""
        if role == "ultimate_debtor":
            return "UltmtDbtr"
        if role == "ultimate_creditor":
            return "UltmtCdtr"
        raise AssertionError(f"unsupported ultimate role: {role}")

    def _assert_financial_truth(self, entry: object, detail: object) -> None:
        """Keep organisation identifiers independent from exact accounting values."""
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
