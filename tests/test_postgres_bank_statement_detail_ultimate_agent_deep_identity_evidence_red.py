"""PostgreSQL REDs for deep ultimate-agent identity evidence."""

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


class BankStatementDetailUltimateAgentDeepIdentityEvidenceRedTests(unittest.TestCase):
    """Retain deep BranchAndFinancialInstitutionIdentification8 facts by ultimate role."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare isolated PostgreSQL state and schema-shaped ultimate agents."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.identities = {
            "ultimate_debtor": {
                "bicfi": "DEUTDEFF",
                "lei": "7LTWFZYICNSX8D621K86",
                "name": "Ultimate Debtor Institution",
                "branch_id": "ULT-DBTR-BR-001",
                "branch_lei": "529900Z6KVD8Y83D7K60",
                "branch_name": "Ultimate Debtor Branch",
            },
            "ultimate_creditor": {
                "bicfi": "BNPAFRPP",
                "lei": "5493001KJTIIGC8Y1R12",
                "name": "Ultimate Creditor Institution",
                "branch_id": "ULT-CDTR-BR-001",
                "branch_lei": "213800D1EI4B9WTWWD28",
                "branch_name": "Ultimate Creditor Branch",
            },
        }

    def test_institution_and_branch_identity_are_material_for_each_ultimate_agent(self) -> None:
        """Institution and branch facts independently change ultimate-role evidence."""
        for role, identity in self.identities.items():
            baseline = parse_bank_statement_payload(
                self._with_ultimate_agent(role, identity),
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

            for semantic, changed_identity in self._variants(identity).items():
                with self.subTest(role=role, semantic=semantic):
                    changed = parse_bank_statement_payload(
                        self._with_ultimate_agent(role, changed_identity),
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

    def test_branch_layout_is_representation_only_for_each_ultimate_agent(self) -> None:
        """Whitespace inside BrnchId changes raw bytes without semantic drift."""
        needle = b"                <BrnchId>\n"
        for role, identity in self.identities.items():
            with self.subTest(role=role):
                baseline = self._with_ultimate_agent(role, identity)
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

    def test_every_deep_agent_variant_reaches_complete_correction_boundary(self) -> None:
        """Accepted ultimate-agent identity cannot be silently replaced on replay."""
        for role, identity in self.identities.items():
            baseline = self._with_ultimate_agent(role, identity)
            reference = self._register_statement_account(baseline)
            store = MemoryArtifactStore()
            accept_bank_statement_evidence(
                self._command(baseline, reference, f"{role}-deep-baseline"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=store,
            )
            for semantic, changed_identity in self._variants(identity).items():
                with self.subTest(role=role, semantic=semantic):
                    changed = self._with_ultimate_agent(role, changed_identity)
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        _CORRECTION_ERROR,
                    ):
                        accept_bank_statement_evidence(
                            self._command(
                                changed,
                                reference,
                                f"{role}-deep-{semantic}",
                            ),
                            posting.DATABASE_URL,
                            self.case.policy.tenant_reference,
                            artifact_store=store,
                        )

    def test_buyer_projection_keeps_deep_agent_source_values_non_reversible(self) -> None:
        """Deep ultimate-agent values affect digest identity without buyer disclosure."""
        for role, identity in self.identities.items():
            with self.subTest(role=role):
                deep = self._ingest_and_read_first_detail(
                    self._with_ultimate_agent(role, identity),
                    f"{role}-deep",
                )
                bic_only_identity = {"bicfi": identity["bicfi"]}
                bic_only = self._ingest_and_read_first_detail(
                    self._with_ultimate_agent(role, bic_only_identity),
                    f"{role}-bic-only",
                )
                evidence_key = self._evidence_key(role)
                for projection in (deep, bic_only):
                    self._assert_sha256(projection[evidence_key])
                    self._assert_sha256(projection["source_detail_hash"])
                    self.assertEqual(
                        Decimal(str(projection["detail_amount"])),
                        Decimal("25000.00"),
                    )
                    self.assertEqual(projection["detail_currency_code"], "KRW")

                self.assertNotEqual(deep[evidence_key], bic_only[evidence_key])
                self.assertNotEqual(
                    deep["source_detail_hash"],
                    bic_only["source_detail_hash"],
                )
                deep_public = self._public_projection(deep, evidence_key)
                bic_public = self._public_projection(bic_only, evidence_key)
                self.assertEqual(deep_public, bic_public)
                buyer_values = set(self._scalar_leaves(deep_public))
                source_values = {
                    str(value)
                    for key, value in identity.items()
                    if key != "bicfi"
                }
                self.assertTrue(source_values.isdisjoint(buyer_values))

    @staticmethod
    def _variants(identity: dict[str, str]) -> dict[str, dict[str, str]]:
        """Return independent changes for every deep institution/branch scalar."""
        alternatives = {
            "bicfi": "BOFAUS3N",
            "lei": "529900Z6KVD8Y83D7K60",
            "name": identity.get("name", "") + " Updated",
            "branch_id": identity.get("branch_id", "") + "-ALT",
            "branch_lei": "5493001KJTIIGC8Y1R12",
            "branch_name": identity.get("branch_name", "") + " Updated",
        }
        variants: dict[str, dict[str, str]] = {}
        for key, alternative in alternatives.items():
            if alternative == identity.get(key):
                alternative = alternative + "X"
            changed = copy.deepcopy(identity)
            changed[key] = alternative
            variants[f"{key}-value"] = changed
        return variants

    def _with_ultimate_agent(
        self,
        role: str,
        identity: dict[str, str],
    ) -> bytes:
        """Insert an ultimate Agt branch with institution and optional branch identity."""
        marker = "              </Dbtr>\n            </RltdPties>"
        self.assertEqual(self.fixture.count(marker), 1)
        tag = self._xml_tag(role)
        lines = [
            "              </Dbtr>",
            f"              <{tag}>",
            "                <Agt>",
            "                  <FinInstnId>",
            f"                    <BICFI>{identity['bicfi']}</BICFI>",
        ]
        if "lei" in identity:
            lines.append(f"                    <LEI>{identity['lei']}</LEI>")
        if "name" in identity:
            lines.append(f"                    <Nm>{identity['name']}</Nm>")
        lines.append("                  </FinInstnId>")
        if all(key in identity for key in ("branch_id", "branch_lei", "branch_name")):
            lines.extend(
                [
                    "                  <BrnchId>",
                    f"                    <Id>{identity['branch_id']}</Id>",
                    f"                    <LEI>{identity['branch_lei']}</LEI>",
                    f"                    <Nm>{identity['branch_name']}</Nm>",
                    "                  </BrnchId>",
                ]
            )
        lines.extend(
            [
                "                </Agt>",
                f"              </{tag}>",
                "            </RltdPties>",
            ]
        )
        replacement = "\n".join(lines)
        changed = self.fixture.replace(marker, replacement, 1)
        self.assertNotEqual(changed, self.fixture)
        return changed.encode("utf-8")

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
        projection = copy.deepcopy(detail)
        projection.pop(evidence_key)
        projection.pop("source_detail_hash")
        return projection

    @classmethod
    def _scalar_leaves(cls, value: object) -> list[str]:
        """Return exact scalar leaves so privacy checks avoid substring heuristics."""
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
        """Return one supported ingest command with an isolated replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": bank_account_reference,
            "ingestion_idempotency_key": (
                f"ultimate-agent-deep-{suffix}-{uuid.uuid4().hex}"
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
        """Map the logical role to the camt.053 ultimate-party tag."""
        if role == "ultimate_debtor":
            return "UltmtDbtr"
        if role == "ultimate_creditor":
            return "UltmtCdtr"
        raise AssertionError(f"unsupported ultimate role: {role}")

    def _assert_financial_truth(self, entry: object, detail: object) -> None:
        """Keep ultimate-agent provenance independent from exact accounting facts."""
        self.assertEqual(getattr(entry, "entry_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(entry, "entry_currency_code"), "KRW")
        self.assertEqual(getattr(detail, "detail_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(detail, "detail_currency_code"), "KRW")

    def _assert_sha256(self, value: object) -> None:
        """Require canonical SHA-256 syntax before identity comparisons."""
        self.assertIsInstance(value, str)
        self.assertRegex(value, _HASH_PATTERN)


if __name__ == "__main__":
    unittest.main()
