"""PostgreSQL REDs for ultimate-agent clearing-system choice evidence."""

from __future__ import annotations

import unittest
import uuid

from accounting_information_platform import AccountingValidationError, MemoryArtifactStore
from tests import (
    test_postgres_bank_statement_detail_ultimate_agent_deep_identity_evidence_red as deep,
)

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailUltimateAgentClearingSystemChoiceEvidenceRedTests(
    unittest.TestCase
):
    """Retain ClearingSystemMemberIdentification2 semantics for ultimate agents."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the source-real PostgreSQL and camt.053 integration fixture."""
        deep.BankStatementDetailUltimateAgentDeepIdentityEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare the focused ultimate-agent helper with exception-safe cleanup."""
        self.case = deep.BankStatementDetailUltimateAgentDeepIdentityEvidenceRedTests(
            "setUp"
        )
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def test_clearing_system_choice_member_and_presence_are_material_for_each_role(self) -> None:
        """Choice, value, member id, and optional ClrSysId presence change evidence."""
        for role, identity in self.case.identities.items():
            baseline_payload = self._payload(
                role,
                choice_kind="proprietary",
                choice_value="USFW",
                member_id=f"{role.upper()}-MEMBER-001",
            )
            baseline = deep.parse_bank_statement_payload(
                baseline_payload,
                deep.CAMT053_MESSAGE_DEFINITION,
            )
            baseline_entry = baseline.entries[0]
            baseline_detail = baseline_entry.entry_details[0]
            evidence_key = self.case._evidence_key(role)
            baseline_role_hash = getattr(baseline_detail, evidence_key)
            self.case._assert_financial_truth(baseline_entry, baseline_detail)
            for value in (
                baseline_role_hash,
                baseline_detail.source_detail_hash,
                baseline_entry.source_entry_hash,
                baseline.normalized_payload_hash,
                baseline.account_identifier_hash,
                baseline.entries[1].source_entry_hash,
            ):
                self.case._assert_sha256(value)

            for semantic, payload in self._variants(role).items():
                with self.subTest(role=role, semantic=semantic):
                    changed = deep.parse_bank_statement_payload(
                        payload,
                        deep.CAMT053_MESSAGE_DEFINITION,
                    )
                    changed_entry = changed.entries[0]
                    changed_detail = changed_entry.entry_details[0]
                    changed_role_hash = getattr(changed_detail, evidence_key)
                    self.case._assert_financial_truth(changed_entry, changed_detail)
                    for value in (
                        changed_role_hash,
                        changed_detail.source_detail_hash,
                        changed_entry.source_entry_hash,
                        changed.normalized_payload_hash,
                        changed.account_identifier_hash,
                        changed.entries[1].source_entry_hash,
                    ):
                        self.case._assert_sha256(value)

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

    def test_clearing_system_layout_is_representation_only_for_each_role(self) -> None:
        """Whitespace inside ClrSysId changes bytes without changing semantics."""
        needle = b"                      <ClrSysId>\n"
        for role in self.case.identities:
            with self.subTest(role=role):
                baseline = self._payload(
                    role,
                    choice_kind="proprietary",
                    choice_value="USFW",
                    member_id=f"{role.upper()}-MEMBER-001",
                )
                self.assertEqual(baseline.count(needle), 1)
                formatted = baseline.replace(
                    needle,
                    needle + b"                        \n",
                    1,
                )
                self.assertNotEqual(baseline, formatted)

                left = deep.parse_bank_statement_payload(
                    baseline,
                    deep.CAMT053_MESSAGE_DEFINITION,
                )
                right = deep.parse_bank_statement_payload(
                    formatted,
                    deep.CAMT053_MESSAGE_DEFINITION,
                )
                left_entry = left.entries[0]
                right_entry = right.entries[0]
                left_detail = left_entry.entry_details[0]
                right_detail = right_entry.entry_details[0]
                evidence_key = self.case._evidence_key(role)
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
                    self.case._assert_sha256(value)

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

    def test_every_clearing_variant_reaches_complete_correction_boundary(self) -> None:
        """Accepted ultimate-agent clearing evidence cannot be silently replaced."""
        for role in self.case.identities:
            baseline = self._payload(
                role,
                choice_kind="proprietary",
                choice_value="USFW",
                member_id=f"{role.upper()}-MEMBER-001",
            )
            reference = self.case._register_statement_account(baseline)
            store = MemoryArtifactStore()
            deep.accept_bank_statement_evidence(
                self.case._command(
                    baseline,
                    reference,
                    f"{role}-clearing-baseline",
                ),
                deep.posting.DATABASE_URL,
                self.case.case.policy.tenant_reference,
                artifact_store=store,
            )
            for semantic, payload in self._variants(role).items():
                with self.subTest(role=role, semantic=semantic):
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        _CORRECTION_ERROR,
                    ):
                        deep.accept_bank_statement_evidence(
                            self.case._command(
                                payload,
                                reference,
                                f"{role}-clearing-{semantic}",
                            ),
                            deep.posting.DATABASE_URL,
                            self.case.case.policy.tenant_reference,
                            artifact_store=store,
                        )

    def test_buyer_projection_keeps_clearing_source_values_non_reversible(self) -> None:
        """Clearing-system facts affect evidence without becoming buyer fields."""
        for role, identity in self.case.identities.items():
            with self.subTest(role=role):
                rich_payload = self._payload(
                    role,
                    choice_kind="proprietary",
                    choice_value="ULTIMATE-CLEARING-SYSTEM",
                    member_id=f"ULTIMATE-{role.upper()}-MEMBER-7781",
                )
                baseline_payload = self.case._with_ultimate_agent(
                    role,
                    {"bicfi": identity["bicfi"]},
                )
                rich = self.case._ingest_and_read_first_detail(
                    rich_payload,
                    f"{role}-clearing-rich-{uuid.uuid4().hex}",
                )
                baseline = self.case._ingest_and_read_first_detail(
                    baseline_payload,
                    f"{role}-clearing-baseline-{uuid.uuid4().hex}",
                )
                evidence_key = self.case._evidence_key(role)
                for projection in (rich, baseline):
                    self.case._assert_sha256(projection[evidence_key])
                    self.case._assert_sha256(projection["source_detail_hash"])

                self.assertNotEqual(rich[evidence_key], baseline[evidence_key])
                self.assertNotEqual(
                    rich["source_detail_hash"],
                    baseline["source_detail_hash"],
                )
                rich_public = self.case._public_projection(rich, evidence_key)
                baseline_public = self.case._public_projection(baseline, evidence_key)
                self.assertEqual(rich_public, baseline_public)
                buyer_values = set(self.case._scalar_leaves(rich_public))
                self.assertNotIn("ULTIMATE-CLEARING-SYSTEM", buyer_values)
                self.assertNotIn(
                    f"ULTIMATE-{role.upper()}-MEMBER-7781",
                    buyer_values,
                )

    def _variants(self, role: str) -> dict[str, bytes]:
        """Build independent clearing member, choice, value, and presence variants."""
        member = f"{role.upper()}-MEMBER-001"
        return {
            "member-id": self._payload(
                role,
                choice_kind="proprietary",
                choice_value="USFW",
                member_id=f"{role.upper()}-MEMBER-002",
            ),
            "choice-value": self._payload(
                role,
                choice_kind="proprietary",
                choice_value="ALTCLR",
                member_id=member,
            ),
            "same-scalar-choice-discriminator": self._payload(
                role,
                choice_kind="code",
                choice_value="USFW",
                member_id=member,
            ),
            "choice-absent": self._payload(
                role,
                choice_kind=None,
                choice_value=None,
                member_id=member,
            ),
        }

    def _payload(
        self,
        role: str,
        *,
        choice_kind: str | None,
        choice_value: str | None,
        member_id: str,
    ) -> bytes:
        """Insert ClearingSystemMemberIdentification2 after ultimate-agent BICFI."""
        identity = self.case.identities[role]
        payload = self.case._with_ultimate_agent(role, {"bicfi": identity["bicfi"]})
        bicfi = identity["bicfi"]
        needle = f"                    <BICFI>{bicfi}</BICFI>\n".encode("utf-8")
        self.assertEqual(payload.count(needle), 1)
        return payload.replace(
            needle,
            needle
            + self._clearing_system_xml(
                choice_kind=choice_kind,
                choice_value=choice_value,
                member_id=member_id,
            ),
            1,
        )

    @staticmethod
    def _clearing_system_xml(
        *,
        choice_kind: str | None,
        choice_value: str | None,
        member_id: str,
    ) -> bytes:
        """Serialize ClearingSystemMemberIdentification2 in V14 sequence order."""
        if not member_id:
            raise AssertionError("MmbId is mandatory")
        lines = ["                    <ClrSysMmbId>\n"]
        if choice_kind is not None or choice_value is not None:
            if choice_kind not in {"code", "proprietary"} or not choice_value:
                raise AssertionError("ClrSysId requires code|proprietary plus a value")
            tag = "Cd" if choice_kind == "code" else "Prtry"
            lines.extend(
                [
                    "                      <ClrSysId>\n",
                    f"                        <{tag}>{choice_value}</{tag}>\n",
                    "                      </ClrSysId>\n",
                ]
            )
        lines.extend(
            [
                f"                      <MmbId>{member_id}</MmbId>\n",
                "                    </ClrSysMmbId>\n",
            ]
        )
        return "".join(lines).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
