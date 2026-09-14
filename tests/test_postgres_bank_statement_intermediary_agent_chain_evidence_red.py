"""PostgreSQL REDs for camt.053 intermediary-agent chain evidence preservation."""

from __future__ import annotations

import hashlib
import unittest
import uuid

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


class BankStatementIntermediaryAgentChainEvidenceRedTests(unittest.TestCase):
    """Retain reported second and third intermediary agents without collapsing chain position."""

    CASES = {
        2: ("BOFAUS3N", "CITIUS33"),
        3: ("WFBIUS6S", "PNCCUS33"),
    }

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Load the unique outgoing-payment detail used for intermediary-chain evidence."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.marker = (
            "            <AmtDtls>\n"
            "              <TxAmt>\n"
            "                <Amt Ccy=\"KRW\">6000.00</Amt>\n"
            "              </TxAmt>\n"
            "            </AmtDtls>\n"
            "            <RmtInf>"
        )
        self.assertEqual(self.fixture.count(self.marker), 1)

    def test_each_downstream_intermediary_changes_detail_entry_and_statement_hashes(self) -> None:
        """Changing only intermediary-agent 2 or 3 changes every retained evidence identity."""
        for slot, (first_bicfi, second_bicfi) in self.CASES.items():
            with self.subTest(slot=slot):
                first = self._statement(self._with_intermediary_chain(slot, first_bicfi))
                second = self._statement(self._with_intermediary_chain(slot, second_bicfi))
                first_detail = first.entries[1].entry_details[0]
                second_detail = second.entries[1].entry_details[0]

                self.assertNotEqual(first_detail.source_detail_hash, second_detail.source_detail_hash)
                self.assertNotEqual(
                    first.entries[1].source_entry_hash,
                    second.entries[1].source_entry_hash,
                )
                self.assertNotEqual(first.normalized_payload_hash, second.normalized_payload_hash)

    def test_each_downstream_intermediary_reaches_statement_correction_boundary(self) -> None:
        """Changed intermediary-agent 2 or 3 cannot replay as the same statement evidence."""
        for slot, (first_bicfi, second_bicfi) in self.CASES.items():
            with self.subTest(slot=slot):
                first_payload = self._with_intermediary_chain(slot, first_bicfi)
                second_payload = self._with_intermediary_chain(slot, second_bicfi)
                statement = self._statement(first_payload)
                account_reference = self._register_account(statement, f"correction-{slot}")
                store = MemoryArtifactStore()

                accept_bank_statement_evidence(
                    self._command(first_payload, account_reference, f"first-{slot}"),
                    posting.DATABASE_URL,
                    self.case.policy.tenant_reference,
                    artifact_store=store,
                )
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    r"statement identity already exists with different entry evidence",
                ):
                    accept_bank_statement_evidence(
                        self._command(second_payload, account_reference, f"second-{slot}"),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=store,
                    )

    def test_downstream_intermediary_readback_is_digest_only_and_position_specific(self) -> None:
        """Buyer reads expose only the purpose-bound digest for intermediary positions 2 and 3."""
        for slot, (first_bicfi, _) in self.CASES.items():
            with self.subTest(slot=slot):
                private_payload = self._with_intermediary_chain(slot, first_bicfi)
                private_statement = self._statement(private_payload)
                private_account = self._register_account(
                    private_statement,
                    f"private-{slot}",
                )
                private_detail = self._ingest_and_read_target_detail(
                    private_payload,
                    private_account,
                    f"private-{slot}",
                )

                baseline_payload = load_canonical_statement_fixture()
                baseline_statement = self._statement(baseline_payload)
                baseline_account = self._register_account(
                    baseline_statement,
                    f"baseline-{slot}",
                )
                baseline_detail = self._ingest_and_read_target_detail(
                    baseline_payload,
                    baseline_account,
                    f"baseline-{slot}",
                )

                digest_key = f"intermediary_agent_{slot}_evidence_hash"
                expected_hash = "sha256:" + hashlib.sha256(
                    first_bicfi.encode("utf-8")
                ).hexdigest()
                self.assertEqual(private_detail[digest_key], expected_hash)
                for projection in (private_detail, baseline_detail):
                    source_detail_hash = projection["source_detail_hash"]
                    self.assertIsInstance(source_detail_hash, str)
                    self.assertRegex(source_detail_hash, r"\Asha256:[0-9a-f]{64}\Z")

                actual_projection = dict(private_detail)
                baseline_projection = dict(baseline_detail)
                actual_projection.pop(digest_key)
                baseline_projection.pop(digest_key, None)
                actual_projection.pop("source_detail_hash")
                baseline_projection.pop("source_detail_hash")
                self.assertEqual(actual_projection, baseline_projection)

    def _with_intermediary_chain(self, slot: int, variable_bicfi: str) -> bytes:
        """Insert a realistic ordered intermediary chain and vary exactly one downstream agent."""
        if slot not in self.CASES:
            raise AssertionError(f"unsupported intermediary slot: {slot}")
        agents = [(1, "CHASUS33")]
        if slot == 2:
            agents.append((2, variable_bicfi))
        else:
            agents.extend([(2, "BOFAUS3N"), (3, variable_bicfi)])
        related_agents = ["            <RltdAgts>"]
        for agent_slot, bicfi in agents:
            related_agents.extend(
                [
                    f"              <IntrmyAgt{agent_slot}>",
                    "                <FinInstnId>",
                    f"                  <BICFI>{bicfi}</BICFI>",
                    "                </FinInstnId>",
                    f"              </IntrmyAgt{agent_slot}>",
                ]
            )
        related_agents.append("            </RltdAgts>")
        replacement = (
            "            <AmtDtls>\n"
            "              <TxAmt>\n"
            "                <Amt Ccy=\"KRW\">6000.00</Amt>\n"
            "              </TxAmt>\n"
            "            </AmtDtls>\n"
            + "\n".join(related_agents)
            + "\n            <RmtInf>"
        )
        return self.fixture.replace(self.marker, replacement, 1).encode("utf-8")

    @staticmethod
    def _statement(payload: bytes):
        """Parse one source-real camt.053 payload through the supported adapter."""
        return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    def _register_account(self, statement, suffix: str) -> str:
        """Register one isolated buyer account for a statement-evidence scenario."""
        reference = f"urn:cwl:bank_account:{suffix}:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": reference,
                "account_currency_code": statement.account_currency_code,
                "account_identifier_hash": statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        return reference

    def _ingest_and_read_target_detail(
        self,
        payload: bytes,
        account_reference: str,
        suffix: str,
    ) -> dict[str, object]:
        """Ingest one fixture and return the first detail of its debit entry."""
        accepted = accept_bank_statement_evidence(
            self._command(payload, account_reference, suffix),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=MemoryArtifactStore(),
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        return document["bank_statement_entries"][1]["entry_details"][0]

    def _command(
        self,
        payload: bytes,
        account_reference: str,
        suffix: str,
    ) -> dict[str, object]:
        """Return one supported ingest command with an isolated replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": account_reference,
            "ingestion_idempotency_key": (
                f"intermediary-chain-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
