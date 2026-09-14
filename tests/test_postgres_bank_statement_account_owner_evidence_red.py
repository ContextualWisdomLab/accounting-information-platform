"""PostgreSQL REDs for camt.053 statement-account owner evidence."""

from __future__ import annotations

import re
import unittest
import uuid

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
    lookup_bank_statement,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class BankStatementAccountOwnerEvidenceRedTests(unittest.TestCase):
    """Retain Acct/Ownr as source evidence without redefining the account identifier."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare two schema-shaped statements differing only in Acct/Ownr/Nm."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "        <Ccy>KRW</Ccy>\n      </Acct>"
        self.assertEqual(fixture.count(marker), 1)
        self.first_owner_name = "CWL Treasury Owner A"
        self.second_owner_name = "CWL Treasury Owner B"
        self.first_payload = self._with_account_owner(
            fixture,
            marker,
            self.first_owner_name,
        )
        self.second_payload = self._with_account_owner(
            fixture,
            marker,
            self.second_owner_name,
        )
        self.first_statement = parse_bank_statement_payload(
            self.first_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.second_statement = parse_bank_statement_payload(
            self.second_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.first_statement.account_currency_code,
                "account_identifier_hash": self.first_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_account_owner_is_material_statement_evidence_not_account_identifier_identity(self) -> None:
        """A changed reported owner changes statement evidence without changing Acct/Id identity."""
        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.second_statement.source_artifact_hash,
        )
        self.assertEqual(
            self.first_statement.account_identifier_hash,
            self.second_statement.account_identifier_hash,
        )
        self.assertNotEqual(
            self.first_statement.normalized_payload_hash,
            self.second_statement.normalized_payload_hash,
        )

    def test_changed_account_owner_requires_statement_correction(self) -> None:
        """The same statement identity cannot silently replay changed account-owner evidence."""
        first = accept_bank_statement_evidence(
            self._command(self.first_payload, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(first["replayed"])

        with self.assertRaisesRegex(
            AccountingValidationError,
            (
                r"^statement identity already exists with different entry evidence\. "
                r"Use an explicit correction contract, then retry ingest\.$"
            ),
        ):
            accept_bank_statement_evidence(
                self._command(self.second_payload, "second"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_statement_lookup_exposes_purpose_bound_account_owner_evidence_hash(self) -> None:
        """Buyer reads expose a digest of present Acct/Ownr evidence, not an untracked omission."""
        accepted = accept_bank_statement_evidence(
            self._command(self.first_payload, "lookup"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        document = lookup_bank_statement(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )

        owner_hash = document.get("account_owner_evidence_hash")
        self.assertIsInstance(owner_hash, str)
        self.assertRegex(owner_hash or "", _HASH_PATTERN)

    @staticmethod
    def _with_account_owner(fixture: str, marker: str, owner_name: str) -> bytes:
        """Insert one lawful CashAccount43 Ownr/PartyIdentification272 name after Ccy."""
        return fixture.replace(
            marker,
            "        <Ccy>KRW</Ccy>\n"
            "        <Ownr>\n"
            f"          <Nm>{owner_name}</Nm>\n"
            "        </Ownr>\n"
            "      </Acct>",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"account-owner-evidence-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
