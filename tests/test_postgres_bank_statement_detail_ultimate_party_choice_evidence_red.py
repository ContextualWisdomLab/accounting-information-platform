"""PostgreSQL REDs for ultimate debtor/creditor Party50Choice evidence."""

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


class BankStatementDetailUltimatePartyChoiceEvidenceRedTests(unittest.TestCase):
    """Preserve ultimate Party50Choice semantics as non-reversible evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare an isolated fixture and same-scalar party/agent variants."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.same_scalar_name = "Ultimate Party Same Scalar"
        self.changed_agent_name = "Ultimate Agent Changed Scalar"

    def test_party50_choice_and_agent_name_are_material_for_each_ultimate_role(self) -> None:
        """Pty/Agt discriminator and selected agent value independently change evidence."""
        variants = (
            (
                "choice",
                ("Pty", self.same_scalar_name),
                ("Agt", self.same_scalar_name),
            ),
            (
                "agent-name",
                ("Agt", self.same_scalar_name),
                ("Agt", self.changed_agent_name),
            ),
        )
        for role in ("ultimate_debtor", "ultimate_creditor"):
            for semantic, left_choice, right_choice in variants:
                with self.subTest(role=role, semantic=semantic):
                    left = parse_bank_statement_payload(
                        self._with_ultimate_choice(role, *left_choice),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    right = parse_bank_statement_payload(
                        self._with_ultimate_choice(role, *right_choice),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    left_entry = left.entries[0]
                    right_entry = right.entries[0]
                    left_detail = left_entry.entry_details[0]
                    right_detail = right_entry.entry_details[0]
                    evidence_key = self._evidence_key(role)
                    left_role_hash = getattr(left_detail, evidence_key)
                    right_role_hash = getattr(right_detail, evidence_key)

                    self._assert_financial_truth(left_entry, left_detail)
                    self._assert_financial_truth(right_entry, right_detail)
                    for value in (
                        left_role_hash,
                        right_role_hash,
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

                    self.assertNotEqual(left_role_hash, right_role_hash)
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

    def test_agent_branch_whitespace_is_representation_only_for_each_role(self) -> None:
        """Whitespace inside selected Agt changes raw bytes but not semantic identity."""
        for role in ("ultimate_debtor", "ultimate_creditor"):
            with self.subTest(role=role):
                baseline = self._with_ultimate_choice(
                    role,
                    "Agt",
                    self.same_scalar_name,
                )
                needle = b"                <Agt>\n"
                self.assertEqual(baseline.count(needle), 1)
                formatted = baseline.replace(
                    needle,
                    needle + b"                  \n",
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

    def test_every_party_choice_material_variant_reaches_correction_boundary(self) -> None:
        """Accepted ultimate-party evidence cannot silently replay another choice/value."""
        variants = (
            ("choice", "Pty", self.same_scalar_name, "Agt", self.same_scalar_name),
            (
                "agent-name",
                "Agt",
                self.same_scalar_name,
                "Agt",
                self.changed_agent_name,
            ),
        )
        for role in ("ultimate_debtor", "ultimate_creditor"):
            for semantic, base_choice, base_name, changed_choice, changed_name in variants:
                with self.subTest(role=role, semantic=semantic):
                    baseline = self._with_ultimate_choice(
                        role,
                        base_choice,
                        base_name,
                    )
                    changed = self._with_ultimate_choice(
                        role,
                        changed_choice,
                        changed_name,
                    )
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
                        AccountingValidationError,
                        _CORRECTION_ERROR,
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

    def test_buyer_projection_keeps_party_choice_source_values_non_reversible(self) -> None:
        """Choice/value changes affect internal evidence without exposing source text."""
        for role in ("ultimate_debtor", "ultimate_creditor"):
            with self.subTest(role=role):
                party = self._ingest_and_read_first_detail(
                    self._with_ultimate_choice(
                        role,
                        "Pty",
                        self.same_scalar_name,
                    ),
                    f"{role}-choice-party",
                )
                agent = self._ingest_and_read_first_detail(
                    self._with_ultimate_choice(
                        role,
                        "Agt",
                        self.same_scalar_name,
                    ),
                    f"{role}-choice-agent",
                )
                changed_agent = self._ingest_and_read_first_detail(
                    self._with_ultimate_choice(
                        role,
                        "Agt",
                        self.changed_agent_name,
                    ),
                    f"{role}-choice-agent-changed",
                )
                evidence_key = self._evidence_key(role)
                for projection in (party, agent, changed_agent):
                    self._assert_sha256(projection[evidence_key])
                    self._assert_sha256(projection["source_detail_hash"])
                    self.assertEqual(
                        Decimal(str(projection["detail_amount"])),
                        Decimal("25000.00"),
                    )
                    self.assertEqual(projection["detail_currency_code"], "KRW")

                self.assertNotEqual(party[evidence_key], agent[evidence_key])
                self.assertNotEqual(agent[evidence_key], changed_agent[evidence_key])
                self.assertNotEqual(
                    party["source_detail_hash"],
                    agent["source_detail_hash"],
                )
                self.assertNotEqual(
                    agent["source_detail_hash"],
                    changed_agent["source_detail_hash"],
                )

                public_party = self._public_projection(party, evidence_key)
                public_agent = self._public_projection(agent, evidence_key)
                public_changed = self._public_projection(changed_agent, evidence_key)
                self.assertEqual(public_party, public_agent)
                self.assertEqual(public_agent, public_changed)
                buyer_values = set(self._scalar_leaves(public_changed))
                self.assertNotIn(self.same_scalar_name, buyer_values)
                self.assertNotIn(self.changed_agent_name, buyer_values)

    def _with_ultimate_choice(self, role: str, choice: str, name: str) -> bytes:
        """Insert one schema-shaped ultimate Party50Choice branch into the first detail."""
        marker = "              </Dbtr>\n            </RltdPties>"
        self.assertEqual(self.fixture.count(marker), 1)
        tag = self._xml_tag(role)
        choice_xml = self._party_choice_xml(choice, name)
        replacement = (
            "              </Dbtr>\n"
            f"              <{tag}>\n"
            f"{choice_xml}\n"
            f"              </{tag}>\n"
            "            </RltdPties>"
        )
        return self.fixture.replace(marker, replacement, 1).encode("utf-8")

    @staticmethod
    def _party_choice_xml(choice: str, name: str) -> str:
        """Serialize one Party50Choice branch at ultimate-party indentation."""
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
        detail: dict[str, object],
        evidence_key: str,
    ) -> dict[str, object]:
        """Remove only internal evidence identities before buyer-visible comparison."""
        projection = copy.deepcopy(detail)
        projection.pop(evidence_key)
        projection.pop("source_detail_hash")
        return projection

    @classmethod
    def _scalar_leaves(cls, value: object) -> list[str]:
        """Return exact public scalar leaves without substring heuristics."""
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
                f"ultimate-party-choice-{suffix}-{uuid.uuid4().hex}"
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
        """Map the test role to the camt.053 ultimate-party element."""
        if role == "ultimate_debtor":
            return "UltmtDbtr"
        if role == "ultimate_creditor":
            return "UltmtCdtr"
        raise AssertionError(f"unsupported ultimate role: {role}")

    def _assert_financial_truth(self, entry: object, detail: object) -> None:
        """Keep evidence choice changes independent from exact accounting values."""
        self.assertEqual(getattr(entry, "entry_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(entry, "entry_currency_code"), "KRW")
        self.assertEqual(getattr(detail, "detail_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(detail, "detail_currency_code"), "KRW")

    def _assert_sha256(self, value: object) -> None:
        """Require canonical SHA-256 syntax before comparing evidence identity."""
        self.assertIsInstance(value, str)
        self.assertRegex(value, _HASH_PATTERN)


if __name__ == "__main__":
    unittest.main()
