"""PostgreSQL REDs for nested camt.053 direct debtor/creditor party identification."""

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


class BankStatementDebtorCreditorPartyIdentificationEvidenceRedTests(unittest.TestCase):
    """Retain nested direct-party identity without exposing identity-master truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare role-specific nested identity variants and a safe PostgreSQL fixture."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.party_name = "Direct Identified Party"
        self.base_identifier = "DIRECT-PARTY-ID-001"
        self.changed_identifier = "DIRECT-PARTY-ID-002"

    def test_nested_party_identifier_value_and_choice_are_material_to_identity(self) -> None:
        """Identifier value and OrgId/PrvtId discriminator independently change evidence."""
        variants = (
            (
                "identifier-value",
                ("organisation", self.base_identifier),
                ("organisation", self.changed_identifier),
            ),
            (
                "identity-choice",
                ("organisation", self.base_identifier),
                ("person", self.base_identifier),
            ),
        )
        for role in ("debtor", "creditor"):
            target_index = self._target_entry_index(role)
            untouched_index = 1 - target_index
            for semantic, left_identity, right_identity in variants:
                with self.subTest(role=role, semantic=semantic):
                    left = parse_bank_statement_payload(
                        self._with_role_identity(role, *left_identity),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    right = parse_bank_statement_payload(
                        self._with_role_identity(role, *right_identity),
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

    def test_nested_party_identity_xml_layout_is_representation_only(self) -> None:
        """Whitespace inside PartyIdentification272/Id changes bytes, not semantics."""
        baseline = self._with_role_identity(
            "debtor", "organisation", self.base_identifier
        )
        formatted = baseline.replace(
            b"                  <Id>\n",
            b"                  <Id>\n                    \n",
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

    def test_nested_party_identity_change_reaches_complete_correction_boundary(self) -> None:
        """Accepted direct-party identity evidence cannot be replaced by silent replay."""
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                baseline = self._with_role_identity(
                    role, "organisation", self.base_identifier
                )
                changed = self._with_role_identity(
                    role, "person", self.base_identifier
                )
                reference = self._register_statement_account(baseline)
                store = MemoryArtifactStore()
                accept_bank_statement_evidence(
                    self._command(
                        baseline,
                        reference,
                        f"{role}-party-identification-baseline",
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
                            f"{role}-party-identification-changed",
                        ),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=store,
                    )

    def test_buyer_projection_keeps_nested_party_identity_non_reversible(self) -> None:
        """Nested identity changes internal evidence without disclosing bank source IDs."""
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                organisation_entry = self._ingest_and_read_target_entry(
                    role,
                    self._with_role_identity(
                        role, "organisation", self.base_identifier
                    ),
                    f"{role}-party-identification-organisation",
                )
                changed_identifier_entry = self._ingest_and_read_target_entry(
                    role,
                    self._with_role_identity(
                        role, "organisation", self.changed_identifier
                    ),
                    f"{role}-party-identification-changed-identifier",
                )
                person_entry = self._ingest_and_read_target_entry(
                    role,
                    self._with_role_identity(role, "person", self.base_identifier),
                    f"{role}-party-identification-person",
                )
                for projection in (
                    organisation_entry,
                    changed_identifier_entry,
                    person_entry,
                ):
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

                for variant in (changed_identifier_entry, person_entry):
                    self.assertNotEqual(
                        organisation_entry["counterparty_evidence_hash"],
                        variant["counterparty_evidence_hash"],
                    )
                    self.assertNotEqual(
                        organisation_entry["entry_details"][0]["source_detail_hash"],
                        variant["entry_details"][0]["source_detail_hash"],
                    )
                    self.assertNotEqual(
                        organisation_entry["source_entry_hash"],
                        variant["source_entry_hash"],
                    )

                organisation_public = self._public_projection(organisation_entry)
                changed_identifier_public = self._public_projection(
                    changed_identifier_entry
                )
                person_public = self._public_projection(person_entry)
                self.assertEqual(organisation_public, changed_identifier_public)
                self.assertEqual(organisation_public, person_public)

                serialized = json.dumps(
                    {
                        "organisation": organisation_entry,
                        "changed_identifier": changed_identifier_entry,
                        "person": person_entry,
                    },
                    sort_keys=True,
                    default=str,
                )
                self.assertNotIn(self.base_identifier, serialized)
                self.assertNotIn(self.changed_identifier, serialized)

    def _with_role_identity(
        self,
        role: str,
        identity_choice: str,
        identifier: str,
    ) -> bytes:
        """Return a valid direct Pty with PartyIdentification272/Id semantics."""
        party_body = self._party_xml(identity_choice, identifier)
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

    def _party_xml(self, identity_choice: str, identifier: str) -> str:
        """Serialize Pty/Id as the organisation-or-person Party52Choice."""
        if identity_choice == "organisation":
            identity_xml = (
                "                    <OrgId>\n"
                "                      <Othr>\n"
                f"                        <Id>{identifier}</Id>\n"
                "                      </Othr>\n"
                "                    </OrgId>\n"
            )
        elif identity_choice == "person":
            identity_xml = (
                "                    <PrvtId>\n"
                "                      <Othr>\n"
                f"                        <Id>{identifier}</Id>\n"
                "                      </Othr>\n"
                "                    </PrvtId>\n"
            )
        else:
            raise AssertionError(f"unsupported Party52Choice: {identity_choice}")
        return (
            "                <Pty>\n"
            f"                  <Nm>{self.party_name}</Nm>\n"
            "                  <Id>\n"
            + identity_xml
            + "                  </Id>\n"
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
        """Remove only server/internal identifiers before buyer-visible comparison."""
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
                f"direct-party-identification-{suffix}-{uuid.uuid4().hex}"
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

    def _assert_financial_truth(
        self,
        role: str,
        entry: object,
        detail: object,
    ) -> None:
        """Prove party identity evidence does not change exact accounting amounts."""
        self.assertEqual(entry.entry_amount, self._expected_entry_amount(role))
        self.assertEqual(entry.entry_currency_code, "KRW")
        self.assertEqual(detail.detail_amount, self._expected_detail_amount(role))
        self.assertEqual(detail.detail_currency_code, "KRW")

    def _assert_sha256(self, value: object) -> None:
        """Require canonical retained SHA-256 syntax before comparing identities."""
        self.assertIsInstance(value, str)
        self.assertRegex(value, _HASH_PATTERN)


if __name__ == "__main__":
    unittest.main()
