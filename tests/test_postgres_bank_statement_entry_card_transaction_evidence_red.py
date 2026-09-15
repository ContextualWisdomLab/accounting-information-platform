"""PostgreSQL REDs for camt.053 entry card-transaction provenance."""

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
    lookup_bank_statement_entries,
    parse_bank_statement_payload,
)
from accounting_information_platform import bank_statement
from tests import test_postgres_posting as posting

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_ENTRY_CARD_TRANSACTION_PURPOSE = "camt.053.001.14/Stmt/Ntry/CardTx"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementEntryCardTransactionEvidenceRedTests(unittest.TestCase):
    """Retain non-sensitive CardEntry5 provenance without making it accounting truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare statements that isolate aggregate card-entry semantics."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "        <BkTxCd>\n"
            "          <Domn>\n"
            "            <Cd>PMNT</Cd>\n"
            "            <Fmly>\n"
            "              <Cd>RCDT</Cd>\n"
            "              <SubFmlyCd>ESCT</SubFmlyCd>\n"
            "            </Fmly>\n"
            "          </Domn>\n"
            "        </BkTxCd>\n"
            "        <NtryDtls>\n"
        )
        self.assertEqual(fixture.count(marker), 1)

        self.base_payload = self._with_card_transaction(
            fixture,
            marker,
            sale_reconciliation_identifier="SALE-REC-001",
            first_transaction="1001",
            last_transaction="1010",
        )
        self.changed_sale_payload = self._with_card_transaction(
            fixture,
            marker,
            sale_reconciliation_identifier="SALE-REC-002",
            first_transaction="1001",
            last_transaction="1010",
        )
        self.changed_range_payload = self._with_card_transaction(
            fixture,
            marker,
            sale_reconciliation_identifier="SALE-REC-001",
            first_transaction="1001",
            last_transaction="1011",
        )

        formatted_anchor = (
            "        <CardTx>\n"
            "          <AggtdNtry>\n"
            "            <SaleRcncltnId>SALE-REC-001</SaleRcncltnId>\n"
            "            <SeqNbRg>\n"
            "              <FrstTx>1001</FrstTx>\n"
            "              <LastTx>1010</LastTx>\n"
            "            </SeqNbRg>\n"
            "          </AggtdNtry>\n"
            "        </CardTx>\n"
        ).encode("utf-8")
        self.assertEqual(self.base_payload.count(formatted_anchor), 1)
        self.reformatted_payload = self.base_payload.replace(
            formatted_anchor,
            (
                "        <CardTx><AggtdNtry>\n"
                "            <SaleRcncltnId>SALE-REC-001</SaleRcncltnId>\n"
                "            <SeqNbRg><FrstTx>1001</FrstTx><LastTx>1010</LastTx></SeqNbRg>\n"
                "          </AggtdNtry></CardTx>\n"
            ).encode("utf-8"),
            1,
        )

        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_sale_statement = parse_bank_statement_payload(
            self.changed_sale_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_range_statement = parse_bank_statement_payload(
            self.changed_range_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.reformatted_statement = parse_bank_statement_payload(
            self.reformatted_payload, CAMT053_MESSAGE_DEFINITION
        )

        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.base_statement.account_currency_code,
                "account_identifier_hash": self.base_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_card_transaction_aggregate_fields_are_material_entry_evidence(self) -> None:
        """Sale reconciliation identity and aggregate sequence range are material."""
        expected_base = self._expected_hash("SALE-REC-001", "1001", "1010")
        expected_sale = self._expected_hash("SALE-REC-002", "1001", "1010")
        expected_range = self._expected_hash("SALE-REC-001", "1001", "1011")

        for statement, expected_hash in (
            (self.base_statement, expected_base),
            (self.changed_sale_statement, expected_sale),
            (self.changed_range_statement, expected_range),
        ):
            with self.subTest(expected_hash=expected_hash):
                entry = statement.entries[0]
                self.assertRegex(expected_hash, _HASH_PATTERN)
                self.assertEqual(
                    getattr(entry, "entry_card_transaction_evidence_hash", None),
                    expected_hash,
                )
                self._assert_entry_hash_binding(entry, expected_hash)

        self.assertEqual(
            self.base_statement.account_identifier_hash,
            self.changed_sale_statement.account_identifier_hash,
        )
        self.assertEqual(
            self.base_statement.account_identifier_hash,
            self.changed_range_statement.account_identifier_hash,
        )
        self.assertNotEqual(
            self.base_statement.entries[0].source_entry_hash,
            self.changed_sale_statement.entries[0].source_entry_hash,
        )
        self.assertNotEqual(
            self.base_statement.entries[0].source_entry_hash,
            self.changed_range_statement.entries[0].source_entry_hash,
        )
        self.assertNotEqual(
            self.base_statement.normalized_payload_hash,
            self.changed_sale_statement.normalized_payload_hash,
        )
        self.assertNotEqual(
            self.base_statement.normalized_payload_hash,
            self.changed_range_statement.normalized_payload_hash,
        )
        self.assertEqual(
            self.base_statement.entries[1].source_entry_hash,
            self.changed_sale_statement.entries[1].source_entry_hash,
        )
        self.assertEqual(
            self.base_statement.entries[1].source_entry_hash,
            self.changed_range_statement.entries[1].source_entry_hash,
        )

    def test_xml_layout_does_not_change_card_transaction_semantics(self) -> None:
        """Formatting differences must not alter normalized CardTx evidence."""
        expected_hash = self._expected_hash("SALE-REC-001", "1001", "1010")
        first_entry = self.base_statement.entries[0]
        reformatted_entry = self.reformatted_statement.entries[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(first_entry, "entry_card_transaction_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(reformatted_entry, "entry_card_transaction_evidence_hash", None),
            expected_hash,
        )
        self._assert_entry_hash_binding(first_entry, expected_hash)
        self._assert_entry_hash_binding(reformatted_entry, expected_hash)
        self.assertEqual(first_entry.source_entry_hash, reformatted_entry.source_entry_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_card_transaction_requires_explicit_statement_correction(self) -> None:
        """A material CardTx change cannot silently replay one statement identity."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.changed_sale_payload, "changed-sale"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_preserves_card_transaction_provenance(self) -> None:
        """Supported reads retain aggregate CardTx semantics and purpose digest."""
        expected_hash = self._expected_hash("SALE-REC-001", "1001", "1010")
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "lookup"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        entry = document["bank_statement_entries"][0]

        self.assertEqual(entry.get("entry_card_transaction_evidence_hash"), expected_hash)
        self.assertEqual(
            entry.get("card_transaction_evidence"),
            {
                "aggregate_sale_reconciliation_identifier": "SALE-REC-001",
                "aggregate_sequence_range": {
                    "first_transaction": "1001",
                    "last_transaction": "1010",
                },
            },
        )

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Bind source_entry_hash to semantic CardTx evidence, never raw XML bytes."""
        projection = dict(bank_statement._entry_payload(entry))
        if projection.get("entry_card_transaction_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry the exact "
                "entry_card_transaction_evidence_hash"
            )
        if "source_artifact_hash" in projection:
            raise AssertionError("canonical entry projection must not use raw artifact identity")
        preimage = json.dumps(
            projection,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if entry.source_entry_hash != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must be the digest of the canonical entry projection"
            )

    @staticmethod
    def _expected_hash(
        sale_reconciliation_identifier: str,
        first_transaction: str,
        last_transaction: str,
    ) -> str:
        """Digest the admitted non-sensitive aggregate CardEntry5 semantics."""
        preimage = json.dumps(
            {
                "evidence_type": _ENTRY_CARD_TRANSACTION_PURPOSE,
                "aggregate_sale_reconciliation_identifier": sale_reconciliation_identifier,
                "aggregate_sequence_range": {
                    "first_transaction": first_transaction,
                    "last_transaction": last_transaction,
                },
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _with_card_transaction(
        fixture: str,
        marker: str,
        *,
        sale_reconciliation_identifier: str,
        first_transaction: str,
        last_transaction: str,
    ) -> bytes:
        """Insert one entry-level CardEntry5 aggregate before NtryDtls."""
        replacement = (
            "        <BkTxCd>\n"
            "          <Domn>\n"
            "            <Cd>PMNT</Cd>\n"
            "            <Fmly>\n"
            "              <Cd>RCDT</Cd>\n"
            "              <SubFmlyCd>ESCT</SubFmlyCd>\n"
            "            </Fmly>\n"
            "          </Domn>\n"
            "        </BkTxCd>\n"
            "        <CardTx>\n"
            "          <AggtdNtry>\n"
            f"            <SaleRcncltnId>{sale_reconciliation_identifier}</SaleRcncltnId>\n"
            "            <SeqNbRg>\n"
            f"              <FrstTx>{first_transaction}</FrstTx>\n"
            f"              <LastTx>{last_transaction}</LastTx>\n"
            "            </SeqNbRg>\n"
            "          </AggtdNtry>\n"
            "        </CardTx>\n"
            "        <NtryDtls>\n"
        )
        return fixture.replace(marker, replacement, 1).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Build a supported ingest command with an independent idempotency key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"entry-card-transaction-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
