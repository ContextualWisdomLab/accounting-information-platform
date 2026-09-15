"""PostgreSQL REDs for camt.053 statement related-account evidence."""

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
_RELATED_ACCOUNT_EVIDENCE_PURPOSE = "camt.053.001.14/Stmt/RltdAcct"


class BankStatementRelatedAccountEvidenceRedTests(unittest.TestCase):
    """Retain related-account provenance without promoting it to account-master truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare two statements that differ only in related-account identity."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "      </Acct>\n      <Bal>\n"
        self.assertEqual(fixture.count(marker), 1)

        self.operating_payload = self._with_related_account(
            fixture,
            marker,
            "related-operating-account",
        )
        self.reserve_payload = self._with_related_account(
            fixture,
            marker,
            "related-reserve-account",
        )

        formatting_anchor = (
            "      <RltdAcct>\n"
            "        <Id>\n"
            "          <Othr>\n"
            "            <Id>related-operating-account</Id>\n"
            "          </Othr>\n"
            "        </Id>\n"
            "      </RltdAcct>\n"
        ).encode("utf-8")
        self.assertEqual(self.operating_payload.count(formatting_anchor), 1)
        self.reformatted_operating_payload = self.operating_payload.replace(
            formatting_anchor,
            (
                "      <RltdAcct>\n"
                "        <Id>\n"
                "          <Othr>\n"
                "            \n"
                "            <Id>related-operating-account</Id>\n"
                "          </Othr>\n"
                "        </Id>\n"
                "      </RltdAcct>\n"
            ).encode("utf-8"),
            1,
        )

        self.operating_statement = parse_bank_statement_payload(
            self.operating_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.reserve_statement = parse_bank_statement_payload(
            self.reserve_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.reformatted_operating_statement = parse_bank_statement_payload(
            self.reformatted_operating_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.operating_statement.account_currency_code,
                "account_identifier_hash": self.operating_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_related_account_identifier_is_material_statement_evidence(self) -> None:
        """Changing only RltdAcct/Id changes provenance but not the primary account identity."""
        operating_hash = self._expected_related_account_hash("related-operating-account")
        reserve_hash = self._expected_related_account_hash("related-reserve-account")

        self.assertNotEqual(
            self.operating_statement.source_artifact_hash,
            self.reserve_statement.source_artifact_hash,
        )
        self.assertEqual(
            self.operating_statement.account_identifier_hash,
            self.reserve_statement.account_identifier_hash,
        )
        self.assertRegex(operating_hash, _HASH_PATTERN)
        self.assertRegex(reserve_hash, _HASH_PATTERN)
        self.assertEqual(
            getattr(self.operating_statement, "related_account_evidence_hash", None),
            operating_hash,
        )
        self.assertEqual(
            getattr(self.reserve_statement, "related_account_evidence_hash", None),
            reserve_hash,
        )
        self.assertNotEqual(operating_hash, reserve_hash)
        self._assert_normalized_hash_binding(self.operating_statement, operating_hash)
        self._assert_normalized_hash_binding(self.reserve_statement, reserve_hash)
        self.assertNotEqual(
            self.operating_statement.normalized_payload_hash,
            self.reserve_statement.normalized_payload_hash,
        )

    def test_source_formatting_cannot_change_semantically_equal_related_account(self) -> None:
        """Insignificant XML formatting must not leak into related-account evidence identity."""
        expected_hash = self._expected_related_account_hash("related-operating-account")
        self.assertNotEqual(
            self.operating_statement.source_artifact_hash,
            self.reformatted_operating_statement.source_artifact_hash,
        )
        self.assertEqual(
            self.operating_statement.account_identifier_hash,
            self.reformatted_operating_statement.account_identifier_hash,
        )
        self.assertEqual(
            getattr(self.operating_statement, "related_account_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(
                self.reformatted_operating_statement,
                "related_account_evidence_hash",
                None,
            ),
            expected_hash,
        )
        self._assert_normalized_hash_binding(self.operating_statement, expected_hash)
        self._assert_normalized_hash_binding(
            self.reformatted_operating_statement,
            expected_hash,
        )
        self.assertEqual(
            self.operating_statement.normalized_payload_hash,
            self.reformatted_operating_statement.normalized_payload_hash,
        )

    def test_changed_related_account_requires_statement_correction(self) -> None:
        """The same statement identity cannot silently replay changed related-account evidence."""
        first = accept_bank_statement_evidence(
            self._command(self.operating_payload, "first"),
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
                self._command(self.reserve_payload, "second"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_statement_lookup_exposes_same_purpose_bound_related_account_hash(self) -> None:
        """Buyer reads expose the exact related-account digest admitted at normalization."""
        expected_hash = self._expected_related_account_hash("related-operating-account")
        self.assertEqual(
            getattr(self.operating_statement, "related_account_evidence_hash", None),
            expected_hash,
        )

        accepted = accept_bank_statement_evidence(
            self._command(self.operating_payload, "lookup"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        document = lookup_bank_statement(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        self.assertEqual(document.get("related_account_evidence_hash"), expected_hash)

    @staticmethod
    def _assert_normalized_hash_binding(statement: object, related_account_hash: str) -> None:
        """Bind statement identity to semantic related-account evidence, never raw bytes."""
        projection = dict(bank_statement._normalized_payload(statement))
        if projection.get("related_account_evidence_hash") != related_account_hash:
            raise AssertionError(
                "canonical normalized statement projection must carry the exact "
                "related_account_evidence_hash"
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
    def _expected_related_account_hash(identifier: str) -> str:
        """Return the digest of only the admitted RltdAcct/Othr identifier semantics."""
        preimage = json.dumps(
            {
                "evidence_type": _RELATED_ACCOUNT_EVIDENCE_PURPOSE,
                "identification_choice": "Othr",
                "identifier": identifier,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _with_related_account(fixture: str, marker: str, identifier: str) -> bytes:
        """Insert one lawful CashAccount40 RltdAcct immediately after the primary Acct."""
        return fixture.replace(
            marker,
            "      </Acct>\n"
            "      <RltdAcct>\n"
            "        <Id>\n"
            "          <Othr>\n"
            f"            <Id>{identifier}</Id>\n"
            "          </Othr>\n"
            "        </Id>\n"
            "      </RltdAcct>\n"
            "      <Bal>\n",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"related-account-evidence-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
