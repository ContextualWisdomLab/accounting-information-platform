"""PostgreSQL REDs for camt.053 statement-account type evidence."""

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
from tests import test_postgres_posting as posting

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_ACCOUNT_TYPE_EVIDENCE_PURPOSE = "camt.053.001.14/Stmt/Acct/Tp"


class BankStatementAccountTypeEvidenceRedTests(unittest.TestCase):
    """Retain Acct/Tp evidence without redefining Acct/Id identity."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare lawful statements differing only in the account-type choice."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "        <Ccy>KRW</Ccy>\n      </Acct>"
        self.assertEqual(fixture.count(marker), 1)
        self.code_payload = self._with_account_type(
            fixture,
            marker,
            "          <Cd>CACC</Cd>",
        )
        self.proprietary_payload = self._with_account_type(
            fixture,
            marker,
            "          <Prtry>CACC</Prtry>",
        )
        self.savings_payload = self._with_account_type(
            fixture,
            marker,
            "          <Cd>SVGS</Cd>",
        )
        self.code_statement = parse_bank_statement_payload(
            self.code_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.proprietary_statement = parse_bank_statement_payload(
            self.proprietary_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.savings_statement = parse_bank_statement_payload(
            self.savings_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.code_statement.account_currency_code,
                "account_identifier_hash": self.code_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_account_type_value_is_material_statement_evidence_not_account_identity(self) -> None:
        """A changed coded account type changes type/statement evidence, not Acct/Id."""
        self._assert_type_evidence_difference(
            self.code_statement,
            self.savings_statement,
            left_choice="Cd",
            left_value="CACC",
            right_choice="Cd",
            right_value="SVGS",
        )

    def test_account_type_choice_discriminator_is_material_even_when_text_matches(self) -> None:
        """Coded and proprietary account types with equal text cannot provenance-alias."""
        self._assert_type_evidence_difference(
            self.code_statement,
            self.proprietary_statement,
            left_choice="Cd",
            left_value="CACC",
            right_choice="Prtry",
            right_value="CACC",
        )

    def test_changed_account_type_requires_statement_correction(self) -> None:
        """The same statement identity cannot silently replay changed account-type evidence."""
        first = accept_bank_statement_evidence(
            self._command(self.code_payload, "first"),
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
                self._command(self.savings_payload, "second"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_statement_lookup_exposes_same_purpose_bound_account_type_hash(self) -> None:
        """Buyer reads expose the exact type-evidence digest admitted during normalization."""
        type_hash = getattr(self.code_statement, "account_type_evidence_hash", None)
        expected_hash = self._expected_account_type_hash("Cd", "CACC")
        self.assertEqual(type_hash, expected_hash)

        accepted = accept_bank_statement_evidence(
            self._command(self.code_payload, "lookup"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        document = lookup_bank_statement(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )

        self.assertEqual(document.get("account_type_evidence_hash"), expected_hash)

    def _assert_type_evidence_difference(
        self,
        left: object,
        right: object,
        *,
        left_choice: str,
        left_value: str,
        right_choice: str,
        right_value: str,
    ) -> None:
        """Bind each digest to the canonical account-type choice and value."""
        left_hash = getattr(left, "account_type_evidence_hash", None)
        right_hash = getattr(right, "account_type_evidence_hash", None)
        expected_left = self._expected_account_type_hash(left_choice, left_value)
        expected_right = self._expected_account_type_hash(right_choice, right_value)

        self.assertNotEqual(left.source_artifact_hash, right.source_artifact_hash)
        self.assertEqual(left.account_identifier_hash, right.account_identifier_hash)
        self.assertRegex(expected_left, _HASH_PATTERN)
        self.assertRegex(expected_right, _HASH_PATTERN)
        self.assertEqual(left_hash, expected_left)
        self.assertEqual(right_hash, expected_right)
        self.assertNotEqual(expected_left, expected_right)
        self.assertNotEqual(left.normalized_payload_hash, right.normalized_payload_hash)

    @staticmethod
    def _expected_account_type_hash(choice: str, value: str) -> str:
        """Return the purpose-bound digest of only the admitted Acct/Tp semantics."""
        preimage = json.dumps(
            {
                "choice": choice,
                "evidence_type": _ACCOUNT_TYPE_EVIDENCE_PURPOSE,
                "value": value,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _with_account_type(fixture: str, marker: str, type_choice: str) -> bytes:
        """Insert one CashAccountType3Choice before the account currency."""
        return fixture.replace(
            marker,
            "        <Tp>\n"
            f"{type_choice}\n"
            "        </Tp>\n"
            "        <Ccy>KRW</Ccy>\n"
            "      </Acct>",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"account-type-evidence-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
