"""PostgreSQL REDs for remaining camt.053 transaction-agent role evidence."""

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


class BankStatementPaymentAgentRoleEvidenceRedTests(unittest.TestCase):
    """Retain non-counterparty payment-agent roles at transaction-detail granularity."""

    CASES = {
        "InstgAgt": ("instructing_agent_evidence_hash", "DEUTDEFF", "COBADEFF"),
        "InstdAgt": ("instructed_agent_evidence_hash", "BNPAFRPP", "SOGEFRPP"),
        "RcvgAgt": ("receiving_agent_evidence_hash", "CHASUS33", "BOFAUS3N"),
        "DlvrgAgt": ("delivering_agent_evidence_hash", "CITIUS33", "WFBIUS6S"),
        "IssgAgt": ("issuing_agent_evidence_hash", "PNCCUS33", "NWBKGB2L"),
        "SttlmPlc": ("settlement_place_evidence_hash", "BARCGB22", "DABADKKK"),
    }

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Load the unique outgoing-payment detail used for role-specific agent evidence."""
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

    def test_each_payment_agent_role_changes_detail_entry_and_statement_hashes(self) -> None:
        """Changing exactly one role-specific BICFI changes every retained evidence identity."""
        for element_name, (_, first_bicfi, second_bicfi) in self.CASES.items():
            with self.subTest(element_name=element_name):
                first = self._statement(self._with_agent(element_name, first_bicfi))
                second = self._statement(self._with_agent(element_name, second_bicfi))
                first_detail = first.entries[1].entry_details[0]
                second_detail = second.entries[1].entry_details[0]

                self.assertNotEqual(first_detail.source_detail_hash, second_detail.source_detail_hash)
                self.assertNotEqual(
                    first.entries[1].source_entry_hash,
                    second.entries[1].source_entry_hash,
                )
                self.assertNotEqual(first.normalized_payload_hash, second.normalized_payload_hash)

    def test_each_payment_agent_role_reaches_statement_correction_boundary(self) -> None:
        """Changed role-specific agent evidence cannot replay as the same statement evidence."""
        for element_name, (_, first_bicfi, second_bicfi) in self.CASES.items():
            with self.subTest(element_name=element_name):
                first_payload = self._with_agent(element_name, first_bicfi)
                second_payload = self._with_agent(element_name, second_bicfi)
                statement = self._statement(first_payload)
                account_reference = self._register_account(
                    statement,
                    f"correction-{element_name.lower()}",
                )
                store = MemoryArtifactStore()

                accept_bank_statement_evidence(
                    self._command(first_payload, account_reference, f"first-{element_name}"),
                    posting.DATABASE_URL,
                    self.case.policy.tenant_reference,
                    artifact_store=store,
                )
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    r"statement identity already exists with different entry evidence",
                ):
                    accept_bank_statement_evidence(
                        self._command(second_payload, account_reference, f"second-{element_name}"),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=store,
                    )

    def test_payment_agent_role_readback_is_digest_only_and_role_specific(self) -> None:
        """Buyer reads expose only one purpose-bound digest for each reported agent role."""
        for element_name, (digest_key, first_bicfi, _) in self.CASES.items():
            with self.subTest(element_name=element_name):
                private_payload = self._with_agent(element_name, first_bicfi)
                private_statement = self._statement(private_payload)
                private_account = self._register_account(
                    private_statement,
                    f"private-{element_name.lower()}",
                )
                private_detail = self._ingest_and_read_target_detail(
                    private_payload,
                    private_account,
                    f"private-{element_name}",
                )

                baseline_payload = load_canonical_statement_fixture()
                baseline_statement = self._statement(baseline_payload)
                baseline_account = self._register_account(
                    baseline_statement,
                    f"baseline-{element_name.lower()}",
                )
                baseline_detail = self._ingest_and_read_target_detail(
                    baseline_payload,
                    baseline_account,
                    f"baseline-{element_name}",
                )

                expected_hash = "sha256:" + hashlib.sha256(first_bicfi.encode("utf-8")).hexdigest()
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

    def _with_agent(self, element_name: str, bicfi: str) -> bytes:
        """Insert exactly one role-specific financial institution into the debit detail."""
        if element_name not in self.CASES:
            raise AssertionError(f"unsupported transaction-agent role: {element_name}")
        replacement = (
            "            <AmtDtls>\n"
            "              <TxAmt>\n"
            "                <Amt Ccy=\"KRW\">6000.00</Amt>\n"
            "              </TxAmt>\n"
            "            </AmtDtls>\n"
            "            <RltdAgts>\n"
            f"              <{element_name}>\n"
            "                <FinInstnId>\n"
            f"                  <BICFI>{bicfi}</BICFI>\n"
            "                </FinInstnId>\n"
            f"              </{element_name}>\n"
            "            </RltdAgts>\n"
            "            <RmtInf>"
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
            "ingestion_idempotency_key": f"payment-agent-role-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
