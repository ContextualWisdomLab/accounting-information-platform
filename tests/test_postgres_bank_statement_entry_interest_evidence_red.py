"""PostgreSQL REDs for camt.053 entry-level interest provenance."""

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
_ENTRY_INTEREST_PURPOSE = "camt.053.001.14/Stmt/Ntry/Intrst"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementEntryInterestEvidenceRedTests(unittest.TestCase):
    """Retain bank-reported entry interest without turning it into posting policy."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare statements that isolate TransactionInterest4 semantics."""
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

        self.base_payload = self._with_interest(
            fixture,
            marker,
            total_amount="125.00",
            record_amount="100.00",
            credit_debit_code="CRDT",
        )
        self.changed_total_payload = self._with_interest(
            fixture,
            marker,
            total_amount="126.00",
            record_amount="100.00",
            credit_debit_code="CRDT",
        )
        self.changed_record_amount_payload = self._with_interest(
            fixture,
            marker,
            total_amount="125.00",
            record_amount="101.00",
            credit_debit_code="CRDT",
        )
        self.changed_direction_payload = self._with_interest(
            fixture,
            marker,
            total_amount="125.00",
            record_amount="100.00",
            credit_debit_code="DBIT",
        )

        formatting_anchor = (
            "        <Intrst>\n"
            "          <TtlIntrstAndTaxAmt Ccy=\"KRW\">125.00</TtlIntrstAndTaxAmt>\n"
            "          <Rcrd>\n"
            "            <Amt Ccy=\"KRW\">100.00</Amt>\n"
            "            <CdtDbtInd>CRDT</CdtDbtInd>\n"
            "          </Rcrd>\n"
            "        </Intrst>\n"
        ).encode("utf-8")
        self.assertEqual(self.base_payload.count(formatting_anchor), 1)
        self.reformatted_payload = self.base_payload.replace(
            formatting_anchor,
            (
                "        <Intrst><TtlIntrstAndTaxAmt Ccy=\"KRW\">125.00</TtlIntrstAndTaxAmt>\n"
                "          <Rcrd><Amt Ccy=\"KRW\">100.00</Amt>"
                "<CdtDbtInd>CRDT</CdtDbtInd></Rcrd></Intrst>\n"
            ).encode("utf-8"),
            1,
        )

        self.base_statement = parse_bank_statement_payload(
            self.base_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_total_statement = parse_bank_statement_payload(
            self.changed_total_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_record_amount_statement = parse_bank_statement_payload(
            self.changed_record_amount_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.changed_direction_statement = parse_bank_statement_payload(
            self.changed_direction_payload, CAMT053_MESSAGE_DEFINITION
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

    def test_entry_interest_amount_and_direction_are_material_evidence(self) -> None:
        """Total amount, record amount, and record direction are independently material."""
        variants = (
            (
                self.base_statement,
                self._expected_hash("125.00", "100.00", "CRDT"),
            ),
            (
                self.changed_total_statement,
                self._expected_hash("126.00", "100.00", "CRDT"),
            ),
            (
                self.changed_record_amount_statement,
                self._expected_hash("125.00", "101.00", "CRDT"),
            ),
            (
                self.changed_direction_statement,
                self._expected_hash("125.00", "100.00", "DBIT"),
            ),
        )

        expected_hashes: list[str] = []
        for statement, expected_hash in variants:
            with self.subTest(expected_hash=expected_hash):
                entry = statement.entries[0]
                self.assertRegex(expected_hash, _HASH_PATTERN)
                self.assertEqual(
                    getattr(entry, "entry_interest_evidence_hash", None),
                    expected_hash,
                )
                self._assert_entry_hash_binding(entry, expected_hash)
                expected_hashes.append(expected_hash)

        self.assertEqual(len(set(expected_hashes)), len(expected_hashes))
        for changed in (
            self.changed_total_statement,
            self.changed_record_amount_statement,
            self.changed_direction_statement,
        ):
            self.assertEqual(
                self.base_statement.account_identifier_hash,
                changed.account_identifier_hash,
            )
            self.assertNotEqual(
                self.base_statement.entries[0].source_entry_hash,
                changed.entries[0].source_entry_hash,
            )
            self.assertNotEqual(
                self.base_statement.normalized_payload_hash,
                changed.normalized_payload_hash,
            )
            self.assertEqual(
                self.base_statement.entries[1].source_entry_hash,
                changed.entries[1].source_entry_hash,
            )

    def test_xml_layout_does_not_change_entry_interest_semantics(self) -> None:
        """Formatting differences must not alter normalized interest evidence."""
        expected_hash = self._expected_hash("125.00", "100.00", "CRDT")
        base_entry = self.base_statement.entries[0]
        reformatted_entry = self.reformatted_statement.entries[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(base_entry, "entry_interest_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(reformatted_entry, "entry_interest_evidence_hash", None),
            expected_hash,
        )
        self._assert_entry_hash_binding(base_entry, expected_hash)
        self._assert_entry_hash_binding(reformatted_entry, expected_hash)
        self.assertEqual(base_entry.source_entry_hash, reformatted_entry.source_entry_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_entry_interest_requires_explicit_statement_correction(self) -> None:
        """Material interest provenance cannot silently replay one statement identity."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.changed_total_payload, "changed-total"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_preserves_exact_entry_interest_provenance(self) -> None:
        """Supported reads retain exact reported interest semantics and purpose digest."""
        expected_hash = self._expected_hash("125.00", "100.00", "CRDT")
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

        self.assertEqual(entry.get("entry_interest_evidence_hash"), expected_hash)
        self.assertEqual(
            entry.get("interest_evidence"),
            {
                "total_interest_and_tax_amount": {
                    "amount": "125.00",
                    "currency_code": "KRW",
                },
                "records": [
                    {
                        "amount": "100.00",
                        "currency_code": "KRW",
                        "credit_debit_code": "CRDT",
                    }
                ],
            },
        )

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Bind source_entry_hash to semantic interest evidence, never XML layout."""
        projection = dict(bank_statement._entry_payload(entry))
        if projection.get("entry_interest_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry the exact entry_interest_evidence_hash"
            )
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
        total_amount: str,
        record_amount: str,
        credit_debit_code: str,
    ) -> str:
        """Digest the complete focused TransactionInterest4 semantics."""
        preimage = json.dumps(
            {
                "evidence_type": _ENTRY_INTEREST_PURPOSE,
                "total_interest_and_tax_amount": {
                    "amount": total_amount,
                    "currency_code": "KRW",
                },
                "records": [
                    {
                        "amount": record_amount,
                        "currency_code": "KRW",
                        "credit_debit_code": credit_debit_code,
                    }
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _with_interest(
        fixture: str,
        marker: str,
        *,
        total_amount: str,
        record_amount: str,
        credit_debit_code: str,
    ) -> bytes:
        """Insert one entry-level TransactionInterest4 immediately before NtryDtls."""
        return fixture.replace(
            marker,
            "        <BkTxCd>\n"
            "          <Domn>\n"
            "            <Cd>PMNT</Cd>\n"
            "            <Fmly>\n"
            "              <Cd>RCDT</Cd>\n"
            "              <SubFmlyCd>ESCT</SubFmlyCd>\n"
            "            </Fmly>\n"
            "          </Domn>\n"
            "        </BkTxCd>\n"
            "        <Intrst>\n"
            f"          <TtlIntrstAndTaxAmt Ccy=\"KRW\">{total_amount}</TtlIntrstAndTaxAmt>\n"
            "          <Rcrd>\n"
            f"            <Amt Ccy=\"KRW\">{record_amount}</Amt>\n"
            f"            <CdtDbtInd>{credit_debit_code}</CdtDbtInd>\n"
            "          </Rcrd>\n"
            "        </Intrst>\n"
            "        <NtryDtls>\n",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Build a supported ingest command with an independent idempotency key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"entry-interest-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
