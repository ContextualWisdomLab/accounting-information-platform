"""PostgreSQL REDs for initiating-party Party50Choice evidence semantics."""

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
    load_canonical_statement_fixture,
    parse_bank_statement_payload,
)
from tests import test_postgres_bank_statement_detail_initiating_party_evidence_red as initiating
from tests import test_postgres_posting as posting


class BankStatementDetailInitiatingPartyChoiceEvidenceRedTests(unittest.TestCase):
    """Preserve InitgPty Pty|Agt choice without promoting it to accounting truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        initiating.BankStatementDetailInitiatingPartyEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one isolated initiating-party helper and canonical fixture."""
        self.helper = initiating.BankStatementDetailInitiatingPartyEvidenceRedTests("setUp")
        self.addCleanup(self.helper.doCleanups)
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.same_scalar = "Initiating Evidence Shared"
        self.changed_agent_name = "Initiating Agent Changed"

    def test_party_agent_choice_and_agent_name_are_material_detail_evidence(self) -> None:
        """Same-scalar Pty|Agt choice and Agt name changes alter initiating evidence identity."""
        variants = {
            "party": self._with_choice(self._party_xml(self.same_scalar)),
            "agent_same_scalar": self._with_choice(self._agent_xml(self.same_scalar)),
            "agent_changed_name": self._with_choice(self._agent_xml(self.changed_agent_name)),
        }
        parsed = {
            label: parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
            for label, payload in variants.items()
        }

        party = parsed["party"]
        agent = parsed["agent_same_scalar"]
        changed_agent = parsed["agent_changed_name"]
        party_detail = party.entries[0].entry_details[0]
        agent_detail = agent.entries[0].entry_details[0]
        changed_agent_detail = changed_agent.entries[0].entry_details[0]

        for statement, detail in (
            (party, party_detail),
            (agent, agent_detail),
            (changed_agent, changed_agent_detail),
        ):
            entry = statement.entries[0]
            self._assert_sha256(getattr(detail, "initiating_party_evidence_hash"))
            self._assert_sha256(detail.source_detail_hash)
            self._assert_sha256(entry.source_entry_hash)
            self._assert_sha256(statement.entries[1].source_entry_hash)
            self._assert_sha256(statement.normalized_payload_hash)
            self._assert_sha256(statement.account_identifier_hash)
            self.assertEqual(entry.entry_amount, Decimal("25000.00"))
            self.assertEqual(entry.entry_currency_code, "KRW")
            self.assertEqual(detail.detail_amount, Decimal("25000.00"))
            self.assertEqual(detail.detail_currency_code, "KRW")

        self.assertNotEqual(
            getattr(party_detail, "initiating_party_evidence_hash"),
            getattr(agent_detail, "initiating_party_evidence_hash"),
        )
        self.assertNotEqual(
            getattr(agent_detail, "initiating_party_evidence_hash"),
            getattr(changed_agent_detail, "initiating_party_evidence_hash"),
        )
        self.assertNotEqual(party_detail.source_detail_hash, agent_detail.source_detail_hash)
        self.assertNotEqual(agent_detail.source_detail_hash, changed_agent_detail.source_detail_hash)
        self.assertNotEqual(party.entries[0].source_entry_hash, agent.entries[0].source_entry_hash)
        self.assertNotEqual(
            agent.entries[0].source_entry_hash,
            changed_agent.entries[0].source_entry_hash,
        )
        self.assertNotEqual(party.normalized_payload_hash, agent.normalized_payload_hash)
        self.assertNotEqual(agent.normalized_payload_hash, changed_agent.normalized_payload_hash)

        self.assertEqual(party.account_identifier_hash, agent.account_identifier_hash)
        self.assertEqual(agent.account_identifier_hash, changed_agent.account_identifier_hash)
        self.assertEqual(party.entries[1].source_entry_hash, agent.entries[1].source_entry_hash)
        self.assertEqual(
            agent.entries[1].source_entry_hash,
            changed_agent.entries[1].source_entry_hash,
        )

    def test_party_agent_choice_and_agent_name_reach_explicit_correction_boundary(self) -> None:
        """Accepted InitgPty evidence cannot silently replay a changed choice or agent name."""
        cases = (
            (
                "choice",
                self._with_choice(self._party_xml(self.same_scalar)),
                self._with_choice(self._agent_xml(self.same_scalar)),
            ),
            (
                "agent-name",
                self._with_choice(self._agent_xml(self.same_scalar)),
                self._with_choice(self._agent_xml(self.changed_agent_name)),
            ),
        )
        for label, baseline_payload, changed_payload in cases:
            with self.subTest(semantic=label):
                bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
                self.helper._register_bank_account(bank_account_reference)
                store = MemoryArtifactStore()
                initiating.accept_bank_statement_evidence(
                    self.helper._command(
                        baseline_payload,
                        f"initiating-choice-{label}-baseline",
                        bank_account_reference,
                    ),
                    posting.DATABASE_URL,
                    self.helper.case.policy.tenant_reference,
                    artifact_store=store,
                )
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    (
                        r"^statement identity already exists with different entry evidence\. "
                        r"Use an explicit correction contract, then retry ingest\.$"
                    ),
                ):
                    initiating.accept_bank_statement_evidence(
                        self.helper._command(
                            changed_payload,
                            f"initiating-choice-{label}-changed",
                            bank_account_reference,
                        ),
                        posting.DATABASE_URL,
                        self.helper.case.policy.tenant_reference,
                        artifact_store=store,
                    )

    def test_buyer_projection_keeps_choice_digest_but_not_reversible_party_or_agent_name(self) -> None:
        """Buyer reads expose a choice-sensitive digest without source party or agent names."""
        party_payload = self._with_choice(self._party_xml(self.same_scalar))
        agent_payload = self._with_choice(self._agent_xml(self.same_scalar))

        party_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        agent_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        self.helper._register_bank_account(party_reference)
        self.helper._register_bank_account(agent_reference)
        party_entry, party_detail = self._ingest_and_read_first_entry_and_detail(
            party_payload,
            party_reference,
            "initiating-choice-party-read",
        )
        agent_entry, agent_detail = self._ingest_and_read_first_entry_and_detail(
            agent_payload,
            agent_reference,
            "initiating-choice-agent-read",
        )

        evidence_key = "initiating_party_evidence_hash"
        for entry, detail in (
            (party_entry, party_detail),
            (agent_entry, agent_detail),
        ):
            self._assert_sha256(detail[evidence_key])
            self._assert_sha256(detail["source_detail_hash"])
            self._assert_sha256(entry["source_entry_hash"])
            self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
            self.assertEqual(entry["entry_currency_code"], "KRW")
            self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
            self.assertEqual(detail["detail_currency_code"], "KRW")
        self.assertNotEqual(party_detail[evidence_key], agent_detail[evidence_key])
        self.assertNotEqual(
            party_detail["source_detail_hash"],
            agent_detail["source_detail_hash"],
        )
        self.assertNotEqual(party_entry["source_entry_hash"], agent_entry["source_entry_hash"])

        party_public = dict(party_detail)
        agent_public = dict(agent_detail)
        for projection in (party_public, agent_public):
            projection.pop(evidence_key)
            projection.pop("source_detail_hash")
        self.assertEqual(party_public, agent_public)

        serialized = json.dumps(
            {"party": party_detail, "agent": agent_detail},
            sort_keys=True,
            default=str,
        )
        self.assertNotIn(self.same_scalar, serialized)

    def test_choice_local_whitespace_changes_artifact_only(self) -> None:
        """Layout whitespace inside InitgPty changes bytes but not semantic evidence."""
        baseline_payload = self._with_choice(self._agent_xml(self.same_scalar))
        needle = f"                    <Nm>{self.same_scalar}</Nm>\n".encode("utf-8")
        self.assertEqual(baseline_payload.count(needle), 1)
        formatted_payload = baseline_payload.replace(
            needle,
            b"                    \n" + needle,
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
        baseline_detail = baseline.entries[0].entry_details[0]
        formatted_detail = formatted.entries[0].entry_details[0]

        for value in (
            baseline.source_artifact_hash,
            formatted.source_artifact_hash,
            getattr(baseline_detail, "initiating_party_evidence_hash"),
            getattr(formatted_detail, "initiating_party_evidence_hash"),
            baseline_detail.source_detail_hash,
            formatted_detail.source_detail_hash,
            baseline.entries[0].source_entry_hash,
            formatted.entries[0].source_entry_hash,
            baseline.entries[1].source_entry_hash,
            formatted.entries[1].source_entry_hash,
            baseline.normalized_payload_hash,
            formatted.normalized_payload_hash,
            baseline.account_identifier_hash,
            formatted.account_identifier_hash,
        ):
            self._assert_sha256(value)

        self.assertNotEqual(baseline.source_artifact_hash, formatted.source_artifact_hash)
        self.assertEqual(
            getattr(baseline_detail, "initiating_party_evidence_hash"),
            getattr(formatted_detail, "initiating_party_evidence_hash"),
        )
        self.assertEqual(baseline_detail.source_detail_hash, formatted_detail.source_detail_hash)
        self.assertEqual(
            baseline.entries[0].source_entry_hash,
            formatted.entries[0].source_entry_hash,
        )
        self.assertEqual(baseline.normalized_payload_hash, formatted.normalized_payload_hash)
        self.assertEqual(baseline.account_identifier_hash, formatted.account_identifier_hash)
        self.assertEqual(
            baseline.entries[1].source_entry_hash,
            formatted.entries[1].source_entry_hash,
        )
        for statement, detail in (
            (baseline, baseline_detail),
            (formatted, formatted_detail),
        ):
            self.assertEqual(statement.entries[0].entry_amount, Decimal("25000.00"))
            self.assertEqual(statement.entries[0].entry_currency_code, "KRW")
            self.assertEqual(detail.detail_amount, Decimal("25000.00"))
            self.assertEqual(detail.detail_currency_code, "KRW")

    def _ingest_and_read_first_entry_and_detail(
        self,
        payload: bytes,
        bank_account_reference: str,
        suffix: str,
    ) -> tuple[dict[str, object], dict[str, object]]:
        """Ingest one fixture and return its first buyer entry plus first detail."""
        accepted = initiating.accept_bank_statement_evidence(
            self.helper._command(payload, suffix, bank_account_reference),
            posting.DATABASE_URL,
            self.helper.case.policy.tenant_reference,
            artifact_store=MemoryArtifactStore(),
        )
        document = initiating.lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.helper.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        entry = document["bank_statement_entries"][0]
        return entry, entry["entry_details"][0]

    def _with_choice(self, choice_xml: str) -> bytes:
        """Insert one schema-shaped TransactionParties12/InitgPty choice."""
        marker = (
            "            <RltdPties>\n"
            "              <Dbtr>"
        )
        self.assertEqual(self.fixture.count(marker), 1)
        replacement = (
            "            <RltdPties>\n"
            "              <InitgPty>\n"
            f"{choice_xml}\n"
            "              </InitgPty>\n"
            "              <Dbtr>"
        )
        return self.fixture.replace(marker, replacement, 1).encode("utf-8")

    @staticmethod
    def _party_xml(name: str) -> str:
        """Return the PartyIdentification272 side of Party50Choice."""
        return (
            "                <Pty>\n"
            f"                  <Nm>{name}</Nm>\n"
            "                </Pty>"
        )

    @staticmethod
    def _agent_xml(name: str) -> str:
        """Return the BranchAndFinancialInstitutionIdentification8 side of Party50Choice."""
        return (
            "                <Agt>\n"
            "                  <FinInstnId>\n"
            f"                    <Nm>{name}</Nm>\n"
            "                  </FinInstnId>\n"
            "                </Agt>"
        )

    def _assert_sha256(self, value: object) -> None:
        """Require one canonical SHA-256 evidence identifier."""
        self.assertIsInstance(value, str)
        if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
            raise AssertionError(f"expected canonical sha256 digest, got {value!r}")


if __name__ == "__main__":
    unittest.main()
