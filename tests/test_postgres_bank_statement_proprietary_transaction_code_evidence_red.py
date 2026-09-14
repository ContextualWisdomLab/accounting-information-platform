"""PostgreSQL REDs for proprietary ISO 20022 bank-transaction-code evidence."""

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
    lookup_bank_statement_entries,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting


class BankStatementProprietaryTransactionCodeEvidenceRedTests(unittest.TestCase):
    """Preserve schema-valid BkTxCd/Prtry evidence without aliasing replays."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create same-identity statements that vary only proprietary code evidence."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "        </Domn>\n        </BkTxCd>"
        self.assertEqual(fixture.count(marker), 2)

        self.first_code = "BANK-CODE-A"
        self.second_code = "BANK-CODE-B"
        self.first_issuer = "Fixture Bank A"
        self.second_issuer = "Fixture Bank B"
        self.first_payload = self._with_proprietary_code(
            fixture, marker, code=self.first_code, issuer=self.first_issuer
        )
        self.changed_code_payload = self._with_proprietary_code(
            fixture, marker, code=self.second_code, issuer=self.first_issuer
        )
        self.changed_issuer_payload = self._with_proprietary_code(
            fixture, marker, code=self.first_code, issuer=self.second_issuer
        )
        self.code_only_payload = self._with_proprietary_code(
            fixture, marker, code=self.first_code, issuer=None
        )
        self.proprietary_only_payload = self._with_proprietary_only_code(
            fixture, code=self.first_code, issuer=self.first_issuer
        )

        self.first_statement = parse_bank_statement_payload(
            self.first_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_code_statement = parse_bank_statement_payload(
            self.changed_code_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_issuer_statement = parse_bank_statement_payload(
            self.changed_issuer_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.code_only_statement = parse_bank_statement_payload(
            self.code_only_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.proprietary_only_statement = parse_bank_statement_payload(
            self.proprietary_only_payload, CAMT053_MESSAGE_DEFINITION
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

    def test_proprietary_code_and_issuer_are_material_to_canonical_evidence(self) -> None:
        """Changing only proprietary code or issuer changes entry and statement identity."""
        first_entry = self.first_statement.entries[0]
        changed_code_entry = self.changed_code_statement.entries[0]
        changed_issuer_entry = self.changed_issuer_statement.entries[0]

        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.changed_code_statement.source_artifact_hash,
        )
        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.changed_issuer_statement.source_artifact_hash,
        )
        self.assertNotEqual(first_entry.source_entry_hash, changed_code_entry.source_entry_hash)
        self.assertNotEqual(first_entry.source_entry_hash, changed_issuer_entry.source_entry_hash)
        self.assertNotEqual(
            self.first_statement.normalized_payload_hash,
            self.changed_code_statement.normalized_payload_hash,
        )
        self.assertNotEqual(
            self.first_statement.normalized_payload_hash,
            self.changed_issuer_statement.normalized_payload_hash,
        )

    def test_same_statement_identity_cannot_replay_changed_proprietary_code(self) -> None:
        """Changed proprietary transaction evidence requires correction, not silent replay."""
        accept_bank_statement_evidence(
            self._command(self.first_payload, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        with self.assertRaisesRegex(
            AccountingValidationError,
            r"^statement identity already exists with different entry evidence\.",
        ):
            accept_bank_statement_evidence(
                self._command(self.changed_code_payload, "changed-code"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_entry_lookup_preserves_proprietary_code_and_issuer(self) -> None:
        """Buyer-visible entry reads expose the bank-reported proprietary code evidence."""
        accepted = accept_bank_statement_evidence(
            self._command(self.first_payload, "lookup"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        first_entry = document["bank_statement_entries"][0]
        self.assertEqual(first_entry["bank_transaction_proprietary_code"], self.first_code)
        self.assertEqual(first_entry["bank_transaction_proprietary_issuer"], self.first_issuer)

    def test_proprietary_issuer_remains_optional_on_supported_ingest(self) -> None:
        """Prtry/Cd remains usable evidence when optional Prtry/Issr is absent."""
        accepted = accept_bank_statement_evidence(
            self._command(self.code_only_payload, "code-only"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        first_entry = document["bank_statement_entries"][0]
        self.assertEqual(first_entry["bank_transaction_proprietary_code"], self.first_code)
        self.assertIsNone(first_entry["bank_transaction_proprietary_issuer"])

    def test_proprietary_only_transaction_code_remains_supported_evidence(self) -> None:
        """BkTxCd may carry Prtry without Domn and still retain exact evidence."""
        parsed_entry = self.proprietary_only_statement.entries[0]
        self.assertIsNone(parsed_entry.bank_transaction_domain_code)
        self.assertIsNone(parsed_entry.bank_transaction_family_code)
        self.assertIsNone(parsed_entry.bank_transaction_subfamily_code)

        accepted = accept_bank_statement_evidence(
            self._command(self.proprietary_only_payload, "proprietary-only"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        stored_entry = document["bank_statement_entries"][0]
        self.assertIsNone(stored_entry["bank_transaction_domain_code"])
        self.assertIsNone(stored_entry["bank_transaction_family_code"])
        self.assertIsNone(stored_entry["bank_transaction_subfamily_code"])
        self.assertEqual(stored_entry["bank_transaction_proprietary_code"], self.first_code)
        self.assertEqual(
            stored_entry["bank_transaction_proprietary_issuer"], self.first_issuer
        )

    @staticmethod
    def _with_proprietary_code(
        fixture: str,
        marker: str,
        *,
        code: str,
        issuer: str | None,
    ) -> bytes:
        """Add one schema-shaped proprietary BkTxCd beside the first domain code."""
        issuer_element = "" if issuer is None else f"          <Issr>{issuer}</Issr>\n"
        replacement = (
            "        </Domn>\n"
            "        <Prtry>\n"
            f"          <Cd>{code}</Cd>\n"
            f"{issuer_element}"
            "        </Prtry>\n"
            "        </BkTxCd>"
        )
        return fixture.replace(marker, replacement, 1).encode("utf-8")

    @staticmethod
    def _with_proprietary_only_code(
        fixture: str,
        *,
        code: str,
        issuer: str,
    ) -> bytes:
        """Replace the first domain code with schema-valid proprietary-only evidence."""
        domain = (
            "        <BkTxCd>\n"
            "          <Domn>\n"
            "            <Cd>PMNT</Cd>\n"
            "            <Fmly>\n"
            "              <Cd>RCDT</Cd>\n"
            "              <SubFmlyCd>ESCT</SubFmlyCd>\n"
            "            </Fmly>\n"
            "          </Domn>\n"
            "        </BkTxCd>"
        )
        if fixture.count(domain) != 1:
            raise AssertionError("expected one first-entry BkTxCd domain anchor")
        proprietary = (
            "        <BkTxCd>\n"
            "          <Prtry>\n"
            f"            <Cd>{code}</Cd>\n"
            f"            <Issr>{issuer}</Issr>\n"
            "          </Prtry>\n"
            "        </BkTxCd>"
        )
        return fixture.replace(domain, proprietary, 1).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"entry-proprietary-code-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
