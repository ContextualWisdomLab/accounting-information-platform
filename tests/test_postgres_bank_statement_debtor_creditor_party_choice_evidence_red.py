"""PostgreSQL REDs for camt.053 direct debtor/creditor Party50Choice evidence."""

from __future__ import annotations

import hashlib
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


class BankStatementDebtorCreditorPartyChoiceEvidenceRedTests(unittest.TestCase):
    """Preserve direct debtor/creditor Party50Choice semantics without disclosure."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one PostgreSQL fixture and one schema-shaped party choice per role."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.same_scalar_name = "Direct Party Same Scalar"
        self.changed_agent_name = "Direct Party Agent Changed"

    def test_party_choice_and_agent_value_are_material_to_entry_identity(self) -> None:
        """Party50Choice discriminator and the agent branch value are material evidence."""
        cases = (
            (
                "party_choice",
                ("Pty", self.same_scalar_name),
                ("Agt", self.same_scalar_name),
            ),
            (
                "agent_name",
                ("Agt", self.same_scalar_name),
                ("Agt", self.changed_agent_name),
            ),
        )
        for role in ("debtor", "creditor"):
            target_index = self._target_entry_index(role)
            untouched_index = 1 - target_index
            for semantic, left_party, right_party in cases:
                with self.subTest(role=role, semantic=semantic):
                    left = parse_bank_statement_payload(
                        self._with_role_party(role, *left_party),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    right = parse_bank_statement_payload(
                        self._with_role_party(role, *right_party),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    left_entry = left.entries[target_index]
                    right_entry = right.entries[target_index]
                    left_detail = left_entry.entry_details[0]
                    right_detail = right_entry.entry_details[0]

                    self.assertEqual(left_entry.entry_amount, self._expected_entry_amount(role))
                    self.assertEqual(right_entry.entry_amount, self._expected_entry_amount(role))
                    self.assertEqual(left_entry.entry_currency_code, "KRW")
                    self.assertEqual(right_entry.entry_currency_code, "KRW")
                    self.assertEqual(left_detail.detail_amount, self._expected_detail_amount(role))
                    self.assertEqual(right_detail.detail_amount, self._expected_detail_amount(role))
                    self.assertEqual(left_detail.detail_currency_code, "KRW")
                    self.assertEqual(right_detail.detail_currency_code, "KRW")
                    self._assert_sha256(left_entry.counterparty_evidence_hash)
                    self._assert_sha256(right_entry.counterparty_evidence_hash)
                    self._assert_sha256(left_entry.source_entry_hash)
                    self._assert_sha256(right_entry.source_entry_hash)
                    self._assert_sha256(left.normalized_payload_hash)
                    self._assert_sha256(right.normalized_payload_hash)
                    self.assertNotEqual(
                        left_entry.counterparty_evidence_hash,
                        right_entry.counterparty_evidence_hash,
                    )
                    self.assertNotEqual(left_entry.source_entry_hash, right_entry.source_entry_hash)
                    self.assertNotEqual(left.normalized_payload_hash, right.normalized_payload_hash)
                    self.assertEqual(left.account_identifier_hash, right.account_identifier_hash)
                    self.assertEqual(
                        left.entries[untouched_index].source_entry_hash,
                        right.entries[untouched_index].source_entry_hash,
                    )

    def test_existing_party_name_digest_remains_backward_compatible(self) -> None:
        """The established Pty/Nm digest remains SHA-256(name) while choice gains identity."""
        expected = "sha256:" + hashlib.sha256(self.same_scalar_name.encode("utf-8")).hexdigest()
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                statement = parse_bank_statement_payload(
                    self._with_role_party(role, "Pty", self.same_scalar_name),
                    CAMT053_MESSAGE_DEFINITION,
                )
                entry = statement.entries[self._target_entry_index(role)]
                self.assertEqual(entry.counterparty_evidence_hash, expected)

    def test_party_choice_xml_formatting_is_representation_only(self) -> None:
        """Whitespace inside the selected Party50Choice branch changes bytes, not semantics."""
        baseline = self._with_role_party("debtor", "Agt", self.same_scalar_name)
        formatted = baseline.replace(
            b"                <Agt>\n",
            b"                <Agt>\n                  \n",
            1,
        )
        self.assertNotEqual(baseline, formatted)
        left = parse_bank_statement_payload(baseline, CAMT053_MESSAGE_DEFINITION)
        right = parse_bank_statement_payload(formatted, CAMT053_MESSAGE_DEFINITION)
        left_entry = left.entries[0]
        right_entry = right.entries[0]

        self._assert_sha256(left_entry.counterparty_evidence_hash)
        self._assert_sha256(right_entry.counterparty_evidence_hash)
        self._assert_sha256(left_entry.source_entry_hash)
        self._assert_sha256(right_entry.source_entry_hash)
        self._assert_sha256(left.normalized_payload_hash)
        self._assert_sha256(right.normalized_payload_hash)
        self.assertNotEqual(left.source_artifact_hash, right.source_artifact_hash)
        self.assertEqual(left_entry.counterparty_evidence_hash, right_entry.counterparty_evidence_hash)
        self.assertEqual(left_entry.source_entry_hash, right_entry.source_entry_hash)
        self.assertEqual(left.normalized_payload_hash, right.normalized_payload_hash)

    def test_material_party_choice_change_reaches_complete_correction_boundary(self) -> None:
        """Accepted direct-party evidence cannot silently replay another choice branch."""
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                baseline = self._with_role_party(role, "Pty", self.same_scalar_name)
                changed = self._with_role_party(role, "Agt", self.same_scalar_name)
                reference = self._register_statement_account(baseline)
                store = MemoryArtifactStore()
                accept_bank_statement_evidence(
                    self._command(baseline, reference, f"{role}-party-choice-baseline"),
                    posting.DATABASE_URL,
                    self.case.policy.tenant_reference,
                    artifact_store=store,
                )
                with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                    accept_bank_statement_evidence(
                        self._command(changed, reference, f"{role}-party-choice-changed"),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=store,
                    )

    def test_buyer_entry_projection_keeps_party_choice_non_reversible(self) -> None:
        """Choice semantics affect the digest without exposing party/agent source text."""
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                party_entry = self._ingest_and_read_target_entry(
                    role,
                    self._with_role_party(role, "Pty", self.same_scalar_name),
                    f"{role}-party-choice-party",
                )
                agent_entry = self._ingest_and_read_target_entry(
                    role,
                    self._with_role_party(role, "Agt", self.same_scalar_name),
                    f"{role}-party-choice-agent",
                )
                for projection in (party_entry, agent_entry):
                    self._assert_sha256(projection["counterparty_evidence_hash"])
                    self._assert_sha256(projection["source_entry_hash"])
                    self.assertEqual(
                        Decimal(str(projection["entry_amount"])),
                        self._expected_entry_amount(role),
                    )
                    self.assertEqual(projection["entry_currency_code"], "KRW")
                    first_detail = projection["entry_details"][0]
                    self.assertEqual(
                        Decimal(str(first_detail["detail_amount"])),
                        self._expected_detail_amount(role),
                    )
                    self.assertEqual(first_detail["detail_currency_code"], "KRW")
                self.assertNotEqual(
                    party_entry["counterparty_evidence_hash"],
                    agent_entry["counterparty_evidence_hash"],
                )

                party_public = dict(party_entry)
                agent_public = dict(agent_entry)
                for projection in (party_public, agent_public):
                    projection.pop("bank_statement_entry_id")
                    projection.pop("counterparty_evidence_hash")
                    projection.pop("source_entry_hash")
                self.assertEqual(party_public, agent_public)

                serialized = json.dumps(
                    {"party": party_entry, "agent": agent_entry},
                    sort_keys=True,
                    default=str,
                )
                self.assertNotIn(self.same_scalar_name, serialized)

    def _with_role_party(self, role: str, choice: str, name: str) -> bytes:
        """Return a valid detail with direct Dbtr/Cdtr represented by one Party50Choice."""
        party_body = self._party_choice_xml(choice, name)
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

    @staticmethod
    def _party_choice_xml(choice: str, name: str) -> str:
        """Serialize one Party50Choice branch at the indentation inside Dbtr/Cdtr."""
        if choice == "Pty":
            return (
                "                <Pty>\n"
                f"                  <Nm>{name}</Nm>\n"
                "                </Pty>"
            )
        if choice == "Agt":
            return (
                "                <Agt>\n"
                "                  <FinInstnId>\n"
                f"                    <Nm>{name}</Nm>\n"
                "                  </FinInstnId>\n"
                "                </Agt>"
            )
        raise AssertionError(f"unsupported Party50Choice branch: {choice}")

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
            "ingestion_idempotency_key": f"direct-party-choice-{suffix}-{uuid.uuid4().hex}",
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

    def _assert_sha256(self, value: object) -> None:
        """Require canonical retained SHA-256 syntax before comparing evidence identity."""
        self.assertIsInstance(value, str)
        self.assertRegex(value, _HASH_PATTERN)


if __name__ == "__main__":
    unittest.main()
