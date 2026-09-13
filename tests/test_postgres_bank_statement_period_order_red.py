"""PostgreSQL REDs for coherent camt.053 statement reporting periods."""

from __future__ import annotations

import unittest
import uuid

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting


class BankStatementPeriodOrderRedTests(unittest.TestCase):
    """Reject source statement periods whose end precedes their start."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one lawful account plus source-real reporting-period fixtures."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.start_marker = "<FrDtTm>2026-08-23T00:00:00+00:00</FrDtTm>"
        self.end_marker = "<ToDtTm>2026-08-24T23:59:59+00:00</ToDtTm>"
        self.assertEqual(self.fixture.count(self.start_marker), 1)
        self.assertEqual(self.fixture.count(self.end_marker), 1)

        base_statement = parse_bank_statement_payload(
            self.fixture.encode("utf-8"),
            CAMT053_MESSAGE_DEFINITION,
        )
        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": base_statement.account_currency_code,
                "account_identifier_hash": base_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_adapter_rejects_period_end_before_start(self) -> None:
        """Fail closed when the reported period end is earlier than its start."""
        with self.assertRaises(AccountingValidationError):
            parse_bank_statement_payload(
                self._payload(
                    start="2026-08-25T00:00:00+00:00",
                    end="2026-08-24T23:59:59+00:00",
                ),
                CAMT053_MESSAGE_DEFINITION,
            )

    def test_supported_ingest_rejects_period_end_before_start(self) -> None:
        """Do not retain a source statement with a reversed reporting period."""
        payload = self._payload(
            start="2026-08-25T00:00:00+00:00",
            end="2026-08-24T23:59:59+00:00",
        )
        with self.assertRaises(AccountingValidationError):
            accept_bank_statement_evidence(
                {
                    "tenant_reference": self.case.policy.tenant_reference,
                    "bank_account_reference": self.bank_account_reference,
                    "ingestion_idempotency_key": f"period-order-{uuid.uuid4().hex}",
                    "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                    "statement_payload": payload.decode("utf-8"),
                },
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_period_order_uses_absolute_instants_across_offsets(self) -> None:
        """Compare offset-aware instants rather than local clock text."""
        statement = parse_bank_statement_payload(
            self._payload(
                start="2026-08-24T23:00:00+09:00",
                end="2026-08-24T08:00:00-07:00",
            ),
            CAMT053_MESSAGE_DEFINITION,
        )
        self.assertIsNotNone(statement.period_start_at)
        self.assertIsNotNone(statement.period_end_at)
        assert statement.period_start_at is not None
        assert statement.period_end_at is not None
        self.assertLess(statement.period_start_at, statement.period_end_at)

    def _payload(self, *, start: str, end: str) -> bytes:
        """Replace only the canonical reporting-period boundaries."""
        return (
            self.fixture.replace(
                self.start_marker,
                f"<FrDtTm>{start}</FrDtTm>",
                1,
            )
            .replace(
                self.end_marker,
                f"<ToDtTm>{end}</ToDtTm>",
                1,
            )
            .encode("utf-8")
        )


if __name__ == "__main__":
    unittest.main()
