"""PostgreSQL REDs for initiating-party agent clearing-system choice evidence."""

from __future__ import annotations

import unittest
import uuid

from accounting_information_platform import AccountingValidationError, MemoryArtifactStore
from tests import (
    test_postgres_bank_statement_detail_initiating_party_agent_deep_identity_evidence_red as deep,
)

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailInitiatingPartyAgentClearingSystemChoiceEvidenceRedTests(
    unittest.TestCase
):
    """Retain ClearingSystemMemberIdentification2 semantics for InitgPty/Agt."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL initiating-agent fixture."""
        deep.BankStatementDetailInitiatingPartyAgentDeepIdentityEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one exception-safe deep initiating-agent helper."""
        self.deep_case = deep.BankStatementDetailInitiatingPartyAgentDeepIdentityEvidenceRedTests(
            "setUp"
        )
        self.addCleanup(self.deep_case.doCleanups)
        self.deep_case.setUp()
        self.addCleanup(self.deep_case.tearDown)

    def test_clearing_system_choice_member_and_presence_are_material(self) -> None:
        """Choice, value, member id, and optional ClrSysId presence change evidence."""
        baseline = deep.parse_bank_statement_payload(
            self._payload(
                choice_kind="proprietary",
                choice_value="USABA",
                member_id="INITIATING-MEMBER-001",
            ),
            deep.CAMT053_MESSAGE_DEFINITION,
        )
        baseline_entry = baseline.entries[0]
        baseline_detail = baseline_entry.entry_details[0]
        self.deep_case._assert_statement_truth(
            baseline,
            baseline_entry,
            baseline_detail,
        )

        for semantic, payload in self._variants().items():
            with self.subTest(semantic=semantic):
                changed = deep.parse_bank_statement_payload(
                    payload,
                    deep.CAMT053_MESSAGE_DEFINITION,
                )
                changed_entry = changed.entries[0]
                changed_detail = changed_entry.entry_details[0]
                self.deep_case._assert_statement_truth(
                    changed,
                    changed_entry,
                    changed_detail,
                )

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

    def test_clearing_system_layout_is_representation_only(self) -> None:
        """Whitespace inside ClrSysId changes artifact bytes without semantic drift."""
        baseline_payload = self._payload(
            choice_kind="proprietary",
            choice_value="USABA",
            member_id="INITIATING-MEMBER-001",
        )
        needle = b"                      <ClrSysId>\n"
        self.assertEqual(baseline_payload.count(needle), 1)
        formatted_payload = baseline_payload.replace(
            needle,
            needle + b"                        \n",
            1,
        )
        self.assertNotEqual(baseline_payload, formatted_payload)

        baseline = deep.parse_bank_statement_payload(
            baseline_payload,
            deep.CAMT053_MESSAGE_DEFINITION,
        )
        formatted = deep.parse_bank_statement_payload(
            formatted_payload,
            deep.CAMT053_MESSAGE_DEFINITION,
        )
        baseline_entry = baseline.entries[0]
        formatted_entry = formatted.entries[0]
        baseline_detail = baseline_entry.entry_details[0]
        formatted_detail = formatted_entry.entry_details[0]
        self.deep_case._assert_statement_truth(
            baseline,
            baseline_entry,
            baseline_detail,
        )
        self.deep_case._assert_statement_truth(
            formatted,
            formatted_entry,
            formatted_detail,
        )
        self.deep_case.case._assert_sha256(baseline.source_artifact_hash)
        self.deep_case.case._assert_sha256(formatted.source_artifact_hash)

        self.assertNotEqual(
            baseline.source_artifact_hash,
            formatted.source_artifact_hash,
        )
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

    def test_every_clearing_variant_reaches_complete_correction_boundary(self) -> None:
        """Accepted initiating-agent clearing evidence cannot be silently replaced."""
        baseline_payload = self._payload(
            choice_kind="proprietary",
            choice_value="USABA",
            member_id="INITIATING-MEMBER-001",
        )
        for semantic, payload in self._variants().items():
            with self.subTest(semantic=semantic):
                bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
                self.deep_case.case.helper._register_bank_account(bank_account_reference)
                store = MemoryArtifactStore()
                deep.initiating.accept_bank_statement_evidence(
                    self.deep_case.case.helper._command(
                        baseline_payload,
                        f"initiating-agent-clearing-{semantic}-baseline",
                        bank_account_reference,
                    ),
                    deep.posting.DATABASE_URL,
                    self.deep_case.case.helper.case.policy.tenant_reference,
                    artifact_store=store,
                )
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    _CORRECTION_ERROR,
                ):
                    deep.initiating.accept_bank_statement_evidence(
                        self.deep_case.case.helper._command(
                            payload,
                            f"initiating-agent-clearing-{semantic}-changed",
                            bank_account_reference,
                        ),
                        deep.posting.DATABASE_URL,
                        self.deep_case.case.helper.case.policy.tenant_reference,
                        artifact_store=store,
                    )

    def test_buyer_projection_keeps_clearing_source_values_non_reversible(self) -> None:
        """Clearing-system facts affect evidence identity without buyer disclosure."""
        rich_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        baseline_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        self.deep_case.case.helper._register_bank_account(rich_reference)
        self.deep_case.case.helper._register_bank_account(baseline_reference)
        rich_entry, rich_detail = self.deep_case.case._ingest_and_read_first_entry_and_detail(
            self._payload(
                choice_kind="proprietary",
                choice_value="INITIATING-CLEARING-SYSTEM",
                member_id="INITIATING-MEMBER-7781",
            ),
            rich_reference,
            f"initiating-agent-clearing-rich-{uuid.uuid4().hex}",
        )
        baseline_entry, baseline_detail = (
            self.deep_case.case._ingest_and_read_first_entry_and_detail(
                self.deep_case._payload({"bicfi": self.deep_case.identity["bicfi"]}),
                baseline_reference,
                f"initiating-agent-clearing-baseline-{uuid.uuid4().hex}",
            )
        )
        evidence_key = "initiating_party_evidence_hash"

        for entry, detail in (
            (rich_entry, rich_detail),
            (baseline_entry, baseline_detail),
        ):
            self.deep_case._assert_uuid(entry["bank_statement_entry_id"])
            self.deep_case.case._assert_sha256(entry["source_entry_hash"])
            self.deep_case.case._assert_sha256(detail[evidence_key])
            self.deep_case.case._assert_sha256(detail["source_detail_hash"])

        self.assertNotEqual(rich_detail[evidence_key], baseline_detail[evidence_key])
        self.assertNotEqual(
            rich_detail["source_detail_hash"],
            baseline_detail["source_detail_hash"],
        )
        self.assertNotEqual(
            rich_entry["source_entry_hash"],
            baseline_entry["source_entry_hash"],
        )
        rich_public = self.deep_case._public_entry_projection(rich_entry, evidence_key)
        baseline_public = self.deep_case._public_entry_projection(
            baseline_entry,
            evidence_key,
        )
        self.assertEqual(rich_public, baseline_public)
        buyer_values = set(self.deep_case._scalar_leaves(rich_public))
        self.assertNotIn("INITIATING-CLEARING-SYSTEM", buyer_values)
        self.assertNotIn("INITIATING-MEMBER-7781", buyer_values)

    def _variants(self) -> dict[str, bytes]:
        """Build independent clearing member, choice, value, and presence variants."""
        return {
            "member-id": self._payload(
                choice_kind="proprietary",
                choice_value="USABA",
                member_id="INITIATING-MEMBER-002",
            ),
            "choice-value": self._payload(
                choice_kind="proprietary",
                choice_value="ALTCLR",
                member_id="INITIATING-MEMBER-001",
            ),
            "same-scalar-choice-discriminator": self._payload(
                choice_kind="code",
                choice_value="USABA",
                member_id="INITIATING-MEMBER-001",
            ),
            "choice-absent": self._payload(
                choice_kind=None,
                choice_value=None,
                member_id="INITIATING-MEMBER-001",
            ),
        }

    def _payload(
        self,
        *,
        choice_kind: str | None,
        choice_value: str | None,
        member_id: str,
    ) -> bytes:
        """Insert ClearingSystemMemberIdentification2 after InitgPty/Agt BICFI."""
        bicfi = self.deep_case.identity["bicfi"]
        payload = self.deep_case._payload({"bicfi": bicfi})
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
