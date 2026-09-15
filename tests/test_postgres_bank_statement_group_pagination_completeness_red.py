"""PostgreSQL REDs for complete camt.053 message-pagination evidence."""

from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
    parse_bank_statement_payload,
)
from accounting_information_platform import bank_statement
from tests import test_postgres_posting as posting

_GROUP_PAGINATION_ERROR = (
    r"^GrpHdr/MsgPgntn does not represent a complete standalone message\. "
    r"Assemble every message page under a versioned pagination contract, then retry ingest\.$"
)


class BankStatementGroupPaginationCompletenessRedTests(unittest.TestCase):
    """Do not admit one incomplete message page as complete reconciliation evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare canonical and incomplete GroupHeader pagination variants."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        self.baseline_payload = load_canonical_statement_fixture()
        baseline = parse_bank_statement_payload(
            self.baseline_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.first_nonfinal_payload = self._with_group_pagination("1", "false")
        self.second_final_payload = self._with_group_pagination("2", "true")

        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": baseline.account_currency_code,
                "account_identifier_hash": baseline.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def _command(self, payload: bytes, suffix: str) -> dict[str, str]:
        """Build one supported ingest command with a fresh replay identity."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"group-pagination-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

    def test_incomplete_message_pages_fail_closed_before_statement_normalization(self) -> None:
        """Neither a non-final first page nor an isolated later final page is complete."""
        for suffix, payload in (
            ("first-nonfinal", self.first_nonfinal_payload),
            ("second-final", self.second_final_payload),
        ):
            with self.subTest(suffix=suffix):
                with patch.object(
                    bank_statement,
                    "_normalize_statement",
                    side_effect=AssertionError(
                        "statement normalization ran before group-pagination completeness admission"
                    ),
                ):
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        _GROUP_PAGINATION_ERROR,
                    ):
                        parse_bank_statement_payload(
                            payload,
                            CAMT053_MESSAGE_DEFINITION,
                        )

    def test_supported_ingest_rejects_incomplete_message_pages_before_artifact_retention(self) -> None:
        """An incomplete message page cannot become durable accepted bank-statement evidence."""
        for suffix, payload in (
            ("first-nonfinal", self.first_nonfinal_payload),
            ("second-final", self.second_final_payload),
        ):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    _GROUP_PAGINATION_ERROR,
                ):
                    accept_bank_statement_evidence(
                        self._command(payload, suffix),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )
                self.assertEqual(
                    self.store._artifacts,
                    {},
                    "incomplete message-page bytes must not be retained as accepted evidence",
                )

    def test_message_without_group_pagination_remains_supported(self) -> None:
        """The completeness boundary must not reject the canonical unpaginated profile."""
        statement = parse_bank_statement_payload(
            self.baseline_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.assertEqual(
            statement.message_definition_identifier,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.assertTrue(statement.entries)

    def _with_group_pagination(self, page_number: str, last_page: str) -> bytes:
        """Insert one schema-shaped GroupHeader MessagePagination block."""
        fixture = self.baseline_payload.decode("utf-8")
        marker = (
            "      <CreDtTm>2026-08-24T09:00:00+00:00</CreDtTm>\n"
            "    </GrpHdr>"
        )
        if fixture.count(marker) != 1:
            raise AssertionError("canonical fixture must contain exactly one GroupHeader terminator")
        return fixture.replace(
            marker,
            "      <CreDtTm>2026-08-24T09:00:00+00:00</CreDtTm>\n"
            "      <MsgPgntn>\n"
            f"        <PgNb>{page_number}</PgNb>\n"
            f"        <LastPgInd>{last_page}</LastPgInd>\n"
            "      </MsgPgntn>\n"
            "    </GrpHdr>",
            1,
        ).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
