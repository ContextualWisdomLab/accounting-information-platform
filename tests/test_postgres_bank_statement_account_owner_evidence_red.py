"""PostgreSQL REDs for camt.053 statement-account owner evidence."""

from __future__ import annotations

import hashlib
import json
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
from accounting_information_platform import bank_statement
from tests import test_postgres_posting as posting

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_ACCOUNT_OWNER_EVIDENCE_PURPOSE = "camt.053.001.14/Stmt/Acct/Ownr"


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
        formatting_anchor = (
            f"        <Ownr>\n          <Nm>{self.first_owner_name}</Nm>\n"
        ).encode("utf-8")
        self.assertEqual(self.first_payload.count(formatting_anchor), 1)
        self.reformatted_first_payload = self.first_payload.replace(
            formatting_anchor,
            (
                "        <Ownr>\n          \n"
                f"          <Nm>{self.first_owner_name}</Nm>\n"
            ).encode("utf-8"),
            1,
        )

        self.first_statement = parse_bank_statement_payload(
            self.first_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.reformatted_first_statement = parse_bank_statement_payload(
            self.reformatted_first_payload,
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
        """A changed reported owner changes owner/statement evidence, not Acct/Id identity."""
        self._assert_owner_evidence_difference(
            self.first_statement,
            self.second_statement,
            left_name=self.first_owner_name,
            right_name=self.second_owner_name,
        )

    def test_source_formatting_cannot_change_semantically_equal_owner_evidence(self) -> None:
        """Insignificant XML formatting must not leak into normalized owner evidence identity."""
        expected_hash = self._expected_account_owner_hash(self.first_owner_name)

        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.reformatted_first_statement.source_artifact_hash,
        )
        self.assertEqual(
            self.first_statement.account_identifier_hash,
            self.reformatted_first_statement.account_identifier_hash,
        )
        self.assertEqual(
            getattr(self.first_statement, "account_owner_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(self.reformatted_first_statement, "account_owner_evidence_hash", None),
            expected_hash,
        )
        self._assert_normalized_hash_binding(self.first_statement, expected_hash)
        self._assert_normalized_hash_binding(
            self.reformatted_first_statement,
            expected_hash,
        )
        self.assertEqual(
            self.first_statement.normalized_payload_hash,
            self.reformatted_first_statement.normalized_payload_hash,
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

    def test_statement_lookup_exposes_same_purpose_bound_account_owner_hash(self) -> None:
        """Buyer reads expose the exact owner-evidence digest admitted during normalization."""
        expected_hash = self._expected_account_owner_hash(self.first_owner_name)
        owner_hash = getattr(self.first_statement, "account_owner_evidence_hash", None)
        self.assertEqual(owner_hash, expected_hash)

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

        self.assertEqual(document.get("account_owner_evidence_hash"), expected_hash)

    def _assert_owner_evidence_difference(
        self,
        left: object,
        right: object,
        *,
        left_name: str,
        right_name: str,
    ) -> None:
        """Bind owner and statement digests to canonical parsed Acct/Ownr evidence."""
        left_hash = getattr(left, "account_owner_evidence_hash", None)
        right_hash = getattr(right, "account_owner_evidence_hash", None)
        expected_left = self._expected_account_owner_hash(left_name)
        expected_right = self._expected_account_owner_hash(right_name)

        self.assertNotEqual(left.source_artifact_hash, right.source_artifact_hash)
        self.assertEqual(left.account_identifier_hash, right.account_identifier_hash)
        self.assertRegex(expected_left, _HASH_PATTERN)
        self.assertRegex(expected_right, _HASH_PATTERN)
        self.assertEqual(left_hash, expected_left)
        self.assertEqual(right_hash, expected_right)
        self.assertNotEqual(expected_left, expected_right)
        self._assert_normalized_hash_binding(left, expected_left)
        self._assert_normalized_hash_binding(right, expected_right)
        self.assertNotEqual(left.normalized_payload_hash, right.normalized_payload_hash)

    @staticmethod
    def _assert_normalized_hash_binding(statement: object, owner_hash: str) -> None:
        """Bind the statement hash to owner evidence while rejecting raw-source coupling."""
        projection = dict(bank_statement._normalized_payload(statement))
        if projection.get("account_owner_evidence_hash") != owner_hash:
            raise AssertionError(
                "canonical normalized statement projection must carry the exact "
                "account_owner_evidence_hash"
            )
        if "source_artifact_hash" in projection:
            raise AssertionError(
                "canonical normalized statement projection must not carry raw artifact identity"
            )
        preimage = json.dumps(
            projection,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        expected_statement_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if statement.normalized_payload_hash != expected_statement_hash:
            raise AssertionError(
                "normalized_payload_hash must be the digest of the canonical normalized projection"
            )

    @staticmethod
    def _expected_account_owner_hash(owner_name: str) -> str:
        """Return the purpose-bound digest of only the admitted Acct/Ownr semantics."""
        preimage = json.dumps(
            {
                "evidence_type": _ACCOUNT_OWNER_EVIDENCE_PURPOSE,
                "name": owner_name,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

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
