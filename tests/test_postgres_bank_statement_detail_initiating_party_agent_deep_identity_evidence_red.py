"""PostgreSQL REDs for deep initiating-party agent identity evidence."""

from __future__ import annotations

import copy
import unittest
import uuid
from decimal import Decimal

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    parse_bank_statement_payload,
)
from tests import test_postgres_bank_statement_detail_initiating_party_choice_evidence_red as initiating_choice
from tests import test_postgres_bank_statement_detail_initiating_party_evidence_red as initiating
from tests import test_postgres_posting as posting

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailInitiatingPartyAgentDeepIdentityEvidenceRedTests(
    unittest.TestCase
):
    """Retain deep InitgPty/Agt facts as non-reversible Evidence-Audit provenance."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        initiating_choice.BankStatementDetailInitiatingPartyChoiceEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one exception-safe initiating-party choice helper."""
        self.case = initiating_choice.BankStatementDetailInitiatingPartyChoiceEvidenceRedTests(
            "setUp"
        )
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.identity = {
            "bicfi": "DEUTDEFF",
            "lei": "7LTWFZYICNSX8D621K86",
            "name": "Initiating Financial Institution",
            "branch_id": "INIT-BR-001",
            "branch_lei": "529900Z6KVD8Y83D7K60",
            "branch_name": "Initiating Branch",
        }

    def test_institution_and_branch_identity_are_material_initiating_evidence(self) -> None:
        """Each institution and branch scalar independently changes initiating evidence."""
        baseline = parse_bank_statement_payload(
            self._payload(self.identity),
            CAMT053_MESSAGE_DEFINITION,
        )
        baseline_entry = baseline.entries[0]
        baseline_detail = baseline_entry.entry_details[0]
        self._assert_statement_truth(baseline, baseline_entry, baseline_detail)

        for semantic, changed_identity in self._variants(self.identity).items():
            with self.subTest(semantic=semantic):
                changed = parse_bank_statement_payload(
                    self._payload(changed_identity),
                    CAMT053_MESSAGE_DEFINITION,
                )
                changed_entry = changed.entries[0]
                changed_detail = changed_entry.entry_details[0]
                self._assert_statement_truth(changed, changed_entry, changed_detail)

                self.assertNotEqual(
                    baseline_detail.initiating_party_evidence_hash,
                    changed_detail.initiating_party_evidence_hash,
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
                    baseline.entries[1].source_entry_hash,
                    changed.entries[1].source_entry_hash,
                )

    def test_branch_layout_is_representation_only(self) -> None:
        """Whitespace inside InitgPty/Agt/BrnchId changes bytes without semantic drift."""
        baseline_payload = self._payload(self.identity)
        needle = b"                  <BrnchId>\n"
        self.assertEqual(baseline_payload.count(needle), 1)
        formatted_payload = baseline_payload.replace(
            needle,
            needle + b"                    \n",
            1,
        )
        self.assertNotEqual(baseline_payload, formatted_payload)

        baseline = parse_bank_statement_payload(
            baseline_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        formatted = parse_bank_statement_payload(
            formatted_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        baseline_entry = baseline.entries[0]
        formatted_entry = formatted.entries[0]
        baseline_detail = baseline_entry.entry_details[0]
        formatted_detail = formatted_entry.entry_details[0]
        self._assert_statement_truth(baseline, baseline_entry, baseline_detail)
        self._assert_statement_truth(formatted, formatted_entry, formatted_detail)
        self.case._assert_sha256(baseline.source_artifact_hash)
        self.case._assert_sha256(formatted.source_artifact_hash)

        self.assertNotEqual(baseline.source_artifact_hash, formatted.source_artifact_hash)
        self.assertEqual(
            baseline_detail.initiating_party_evidence_hash,
            formatted_detail.initiating_party_evidence_hash,
        )
        self.assertEqual(
            baseline_detail.source_detail_hash,
            formatted_detail.source_detail_hash,
        )
        self.assertEqual(
            baseline_entry.source_entry_hash,
            formatted_entry.source_entry_hash,
        )
        self.assertEqual(
            baseline.normalized_payload_hash,
            formatted.normalized_payload_hash,
        )
        self.assertEqual(
            baseline.account_identifier_hash,
            formatted.account_identifier_hash,
        )
        self.assertEqual(
            baseline.entries[1].source_entry_hash,
            formatted.entries[1].source_entry_hash,
        )

    def test_every_deep_agent_variant_reaches_complete_correction_boundary(self) -> None:
        """Accepted initiating-agent identity cannot be silently replaced on replay."""
        baseline_payload = self._payload(self.identity)
        for semantic, changed_identity in self._variants(self.identity).items():
            with self.subTest(semantic=semantic):
                bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
                self.case.helper._register_bank_account(bank_account_reference)
                store = MemoryArtifactStore()
                initiating.accept_bank_statement_evidence(
                    self.case.helper._command(
                        baseline_payload,
                        f"initiating-agent-deep-{semantic}-baseline",
                        bank_account_reference,
                    ),
                    posting.DATABASE_URL,
                    self.case.helper.case.policy.tenant_reference,
                    artifact_store=store,
                )
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    _CORRECTION_ERROR,
                ):
                    initiating.accept_bank_statement_evidence(
                        self.case.helper._command(
                            self._payload(changed_identity),
                            f"initiating-agent-deep-{semantic}-changed",
                            bank_account_reference,
                        ),
                        posting.DATABASE_URL,
                        self.case.helper.case.policy.tenant_reference,
                        artifact_store=store,
                    )

    def test_buyer_projection_keeps_deep_agent_values_non_reversible(self) -> None:
        """Deep initiating-agent values affect evidence identity without buyer disclosure."""
        deep_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        bic_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        self.case.helper._register_bank_account(deep_reference)
        self.case.helper._register_bank_account(bic_reference)
        deep_entry, deep_detail = self.case._ingest_and_read_first_entry_and_detail(
            self._payload(self.identity),
            deep_reference,
            f"initiating-agent-deep-{uuid.uuid4().hex}",
        )
        bic_only_identity = {"bicfi": self.identity["bicfi"]}
        bic_entry, bic_detail = self.case._ingest_and_read_first_entry_and_detail(
            self._payload(bic_only_identity),
            bic_reference,
            f"initiating-agent-bic-only-{uuid.uuid4().hex}",
        )
        evidence_key = "initiating_party_evidence_hash"

        for entry, detail in ((deep_entry, deep_detail), (bic_entry, bic_detail)):
            self._assert_uuid(entry["bank_statement_entry_id"])
            self.case._assert_sha256(entry["source_entry_hash"])
            self.case._assert_sha256(detail[evidence_key])
            self.case._assert_sha256(detail["source_detail_hash"])
            self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
            self.assertEqual(entry["entry_currency_code"], "KRW")
            self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
            self.assertEqual(detail["detail_currency_code"], "KRW")

        self.assertNotEqual(deep_detail[evidence_key], bic_detail[evidence_key])
        self.assertNotEqual(
            deep_detail["source_detail_hash"],
            bic_detail["source_detail_hash"],
        )
        self.assertNotEqual(deep_entry["source_entry_hash"], bic_entry["source_entry_hash"])
        deep_public = self._public_entry_projection(deep_entry, evidence_key)
        bic_public = self._public_entry_projection(bic_entry, evidence_key)
        self.assertEqual(deep_public, bic_public)
        buyer_values = set(self._scalar_leaves(deep_public))
        source_values = {str(value) for value in self.identity.values()}
        self.assertTrue(source_values.isdisjoint(buyer_values))

    def _payload(self, identity: dict[str, str]) -> bytes:
        """Insert one schema-shaped InitgPty/Agt deep identity branch."""
        return self.case._with_choice(self._agent_xml(identity))

    @staticmethod
    def _agent_xml(identity: dict[str, str]) -> str:
        """Serialize BranchAndFinancialInstitutionIdentification8 in schema order."""
        lines = [
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
        lines.append("                </Agt>")
        return "\n".join(lines)

    @staticmethod
    def _variants(identity: dict[str, str]) -> dict[str, dict[str, str]]:
        """Return independent changes for each institution and branch identity scalar."""
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

    def _assert_statement_truth(self, statement: object, entry: object, detail: object) -> None:
        """Require canonical evidence identities and preserve exact transaction facts."""
        for value in (
            getattr(detail, "initiating_party_evidence_hash"),
            getattr(detail, "source_detail_hash"),
            getattr(entry, "source_entry_hash"),
            getattr(statement, "normalized_payload_hash"),
            getattr(statement, "account_identifier_hash"),
            getattr(statement.entries[1], "source_entry_hash"),
        ):
            self.case._assert_sha256(value)
        self.assertEqual(getattr(entry, "entry_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(entry, "entry_currency_code"), "KRW")
        self.assertEqual(getattr(detail, "detail_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(detail, "detail_currency_code"), "KRW")

    @staticmethod
    def _assert_uuid(value: object) -> None:
        """Require canonical lowercase hyphenated UUID text before hiding server identity."""
        if not isinstance(value, str):
            raise AssertionError(f"expected UUID text, got {value!r}")
        try:
            parsed = uuid.UUID(value)
        except (ValueError, AttributeError) as exc:
            raise AssertionError(f"expected canonical UUID text, got {value!r}") from exc
        if str(parsed) != value:
            raise AssertionError(f"expected canonical UUID text, got {value!r}")

    @staticmethod
    def _public_entry_projection(
        entry: dict[str, object],
        evidence_key: str,
    ) -> dict[str, object]:
        """Remove only server identity and internal evidence hashes for comparison."""
        projection = copy.deepcopy(entry)
        projection.pop("bank_statement_entry_id")
        projection.pop("source_entry_hash")
        details = projection.get("entry_details")
        if not isinstance(details, list):
            raise AssertionError("expected entry_details to be a list")
        for detail in details:
            if not isinstance(detail, dict):
                raise AssertionError("expected buyer detail mapping")
            detail.pop(evidence_key)
            detail.pop("source_detail_hash")
        return projection

    @classmethod
    def _scalar_leaves(cls, value: object) -> list[str]:
        """Collect exact scalar buyer leaves recursively without substring heuristics."""
        leaves: list[str] = []
        if isinstance(value, dict):
            for child in value.values():
                leaves.extend(cls._scalar_leaves(child))
        elif isinstance(value, (list, tuple)):
            for child in value:
                leaves.extend(cls._scalar_leaves(child))
        elif value is not None:
            leaves.append(str(value))
        return leaves


if __name__ == "__main__":
    unittest.main()
