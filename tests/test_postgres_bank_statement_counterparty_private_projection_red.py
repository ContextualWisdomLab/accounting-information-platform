"""PostgreSQL REDs for purpose-bound counterparty buyer projections."""

from __future__ import annotations

import hashlib
import unittest
import uuid

from accounting_information_platform import (
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
    lookup_bank_statement_entries,
)
from tests import test_postgres_bank_statement_counterparty_account_evidence_red as account_red
from tests import test_postgres_bank_statement_counterparty_agent_evidence_red as agent_red
from tests import test_postgres_posting as posting


class BankStatementCounterpartyPrivateProjectionRedTests(unittest.TestCase):
    """Reject buyer fields derived from private counterparty evidence beyond its digest."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def test_debtor_account_projection_differs_from_baseline_only_by_digest(self) -> None:
        """A debtor-account source fact may add only its digest and detail identity."""
        provider = account_red.BankStatementCounterpartyAccountEvidenceRedTests(
            "test_entry_lookup_preserves_debtor_account_evidence_hash"
        )
        provider.setUp()
        self.addCleanup(provider.doCleanups)

        party_detail = self._ingest_and_read_first_detail(
            provider,
            provider.first_payload,
            provider.bank_account_reference,
            "private-projection",
        )
        baseline_detail = self._baseline_detail(provider, "account-baseline")
        expected_hash = "sha256:" + hashlib.sha256(
            provider.first_debtor_account_iban.encode("utf-8")
        ).hexdigest()
        self._assert_digest_only_projection_delta(
            party_detail,
            baseline_detail,
            "debtor_account_evidence_hash",
            expected_hash,
        )

    def test_debtor_agent_projection_differs_from_baseline_only_by_digest(self) -> None:
        """A debtor-agent source fact may add only its digest and detail identity."""
        provider = agent_red.BankStatementCounterpartyAgentEvidenceRedTests(
            "test_entry_lookup_preserves_debtor_agent_evidence_hash"
        )
        provider.setUp()
        self.addCleanup(provider.doCleanups)

        party_detail = self._ingest_and_read_first_detail(
            provider,
            provider.first_payload,
            provider.bank_account_reference,
            "private-projection",
        )
        baseline_detail = self._baseline_detail(provider, "agent-baseline")
        expected_hash = "sha256:" + hashlib.sha256(
            provider.first_debtor_agent_bicfi.encode("utf-8")
        ).hexdigest()
        self._assert_digest_only_projection_delta(
            party_detail,
            baseline_detail,
            "debtor_agent_evidence_hash",
            expected_hash,
        )

    def _baseline_detail(self, provider: unittest.TestCase, suffix: str) -> dict[str, object]:
        """Read the canonical no-counterparty-detail projection on an isolated account."""
        baseline_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        first_statement = provider.first_statement
        accept_bank_account_record(
            {
                "tenant_reference": provider.case.policy.tenant_reference,
                "bank_account_reference": baseline_account_reference,
                "account_currency_code": first_statement.account_currency_code,
                "account_identifier_hash": first_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            provider.case.policy.tenant_reference,
        )
        return self._ingest_and_read_first_detail(
            provider,
            load_canonical_statement_fixture(),
            baseline_account_reference,
            suffix,
        )

    @staticmethod
    def _ingest_and_read_first_detail(
        provider: unittest.TestCase,
        payload: bytes,
        bank_account_reference: str,
        suffix: str,
    ) -> dict[str, object]:
        """Ingest one fixture and return its first supported detail projection."""
        accepted = accept_bank_statement_evidence(
            {
                "tenant_reference": provider.case.policy.tenant_reference,
                "bank_account_reference": bank_account_reference,
                "ingestion_idempotency_key": f"counterparty-private-{suffix}-{uuid.uuid4().hex}",
                "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                "statement_payload": payload.decode("utf-8"),
            },
            posting.DATABASE_URL,
            provider.case.policy.tenant_reference,
            artifact_store=MemoryArtifactStore(),
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            provider.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        return document["bank_statement_entries"][0]["entry_details"][0]

    def _assert_digest_only_projection_delta(
        self,
        detail: dict[str, object],
        baseline_detail: dict[str, object],
        evidence_key: str,
        expected_hash: str,
    ) -> None:
        """Require private-source buyer output to add no reversible projection field."""
        self.assertEqual(detail[evidence_key], expected_hash)
        actual_projection = dict(detail)
        baseline_projection = dict(baseline_detail)
        actual_projection.pop(evidence_key)
        baseline_projection.pop(evidence_key, None)
        actual_projection.pop("source_detail_hash")
        baseline_projection.pop("source_detail_hash")
        self.assertEqual(actual_projection, baseline_projection)


if __name__ == "__main__":
    unittest.main()
