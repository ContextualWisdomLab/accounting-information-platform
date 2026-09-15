"""PostgreSQL REDs for camt.053 additional-statement-information evidence."""

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
_ADDITIONAL_STATEMENT_INFORMATION_PURPOSE = "camt.053.001.14/Stmt/AddtlStmtInf"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementAdditionalStatementInformationEvidenceRedTests(unittest.TestCase):
    """Retain statement-level bank narrative as purpose-bound evidence only."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare statements that isolate one AddtlStmtInf semantic."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "      </Ntry>\n    </Stmt>\n"
        self.assertEqual(fixture.count(marker), 1)

        self.first_information = "Bank statement note: sweep completed"
        self.second_information = "Bank statement note: sweep pending"
        self.first_payload = self._with_additional_statement_information(
            fixture,
            marker,
            self.first_information,
        )
        self.second_payload = self._with_additional_statement_information(
            fixture,
            marker,
            self.second_information,
        )

        formatting_anchor = (
            f"      <AddtlStmtInf>{self.first_information}</AddtlStmtInf>\n"
            "    </Stmt>\n"
        ).encode("utf-8")
        self.assertEqual(self.first_payload.count(formatting_anchor), 1)
        self.reformatted_first_payload = self.first_payload.replace(
            formatting_anchor,
            (
                f"      <AddtlStmtInf>{self.first_information}</AddtlStmtInf>\n"
                "      \n"
                "    </Stmt>\n"
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

    def test_additional_statement_information_is_material_statement_evidence(self) -> None:
        """Changing only AddtlStmtInf changes purpose-bound statement evidence."""
        first_hash = self._expected_information_hash(self.first_information)
        second_hash = self._expected_information_hash(self.second_information)

        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.second_statement.source_artifact_hash,
        )
        self.assertEqual(
            self.first_statement.account_identifier_hash,
            self.second_statement.account_identifier_hash,
        )
        self.assertRegex(first_hash, _HASH_PATTERN)
        self.assertRegex(second_hash, _HASH_PATTERN)
        self.assertEqual(
            getattr(
                self.first_statement,
                "statement_additional_information_evidence_hash",
                None,
            ),
            first_hash,
        )
        self.assertEqual(
            getattr(
                self.second_statement,
                "statement_additional_information_evidence_hash",
                None,
            ),
            second_hash,
        )
        self.assertNotEqual(first_hash, second_hash)
        self._assert_normalized_hash_binding(
            self.first_statement,
            first_hash,
            self.first_information,
        )
        self._assert_normalized_hash_binding(
            self.second_statement,
            second_hash,
            self.second_information,
        )
        self.assertNotEqual(
            self.first_statement.normalized_payload_hash,
            self.second_statement.normalized_payload_hash,
        )

    def test_source_formatting_cannot_change_semantically_equal_statement_information(self) -> None:
        """XML layout outside AddtlStmtInf text must not leak into semantic identity."""
        expected_hash = self._expected_information_hash(self.first_information)
        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.reformatted_first_statement.source_artifact_hash,
        )
        self.assertEqual(
            self.first_statement.account_identifier_hash,
            self.reformatted_first_statement.account_identifier_hash,
        )
        self.assertEqual(
            getattr(
                self.first_statement,
                "statement_additional_information_evidence_hash",
                None,
            ),
            expected_hash,
        )
        self.assertEqual(
            getattr(
                self.reformatted_first_statement,
                "statement_additional_information_evidence_hash",
                None,
            ),
            expected_hash,
        )
        self._assert_normalized_hash_binding(
            self.first_statement,
            expected_hash,
            self.first_information,
        )
        self._assert_normalized_hash_binding(
            self.reformatted_first_statement,
            expected_hash,
            self.first_information,
        )
        self.assertEqual(
            self.first_statement.normalized_payload_hash,
            self.reformatted_first_statement.normalized_payload_hash,
        )

    def test_changed_additional_statement_information_requires_correction(self) -> None:
        """Same statement identity cannot silently replay changed bank narrative."""
        first = accept_bank_statement_evidence(
            self._command(self.first_payload, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(first["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.second_payload, "second"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_lookup_returns_digest_without_copying_statement_narrative(self) -> None:
        """Buyer reads expose evidence identity, not unrestricted source free text."""
        expected_hash = self._expected_information_hash(self.first_information)
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
        self.assertEqual(
            document.get("statement_additional_information_evidence_hash"),
            expected_hash,
        )
        self.assertNotIn(
            self.first_information,
            json.dumps(document, default=str, sort_keys=True),
        )

    @staticmethod
    def _assert_normalized_hash_binding(
        statement: object,
        information_hash: str,
        information_text: str,
    ) -> None:
        """Bind normalized identity to the digest without copying source narrative."""
        projection = dict(bank_statement._normalized_payload(statement))
        if (
            projection.get("statement_additional_information_evidence_hash")
            != information_hash
        ):
            raise AssertionError(
                "canonical normalized statement projection must carry the exact "
                "statement_additional_information_evidence_hash"
            )
        serialized_projection = json.dumps(
            projection,
            separators=(",", ":"),
            sort_keys=True,
        )
        if information_text in serialized_projection:
            raise AssertionError(
                "canonical normalized statement projection must not copy AddtlStmtInf free text"
            )
        if "source_artifact_hash" in projection:
            raise AssertionError(
                "canonical normalized statement projection must not carry raw artifact identity"
            )
        expected_statement_hash = (
            "sha256:"
            + hashlib.sha256(serialized_projection.encode("utf-8")).hexdigest()
        )
        if statement.normalized_payload_hash != expected_statement_hash:
            raise AssertionError(
                "normalized_payload_hash must digest the canonical normalized projection"
            )

    @staticmethod
    def _expected_information_hash(value: str) -> str:
        """Digest the exact Max500Text semantic retained for this focused RED."""
        preimage = json.dumps(
            {
                "evidence_type": _ADDITIONAL_STATEMENT_INFORMATION_PURPOSE,
                "text": value,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _with_additional_statement_information(
        fixture: str,
        marker: str,
        value: str,
    ) -> bytes:
        """Insert one lawful AddtlStmtInf after the final entry."""
        return fixture.replace(
            marker,
            "      </Ntry>\n"
            f"      <AddtlStmtInf>{value}</AddtlStmtInf>\n"
            "    </Stmt>\n",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"additional-statement-information-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
