"""PostgreSQL REDs for camt.053 statement-interest evidence."""

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
_STATEMENT_INTEREST_EVIDENCE_PURPOSE = "camt.053.001.14/Stmt/Intrst"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementInterestEvidenceRedTests(unittest.TestCase):
    """Retain reported statement interest without making it accounting policy."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare statements that isolate one AccountInterest4 rate semantic."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "      </Acct>\n      <Bal>\n"
        self.assertEqual(fixture.count(marker), 1)

        self.percentage_payload = self._with_interest_rate(
            fixture,
            marker,
            choice="Pctg",
            value="1.25",
        )
        self.changed_percentage_payload = self._with_interest_rate(
            fixture,
            marker,
            choice="Pctg",
            value="1.26",
        )
        self.other_payload = self._with_interest_rate(
            fixture,
            marker,
            choice="Othr",
            value="1.25",
        )

        formatting_anchor = (
            "      <Intrst>\n"
            "        <Rate>\n"
            "          <Tp>\n"
            "            <Pctg>1.25</Pctg>\n"
            "          </Tp>\n"
            "        </Rate>\n"
            "      </Intrst>\n"
        ).encode("utf-8")
        self.assertEqual(self.percentage_payload.count(formatting_anchor), 1)
        self.reformatted_percentage_payload = self.percentage_payload.replace(
            formatting_anchor,
            (
                "      <Intrst>\n"
                "        <Rate>\n"
                "          <Tp>\n"
                "            <Pctg>1.25</Pctg>\n"
                "            \n"
                "          </Tp>\n"
                "        </Rate>\n"
                "      </Intrst>\n"
            ).encode("utf-8"),
            1,
        )

        self.percentage_statement = parse_bank_statement_payload(
            self.percentage_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_percentage_statement = parse_bank_statement_payload(
            self.changed_percentage_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.other_statement = parse_bank_statement_payload(
            self.other_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.reformatted_percentage_statement = parse_bank_statement_payload(
            self.reformatted_percentage_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.percentage_statement.account_currency_code,
                "account_identifier_hash": self.percentage_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_interest_rate_value_is_material_statement_evidence(self) -> None:
        """Changing only a reported percentage rate changes statement evidence."""
        first_hash = self._expected_interest_hash("Pctg", "1.25")
        second_hash = self._expected_interest_hash("Pctg", "1.26")

        self.assertNotEqual(
            self.percentage_statement.source_artifact_hash,
            self.changed_percentage_statement.source_artifact_hash,
        )
        self.assertEqual(
            self.percentage_statement.account_identifier_hash,
            self.changed_percentage_statement.account_identifier_hash,
        )
        self.assertRegex(first_hash, _HASH_PATTERN)
        self.assertRegex(second_hash, _HASH_PATTERN)
        self.assertEqual(
            getattr(self.percentage_statement, "statement_interest_evidence_hash", None),
            first_hash,
        )
        self.assertEqual(
            getattr(
                self.changed_percentage_statement,
                "statement_interest_evidence_hash",
                None,
            ),
            second_hash,
        )
        self.assertNotEqual(first_hash, second_hash)
        self._assert_normalized_hash_binding(self.percentage_statement, first_hash)
        self._assert_normalized_hash_binding(
            self.changed_percentage_statement,
            second_hash,
        )
        self.assertNotEqual(
            self.percentage_statement.normalized_payload_hash,
            self.changed_percentage_statement.normalized_payload_hash,
        )

    def test_interest_rate_choice_discriminator_is_material(self) -> None:
        """Pctg and Othr remain distinct even when their lexical value is equal."""
        percentage_hash = self._expected_interest_hash("Pctg", "1.25")
        other_hash = self._expected_interest_hash("Othr", "1.25")

        self.assertNotEqual(
            self.percentage_statement.source_artifact_hash,
            self.other_statement.source_artifact_hash,
        )
        self.assertEqual(
            self.percentage_statement.account_identifier_hash,
            self.other_statement.account_identifier_hash,
        )
        self.assertEqual(
            getattr(self.percentage_statement, "statement_interest_evidence_hash", None),
            percentage_hash,
        )
        self.assertEqual(
            getattr(self.other_statement, "statement_interest_evidence_hash", None),
            other_hash,
        )
        self.assertNotEqual(percentage_hash, other_hash)
        self._assert_normalized_hash_binding(self.percentage_statement, percentage_hash)
        self._assert_normalized_hash_binding(self.other_statement, other_hash)
        self.assertNotEqual(
            self.percentage_statement.normalized_payload_hash,
            self.other_statement.normalized_payload_hash,
        )

    def test_source_formatting_cannot_change_semantically_equal_interest(self) -> None:
        """Insignificant XML formatting must not leak into statement-interest identity."""
        expected_hash = self._expected_interest_hash("Pctg", "1.25")
        self.assertNotEqual(
            self.percentage_statement.source_artifact_hash,
            self.reformatted_percentage_statement.source_artifact_hash,
        )
        self.assertEqual(
            self.percentage_statement.account_identifier_hash,
            self.reformatted_percentage_statement.account_identifier_hash,
        )
        self.assertEqual(
            getattr(self.percentage_statement, "statement_interest_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(
                self.reformatted_percentage_statement,
                "statement_interest_evidence_hash",
                None,
            ),
            expected_hash,
        )
        self._assert_normalized_hash_binding(self.percentage_statement, expected_hash)
        self._assert_normalized_hash_binding(
            self.reformatted_percentage_statement,
            expected_hash,
        )
        self.assertEqual(
            self.percentage_statement.normalized_payload_hash,
            self.reformatted_percentage_statement.normalized_payload_hash,
        )

    def test_changed_interest_requires_statement_correction(self) -> None:
        """The same statement identity cannot silently replay changed interest evidence."""
        first = accept_bank_statement_evidence(
            self._command(self.percentage_payload, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(first["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.changed_percentage_payload, "second"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_statement_lookup_exposes_same_purpose_bound_interest_hash(self) -> None:
        """Buyer reads expose the exact interest digest admitted at normalization."""
        expected_hash = self._expected_interest_hash("Pctg", "1.25")
        self.assertEqual(
            getattr(self.percentage_statement, "statement_interest_evidence_hash", None),
            expected_hash,
        )

        accepted = accept_bank_statement_evidence(
            self._command(self.percentage_payload, "lookup"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        document = lookup_bank_statement(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        self.assertEqual(document.get("statement_interest_evidence_hash"), expected_hash)

    @staticmethod
    def _assert_normalized_hash_binding(statement: object, interest_hash: str) -> None:
        """Bind statement identity to semantic interest evidence, never raw XML bytes."""
        projection = dict(bank_statement._normalized_payload(statement))
        if projection.get("statement_interest_evidence_hash") != interest_hash:
            raise AssertionError(
                "canonical normalized statement projection must carry the exact "
                "statement_interest_evidence_hash"
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
    def _expected_interest_hash(rate_choice: str, rate_value: str) -> str:
        """Digest the complete simple AccountInterest4 semantics used by this RED."""
        preimage = json.dumps(
            {
                "evidence_type": _STATEMENT_INTEREST_EVIDENCE_PURPOSE,
                "interest_records": [
                    {
                        "rates": [
                            {
                                "rate_choice": rate_choice,
                                "rate_value": rate_value,
                            }
                        ]
                    }
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _with_interest_rate(
        fixture: str,
        marker: str,
        *,
        choice: str,
        value: str,
    ) -> bytes:
        """Insert one lawful AccountInterest4 immediately before statement balances."""
        if choice not in {"Pctg", "Othr"}:
            raise AssertionError(f"unsupported RateType4Choice for fixture: {choice}")
        return fixture.replace(
            marker,
            "      </Acct>\n"
            "      <Intrst>\n"
            "        <Rate>\n"
            "          <Tp>\n"
            f"            <{choice}>{value}</{choice}>\n"
            "          </Tp>\n"
            "        </Rate>\n"
            "      </Intrst>\n"
            "      <Bal>\n",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"interest-evidence-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
