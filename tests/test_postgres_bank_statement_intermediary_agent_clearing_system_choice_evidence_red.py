"""PostgreSQL REDs for clearing-system choice evidence on intermediary-agent positions."""

from __future__ import annotations

import unittest
import uuid

from accounting_information_platform import AccountingValidationError, MemoryArtifactStore
from tests import (
    test_postgres_bank_statement_intermediary_agent_deep_identity_evidence_red as deep,
)

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementIntermediaryAgentClearingSystemChoiceEvidenceRedTests(
    unittest.TestCase
):
    """Retain ClrSysMmbId choice semantics for IntrmyAgt1, IntrmyAgt2 and IntrmyAgt3."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the source-real PostgreSQL and camt.053 integration fixture."""
        deep.BankStatementIntermediaryAgentDeepIdentityEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare the focused intermediary helper with exception-safe nested cleanup."""
        self.case = deep.BankStatementIntermediaryAgentDeepIdentityEvidenceRedTests(
            "setUp"
        )
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def test_clearing_system_choice_is_material_at_each_chain_position(self) -> None:
        """Choice, value, member id, and absence change canonical intermediary evidence."""
        for slot in (1, 2, 3):
            with self.subTest(slot=slot):
                base_payload = self._payload(
                    slot,
                    choice_kind="proprietary",
                    choice_value="USABA",
                    member_id=f"INTRMY-{slot}-MEMBER-001",
                )
                base = self.case._statement(base_payload)
                base_entry = base.entries[1]
                base_detail = base_entry.entry_details[0]
                digest_key = f"intermediary_agent_{slot}_evidence_hash"
                base_digest = getattr(base_detail, digest_key, None)
                self.case._assert_digest(base_digest)
                self.case._assert_digest(base_detail.source_detail_hash)
                self.case._assert_exact_amount(base_entry, base_detail)

                variants = {
                    "member-id": self._payload(
                        slot,
                        choice_kind="proprietary",
                        choice_value="USABA",
                        member_id=f"INTRMY-{slot}-MEMBER-002",
                    ),
                    "choice-value": self._payload(
                        slot,
                        choice_kind="proprietary",
                        choice_value="ALTCLR",
                        member_id=f"INTRMY-{slot}-MEMBER-001",
                    ),
                    "same-scalar-choice-discriminator": self._payload(
                        slot,
                        choice_kind="code",
                        choice_value="USABA",
                        member_id=f"INTRMY-{slot}-MEMBER-001",
                    ),
                    "choice-absent": self._payload(
                        slot,
                        choice_kind=None,
                        choice_value=None,
                        member_id=f"INTRMY-{slot}-MEMBER-001",
                    ),
                }

                for variant_name, payload in variants.items():
                    with self.subTest(variant=variant_name):
                        statement = self.case._statement(payload)
                        entry = statement.entries[1]
                        detail = entry.entry_details[0]
                        digest = getattr(detail, digest_key, None)
                        self.case._assert_digest(digest)
                        self.case._assert_digest(detail.source_detail_hash)
                        self.assertNotEqual(base_digest, digest)
                        self.assertEqual(
                            base.account_identifier_hash,
                            statement.account_identifier_hash,
                        )
                        self.assertNotEqual(
                            base_detail.source_detail_hash,
                            detail.source_detail_hash,
                        )
                        self.assertNotEqual(
                            base_entry.source_entry_hash,
                            entry.source_entry_hash,
                        )
                        self.assertNotEqual(
                            base.normalized_payload_hash,
                            statement.normalized_payload_hash,
                        )
                        self.assertEqual(
                            base.entries[0].source_entry_hash,
                            statement.entries[0].source_entry_hash,
                        )
                        self.case._assert_exact_amount(entry, detail)

    def test_clearing_system_layout_is_representation_only_at_each_position(self) -> None:
        """Whitespace inside ClrSysId changes raw bytes without changing admitted semantics."""
        needle = b"                    <ClrSysId>\n"
        whitespace = b"                      \n"

        for slot in (1, 2, 3):
            with self.subTest(slot=slot):
                payload = self._payload(
                    slot,
                    choice_kind="proprietary",
                    choice_value="USABA",
                    member_id=f"INTRMY-{slot}-MEMBER-001",
                )
                self.assertEqual(payload.count(needle), 1)
                reformatted = payload.replace(needle, needle + whitespace, 1)
                self.assertNotEqual(payload, reformatted)

                base = self.case._statement(payload)
                changed = self.case._statement(reformatted)
                base_entry = base.entries[1]
                changed_entry = changed.entries[1]
                base_detail = base_entry.entry_details[0]
                changed_detail = changed_entry.entry_details[0]
                digest_key = f"intermediary_agent_{slot}_evidence_hash"
                base_digest = getattr(base_detail, digest_key, None)
                changed_digest = getattr(changed_detail, digest_key, None)
                self.case._assert_digest(base_digest)
                self.case._assert_digest(changed_digest)
                self.case._assert_digest(base_detail.source_detail_hash)
                self.case._assert_digest(changed_detail.source_detail_hash)

                self.assertNotEqual(base.source_artifact_hash, changed.source_artifact_hash)
                self.assertEqual(base_digest, changed_digest)
                self.assertEqual(
                    base_detail.source_detail_hash,
                    changed_detail.source_detail_hash,
                )
                self.assertEqual(
                    base_entry.source_entry_hash,
                    changed_entry.source_entry_hash,
                )
                self.assertEqual(
                    base.normalized_payload_hash,
                    changed.normalized_payload_hash,
                )
                self.case._assert_exact_amount(changed_entry, changed_detail)

    def test_clearing_system_change_requires_explicit_correction(self) -> None:
        """Accepted intermediary clearing evidence cannot be replaced on replay."""
        slot = 3
        base_payload = self._payload(
            slot,
            choice_kind="proprietary",
            choice_value="USABA",
            member_id="INTRMY-3-MEMBER-001",
        )
        changed_payload = self._payload(
            slot,
            choice_kind="code",
            choice_value="USABA",
            member_id="INTRMY-3-MEMBER-001",
        )
        statement = self.case._statement(base_payload)
        account_reference = self.case._register_account(statement, "clearing-correction")
        store = MemoryArtifactStore()
        accepted = deep.accept_bank_statement_evidence(
            self.case._command(base_payload, account_reference, "clearing-base"),
            deep.posting.DATABASE_URL,
            self.case.case.policy.tenant_reference,
            artifact_store=store,
        )
        self.assertFalse(accepted["replayed"])
        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            deep.accept_bank_statement_evidence(
                self.case._command(
                    changed_payload,
                    account_reference,
                    "clearing-changed",
                ),
                deep.posting.DATABASE_URL,
                self.case.case.policy.tenant_reference,
                artifact_store=store,
            )

    def test_clearing_system_choice_stays_digest_only_on_buyer_projection(self) -> None:
        """Buyer reads expose only the purpose digest for intermediary clearing identity."""
        for slot in (1, 2, 3):
            with self.subTest(slot=slot):
                rich_payload = self._payload(
                    slot,
                    choice_kind="proprietary",
                    choice_value="USABA",
                    member_id=f"INTRMY-{slot}-MEMBER-001",
                )
                baseline_payload = self.case._with_chain(
                    slot,
                    self.case.AGENTS[slot],
                )
                rich_statement = self.case._statement(rich_payload)
                baseline_statement = self.case._statement(baseline_payload)
                rich_reference = self.case._register_account(
                    rich_statement,
                    f"clearing-rich-{slot}",
                )
                baseline_reference = self.case._register_account(
                    baseline_statement,
                    f"clearing-baseline-{slot}",
                )
                rich_detail = self.case._ingest_and_read(
                    rich_payload,
                    rich_reference,
                    f"clearing-rich-{slot}-{uuid.uuid4().hex}",
                )
                baseline_detail = self.case._ingest_and_read(
                    baseline_payload,
                    baseline_reference,
                    f"clearing-baseline-{slot}-{uuid.uuid4().hex}",
                )
                digest_key = f"intermediary_agent_{slot}_evidence_hash"

                for projection in (rich_detail, baseline_detail):
                    self.case._assert_digest(projection.get(digest_key))
                    self.case._assert_digest(projection.get("source_detail_hash"))
                    self.assertEqual(projection["detail_amount"], "6000")
                    self.assertEqual(projection["detail_currency_code"], "KRW")

                self.assertNotEqual(
                    rich_detail[digest_key],
                    baseline_detail[digest_key],
                )
                self.assertNotEqual(
                    rich_detail["source_detail_hash"],
                    baseline_detail["source_detail_hash"],
                )

                rich_visible = dict(rich_detail)
                baseline_visible = dict(baseline_detail)
                for projection in (rich_visible, baseline_visible):
                    projection.pop(digest_key)
                    projection.pop("source_detail_hash")
                self.assertEqual(rich_visible, baseline_visible)

    def _payload(
        self,
        slot: int,
        *,
        choice_kind: str | None,
        choice_value: str | None,
        member_id: str,
    ) -> bytes:
        """Insert ClearingSystemMemberIdentification2 after the target intermediary BICFI."""
        identity = self.case.AGENTS[slot]
        payload = self.case._with_chain(slot, identity)
        bicfi = identity["bicfi"]
        needle = f"                  <BICFI>{bicfi}</BICFI>\n".encode("utf-8")
        if payload.count(needle) != 1:
            raise AssertionError("target intermediary position must contain exactly one BICFI")
        clearing = self._clearing_system_xml(
            choice_kind=choice_kind,
            choice_value=choice_value,
            member_id=member_id,
        )
        return payload.replace(needle, needle + clearing, 1)

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
        lines = ["                  <ClrSysMmbId>\n"]
        if choice_kind is not None or choice_value is not None:
            if choice_kind not in {"code", "proprietary"} or not choice_value:
                raise AssertionError("ClrSysId requires code|proprietary plus a value")
            tag = "Cd" if choice_kind == "code" else "Prtry"
            lines.extend(
                [
                    "                    <ClrSysId>\n",
                    f"                      <{tag}>{choice_value}</{tag}>\n",
                    "                    </ClrSysId>\n",
                ]
            )
        lines.extend(
            [
                f"                    <MmbId>{member_id}</MmbId>\n",
                "                  </ClrSysMmbId>\n",
            ]
        )
        return "".join(lines).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
