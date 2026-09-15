"""PostgreSQL REDs for camt.053 statement-account name evidence."""

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
_ACCOUNT_NAME_EVIDENCE_PURPOSE = "camt.053.001.14/Stmt/Acct/Nm"


class BankStatementAccountNameEvidenceRedTests(unittest.TestCase):
    """Retain Acct/Nm as bank-reported evidence without redefining Acct/Id identity."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare lawful statements differing only in Acct/Nm evidence."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "        <Ccy>KRW</Ccy>\n      </Acct>"
        self.assertEqual(fixture.count(marker), 1)
        self.first_name = "CWL Treasury Operating"
        self.second_name = "CWL Treasury Reserve"
        self.first_payload = self._with_account_name(fixture, marker, self.first_name)
        self.second_payload = self._with_account_name(fixture, marker, self.second_name)

        formatting_anchor = (
            f"        <Nm>{self.first_name}</Nm>\n      </Acct>"
        ).encode("utf-8")
        self.assertEqual(self.first_payload.count(formatting_anchor), 1)
        self.reformatted_first_payload = self.first_payload.replace(
            formatting_anchor,
            (
                f"        <Nm>{self.first_name}</Nm>\n"
                "        \n"
                "      </Acct>"
            ).encode("utf-8"),
            1,
        )

        self.first_statement = parse_bank_statement_payload(
            self.first_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.second_statement = parse_bank_statement_payload(
            self.second_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.reformatted_first_statement = parse_bank_statement_payload(
            self.reformatted_first_payload,
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

    def test_account_name_is_material_statement_evidence_not_account_identifier_identity(self) -> None:
        """A changed account name changes name/statement evidence, not Acct/Id identity."""
        self._assert_name_evidence_difference(
            self.first_statement,
            self.second_statement,
            left_name=self.first_name,
            right_name=self.second_name,
        )

    def test_source_formatting_cannot_change_semantically_equal_account_name_evidence(self) -> None:
        """Insignificant XML formatting must not leak into normalized account-name identity."""
        expected_hash = self._expected_account_name_hash(self.first_name)

        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.reformatted_first_statement.source_artifact_hash,
        )
        self.assertEqual(
            self.first_statement.account_identifier_hash,
            self.reformatted_first_statement.account_identifier_hash,
        )
        self.assertEqual(
            getattr(self.first_statement, "account_name_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(self.reformatted_first_statement, "account_name_evidence_hash", None),
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

    def test_changed_account_name_requires_statement_correction(self) -> None:
        """The same statement identity cannot silently replay changed account-name evidence."""
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

    def test_statement_lookup_exposes_same_purpose_bound_account_name_hash(self) -> None:
        """Buyer reads expose the exact account-name digest admitted during normalization."""
        expected_hash = self._expected_account_name_hash(self.first_name)
        self.assertEqual(
            getattr(self.first_statement, "account_name_evidence_hash", None),
            expected_hash,
        )

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

        self.assertEqual(document.get("account_name_evidence_hash"), expected_hash)

    def _assert_name_evidence_difference(
        self,
        left: object,
        right: object,
        *,
        left_name: str,
        right_name: str,
    ) -> None:
        """Bind name and statement digests to canonical parsed Acct/Nm evidence."""
        expected_left = self._expected_account_name_hash(left_name)
        expected_right = self._expected_account_name_hash(right_name)
        left_hash = getattr(left, "account_name_evidence_hash", None)
        right_hash = getattr(right, "account_name_evidence_hash", None)

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
    def _assert_normalized_hash_binding(statement: object, account_name_hash: str) -> None:
        """Bind statement identity to semantic name evidence, never raw artifact bytes."""
        projection = dict(bank_statement._normalized_payload(statement))
        if projection.get("account_name_evidence_hash") != account_name_hash:
            raise AssertionError(
                "canonical normalized statement projection must carry the exact "
                "account_name_evidence_hash"
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
    def _expected_account_name_hash(account_name: str) -> str:
        """Return the purpose-bound digest of only the admitted Acct/Nm semantics."""
        preimage = json.dumps(
            {
                "evidence_type": _ACCOUNT_NAME_EVIDENCE_PURPOSE,
                "name": account_name,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _with_account_name(fixture: str, marker: str, account_name: str) -> bytes:
        """Insert one lawful CashAccount43 Nm after Ccy."""
        return fixture.replace(
            marker,
            "        <Ccy>KRW</Ccy>\n"
            f"        <Nm>{account_name}</Nm>\n"
            "      </Acct>",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"account-name-evidence-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
