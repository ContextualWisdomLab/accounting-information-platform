"""PostgreSQL REDs for direct debtor/creditor organisation identifiers in camt.053."""

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


class BankStatementDebtorCreditorPartyOrganisationIdentifiersEvidenceRedTests(
    unittest.TestCase
):
    """Retain direct-party AnyBIC/LEI as evidence without identity-master authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare role-specific organisation identifier variants and PostgreSQL fixture."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.party_name = "Direct Organisation Party"
        self.base_identifier = "DIRECT-ORG-ID-001"
        self.base_bic = "DEUTDEFFXXX"
        self.changed_bic = "COBADEFFXXX"
        self.base_lei = "7LTWFZYICNSX8D621K86"
        self.changed_lei = "529900Z6KVD8Y83D7K60"

    def test_any_bic_and_lei_value_and_presence_are_material_to_identity(self) -> None:
        """AnyBIC/LEI values and optional presence independently change evidence identity."""
        variants = (
            ("any-bic-value", self.base_bic, self.base_lei, self.changed_bic, self.base_lei),
            ("any-bic-absent", self.base_bic, self.base_lei, None, self.base_lei),
            ("lei-value", self.base_bic, self.base_lei, self.base_bic, self.changed_lei),
            ("lei-absent", self.base_bic, self.base_lei, self.base_bic, None),
        )
        for role in ("debtor", "creditor"):
            target_index = self._target_entry_index(role)
            untouched_index = 1 - target_index
            for semantic, left_bic, left_lei, right_bic, right_lei in variants:
                with self.subTest(role=role, semantic=semantic):
                    left = parse_bank_statement_payload(
                        self._with_role_organisation(role, left_bic, left_lei),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    right = parse_bank_statement_payload(
                        self._with_role_organisation(role, right_bic, right_lei),
                        CAMT053_MESSAGE_DEFINITION,
                    )
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
                        left.account_identifier_hash,
                        right.account_identifier_hash,
                        left.entries[untouched_index].source_entry_hash,
                        right.entries[untouched_index].source_entry_hash,
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
                    self.assertEqual(
                        left.account_identifier_hash,
                        right.account_identifier_hash,
                    )
                    self.assertEqual(
                        left.entries[untouched_index].source_entry_hash,
                        right.entries[untouched_index].source_entry_hash,
                    )

    def test_organisation_identifier_xml_layout_is_representation_only(self) -> None:
        """Whitespace beside AnyBIC changes source bytes, not admitted semantics."""
        baseline = self._with_role_organisation(
            "debtor", self.base_bic, self.base_lei
        )
        formatted = baseline.replace(
            f"                      <AnyBIC>{self.base_bic}</AnyBIC>\n".encode(),
            (
                f"                      <AnyBIC>{self.base_bic}</AnyBIC>\n"
                "                      \n"
            ).encode(),
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

        self.assertNotEqual(left.source_artifact_hash, right.source_artifact_hash)
        self.assertEqual(
            left_entry.counterparty_evidence_hash,
            right_entry.counterparty_evidence_hash,
        )
        self.assertEqual(left_detail.source_detail_hash, right_detail.source_detail_hash)
        self.assertEqual(left_entry.source_entry_hash, right_entry.source_entry_hash)
        self.assertEqual(left.normalized_payload_hash, right.normalized_payload_hash)

    def test_organisation_identifier_change_reaches_complete_correction_boundary(self) -> None:
        """Accepted AnyBIC/LEI evidence cannot be silently replaced by replay."""
        variants = (
            ("any-bic-value", self.changed_bic, self.base_lei),
            ("any-bic-absent", None, self.base_lei),
            ("lei-value", self.base_bic, self.changed_lei),
            ("lei-absent", self.base_bic, None),
        )
        for role in ("debtor", "creditor"):
            baseline = self._with_role_organisation(
                role, self.base_bic, self.base_lei
            )
            reference = self._register_statement_account(baseline)
            store = MemoryArtifactStore()
            accept_bank_statement_evidence(
                self._command(
                    baseline,
                    reference,
                    f"{role}-party-organisation-identifiers-baseline",
                ),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=store,
            )
            for semantic, changed_bic, changed_lei in variants:
                with self.subTest(role=role, semantic=semantic):
                    changed = self._with_role_organisation(
                        role, changed_bic, changed_lei
                    )
                    with self.assertRaisesRegex(
                        AccountingValidationError, _CORRECTION_ERROR
                    ):
                        accept_bank_statement_evidence(
                            self._command(
                                changed,
                                reference,
                                f"{role}-party-organisation-identifiers-{semantic}",
                            ),
                            posting.DATABASE_URL,
                            self.case.policy.tenant_reference,
                            artifact_store=store,
                        )

    def test_buyer_projection_keeps_organisation_identifiers_non_reversible(self) -> None:
        """Organisation identifiers affect internal evidence without leaking source IDs."""
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                baseline = self._ingest_and_read_target_entry(
                    role,
                    self._with_role_organisation(
                        role, self.base_bic, self.base_lei
                    ),
                    f"{role}-party-org-identifiers-baseline",
                )
                changed_bic = self._ingest_and_read_target_entry(
                    role,
                    self._with_role_organisation(
                        role, self.changed_bic, self.base_lei
                    ),
                    f"{role}-party-org-identifiers-bic",
                )
                changed_lei = self._ingest_and_read_target_entry(
                    role,
                    self._with_role_organisation(
                        role, self.base_bic, self.changed_lei
                    ),
                    f"{role}-party-org-identifiers-lei",
                )

                for projection in (baseline, changed_bic, changed_lei):
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

                for variant in (changed_bic, changed_lei):
                    self.assertNotEqual(
                        baseline["counterparty_evidence_hash"],
                        variant["counterparty_evidence_hash"],
                    )
                    self.assertNotEqual(
                        baseline["entry_details"][0]["source_detail_hash"],
                        variant["entry_details"][0]["source_detail_hash"],
                    )
                    self.assertNotEqual(
                        baseline["source_entry_hash"],
                        variant["source_entry_hash"],
                    )

                baseline_public = self._public_projection(baseline)
                self.assertEqual(baseline_public, self._public_projection(changed_bic))
                self.assertEqual(baseline_public, self._public_projection(changed_lei))

                serialized = json.dumps(
                    {
                        "baseline": baseline,
                        "changed_bic": changed_bic,
                        "changed_lei": changed_lei,
                    },
                    sort_keys=True,
                    default=str,
                )
                for source_value in (
                    self.base_identifier,
                    self.base_bic,
                    self.changed_bic,
                    self.base_lei,
                    self.changed_lei,
                ):
                    self.assertNotIn(source_value, serialized)

    def _with_role_organisation(
        self,
        role: str,
        any_bic: str | None,
        lei: str | None,
    ) -> bytes:
        """Return valid direct Pty/OrgId evidence with optional AnyBIC and LEI."""
        party_body = self._party_xml(any_bic, lei)
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

    def _party_xml(self, any_bic: str | None, lei: str | None) -> str:
        """Serialize PartyIdentification272/OrgId with direct identifiers."""
        any_bic_xml = (
            f"                      <AnyBIC>{any_bic}</AnyBIC>\n"
            if any_bic is not None
            else ""
        )
        lei_xml = (
            f"                      <LEI>{lei}</LEI>\n"
            if lei is not None
            else ""
        )
        return (
            "                <Pty>\n"
            f"                  <Nm>{self.party_name}</Nm>\n"
            "                  <Id>\n"
            "                    <OrgId>\n"
            + any_bic_xml
            + lei_xml
            + "                      <Othr>\n"
            f"                        <Id>{self.base_identifier}</Id>\n"
            "                      </Othr>\n"
            "                    </OrgId>\n"
            "                  </Id>\n"
            "                </Pty>"
        )

    def _register_statement_account(self, payload: bytes) -> str:
        """Register only the statement-owner account used by supported ingest."""
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
                f"direct-party-org-identifiers-{suffix}-{uuid.uuid4().hex}"
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

    @classmethod
    def _assert_financial_truth(cls, role: str, entry: object, detail: object) -> None:
        """Keep organisation provenance separate from accounting amount truth."""
        if getattr(entry, "entry_amount", None) != cls._expected_entry_amount(role):
            raise AssertionError("organisation identifiers must not alter entry amount")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("organisation identifiers must not alter entry currency")
        if getattr(detail, "detail_amount", None) != cls._expected_detail_amount(role):
            raise AssertionError("organisation identifiers must not alter detail amount")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("organisation identifiers must not alter detail currency")

    @staticmethod
    def _assert_sha256(value: object) -> None:
        """Require canonical semantic evidence digest format."""
        if not isinstance(value, str) or _HASH_PATTERN.fullmatch(value) is None:
            raise AssertionError(f"expected canonical SHA-256 evidence digest, got {value!r}")


if __name__ == "__main__":
    unittest.main()
