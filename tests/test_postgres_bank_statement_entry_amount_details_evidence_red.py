"""PostgreSQL REDs for camt.053 entry amount-detail provenance."""

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
_ENTRY_AMOUNT_DETAILS_PURPOSE = "camt.053.001.14/Stmt/Ntry/AmtDtls"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementEntryAmountDetailsEvidenceRedTests(unittest.TestCase):
    """Retain bank-reported amount details without making them posting truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare source-real statements that isolate one entry AmtDtls semantic."""
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

        self.instructed_payload = self._with_amount_details(
            fixture,
            marker,
            amount_kind="InstdAmt",
            amount="20.00",
            currency_code="USD",
        )
        self.changed_amount_payload = self._with_amount_details(
            fixture,
            marker,
            amount_kind="InstdAmt",
            amount="20.01",
            currency_code="USD",
        )
        self.changed_currency_payload = self._with_amount_details(
            fixture,
            marker,
            amount_kind="InstdAmt",
            amount="20.00",
            currency_code="EUR",
        )
        self.transaction_amount_payload = self._with_amount_details(
            fixture,
            marker,
            amount_kind="TxAmt",
            amount="20.00",
            currency_code="USD",
        )

        formatting_anchor = (
            "        <AmtDtls>\n"
            "          <InstdAmt>\n"
            "            <Amt Ccy=\"USD\">20.00</Amt>\n"
            "          </InstdAmt>\n"
            "        </AmtDtls>\n"
        ).encode("utf-8")
        self.assertEqual(self.instructed_payload.count(formatting_anchor), 1)
        self.reformatted_payload = self.instructed_payload.replace(
            formatting_anchor,
            (
                "        <AmtDtls>\n"
                "          <InstdAmt><Amt Ccy=\"USD\">20.00</Amt></InstdAmt>\n"
                "        </AmtDtls>\n"
            ).encode("utf-8"),
            1,
        )

        self.instructed_statement = parse_bank_statement_payload(
            self.instructed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_amount_statement = parse_bank_statement_payload(
            self.changed_amount_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_currency_statement = parse_bank_statement_payload(
            self.changed_currency_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.transaction_amount_statement = parse_bank_statement_payload(
            self.transaction_amount_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.reformatted_statement = parse_bank_statement_payload(
            self.reformatted_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.instructed_statement.account_currency_code,
                "account_identifier_hash": self.instructed_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_amount_detail_semantics_are_material_entry_evidence(self) -> None:
        """Amount, currency, and AmountAndCurrencyExchange4 discriminator are material."""
        variants = (
            (
                self.instructed_statement,
                self._expected_amount_details_hash("InstdAmt", "20.00", "USD"),
            ),
            (
                self.changed_amount_statement,
                self._expected_amount_details_hash("InstdAmt", "20.01", "USD"),
            ),
            (
                self.changed_currency_statement,
                self._expected_amount_details_hash("InstdAmt", "20.00", "EUR"),
            ),
            (
                self.transaction_amount_statement,
                self._expected_amount_details_hash("TxAmt", "20.00", "USD"),
            ),
        )

        hashes: list[str] = []
        for statement, expected_hash in variants:
            with self.subTest(expected_hash=expected_hash):
                entry = statement.entries[0]
                self.assertRegex(expected_hash, _HASH_PATTERN)
                self.assertEqual(
                    getattr(entry, "entry_amount_details_evidence_hash", None),
                    expected_hash,
                )
                self._assert_entry_hash_binding(entry, expected_hash)
                hashes.append(expected_hash)

        self.assertEqual(len(set(hashes)), len(hashes))
        for changed_statement in (
            self.changed_amount_statement,
            self.changed_currency_statement,
            self.transaction_amount_statement,
        ):
            self.assertEqual(
                self.instructed_statement.account_identifier_hash,
                changed_statement.account_identifier_hash,
            )
            self.assertNotEqual(
                self.instructed_statement.entries[0].source_entry_hash,
                changed_statement.entries[0].source_entry_hash,
            )
            self.assertNotEqual(
                self.instructed_statement.normalized_payload_hash,
                changed_statement.normalized_payload_hash,
            )
            self.assertEqual(
                self.instructed_statement.entries[1].source_entry_hash,
                changed_statement.entries[1].source_entry_hash,
            )

    def test_xml_formatting_does_not_change_amount_detail_semantics(self) -> None:
        """Element layout differences must not alter normalized amount-detail evidence."""
        expected_hash = self._expected_amount_details_hash("InstdAmt", "20.00", "USD")
        first_entry = self.instructed_statement.entries[0]
        reformatted_entry = self.reformatted_statement.entries[0]

        self.assertNotEqual(
            self.instructed_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(first_entry, "entry_amount_details_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(reformatted_entry, "entry_amount_details_evidence_hash", None),
            expected_hash,
        )
        self._assert_entry_hash_binding(first_entry, expected_hash)
        self._assert_entry_hash_binding(reformatted_entry, expected_hash)
        self.assertEqual(first_entry.source_entry_hash, reformatted_entry.source_entry_hash)
        self.assertEqual(
            self.instructed_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_amount_details_require_explicit_statement_correction(self) -> None:
        """A material entry amount-detail change cannot silently replay one statement."""
        accepted = accept_bank_statement_evidence(
            self._command(self.instructed_payload, "instructed"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.changed_amount_payload, "changed-amount"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_preserves_exact_amount_detail_provenance(self) -> None:
        """Supported reads retain exact amount, currency, kind, and purpose digest."""
        expected_hash = self._expected_amount_details_hash("InstdAmt", "20.00", "USD")
        accepted = accept_bank_statement_evidence(
            self._command(self.instructed_payload, "lookup"),
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

        self.assertEqual(entry.get("entry_amount_details_evidence_hash"), expected_hash)
        self.assertEqual(
            entry.get("amount_details_evidence"),
            [
                {
                    "amount_kind": "InstdAmt",
                    "amount": "20.00",
                    "currency_code": "USD",
                }
            ],
        )

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Bind canonical entry identity to semantic amount details, not XML layout."""
        projection = dict(bank_statement._entry_payload(entry))
        if projection.get("entry_amount_details_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry the exact "
                "entry_amount_details_evidence_hash"
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
    def _expected_amount_details_hash(
        amount_kind: str,
        amount: str,
        currency_code: str,
    ) -> str:
        """Digest one complete AmountAndCurrencyExchangeDetails5 semantic record."""
        preimage = json.dumps(
            {
                "evidence_type": _ENTRY_AMOUNT_DETAILS_PURPOSE,
                "amount_details": [
                    {
                        "amount_kind": amount_kind,
                        "amount": amount,
                        "currency_code": currency_code,
                    }
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _with_amount_details(
        fixture: str,
        marker: str,
        *,
        amount_kind: str,
        amount: str,
        currency_code: str,
    ) -> bytes:
        """Insert one entry-level AmountAndCurrencyExchange4 after BkTxCd."""
        if amount_kind not in {"InstdAmt", "TxAmt"}:
            raise AssertionError("focused RED supports InstdAmt or TxAmt only")
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
            "        <AmtDtls>\n"
            f"          <{amount_kind}>\n"
            f"            <Amt Ccy=\"{currency_code}\">{amount}</Amt>\n"
            f"          </{amount_kind}>\n"
            "        </AmtDtls>\n"
            "        <NtryDtls>\n",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"entry-amount-details-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
