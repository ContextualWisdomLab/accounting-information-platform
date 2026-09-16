"""PostgreSQL REDs for camt.053 transaction-detail card-transaction evidence."""

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
_DETAIL_CARD_TRANSACTION_PATH = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/CardTx"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailCardTransactionEvidenceRedTests(unittest.TestCase):
    """Retain non-sensitive detail CardTx provenance without granting accounting authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Build schema-ordered V14 detail CardTx variants with isolated changes."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        self.marker = (
            "            <RmtInf>\n"
            "              <Ustrd>Invoice 1001</Ustrd>\n"
            "            </RmtInf>"
        )
        self.assertEqual(fixture.count(self.marker), 1)

        self.base_semantics = {
            "transaction": {
                "choice": "Indv",
                "payment_context": {
                    "card_data_entry_mode": "CICC",
                    "fallback_indicator": False,
                },
                "transaction_identifier": "CARD-TX-001",
            }
        }
        self.entry_mode_semantics = {
            "transaction": {
                "choice": "Indv",
                "payment_context": {
                    "card_data_entry_mode": "CTLS",
                    "fallback_indicator": False,
                },
                "transaction_identifier": "CARD-TX-001",
            }
        }
        self.fallback_semantics = {
            "transaction": {
                "choice": "Indv",
                "payment_context": {
                    "card_data_entry_mode": "CICC",
                    "fallback_indicator": True,
                },
                "transaction_identifier": "CARD-TX-001",
            }
        }
        self.identifier_semantics = {
            "transaction": {
                "choice": "Indv",
                "payment_context": {
                    "card_data_entry_mode": "CICC",
                    "fallback_indicator": False,
                },
                "transaction_identifier": "CARD-TX-002",
            }
        }

        self.base_payload = self._with_detail_card_transaction(
            fixture, self.base_semantics
        )
        self.entry_mode_payload = self._with_detail_card_transaction(
            fixture, self.entry_mode_semantics
        )
        self.fallback_payload = self._with_detail_card_transaction(
            fixture, self.fallback_semantics
        )
        self.identifier_payload = self._with_detail_card_transaction(
            fixture, self.identifier_semantics
        )
        self.layout_payload = self._with_detail_card_transaction(
            fixture, self.base_semantics, compact=True
        )

        self.base_statement = self._parse(self.base_payload)
        self.entry_mode_statement = self._parse(self.entry_mode_payload)
        self.fallback_statement = self._parse(self.fallback_payload)
        self.identifier_statement = self._parse(self.identifier_payload)
        self.layout_statement = self._parse(self.layout_payload)

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

    def test_detail_card_transaction_fields_are_material_to_hash_chain(self) -> None:
        """Entry mode, fallback flag, and card transaction ID stay material evidence."""
        base_hash = self._expected_hash(self.base_semantics)
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]

        self.assertRegex(base_hash, _HASH_PATTERN)
        self.assertEqual(
            getattr(base_detail, "card_transaction_evidence", None), self.base_semantics
        )
        self.assertEqual(
            getattr(base_detail, "card_transaction_evidence_hash", None), base_hash
        )
        self._assert_entry_hash_binding(base_entry, base_hash)

        for name, statement, semantics in (
            ("entry-mode", self.entry_mode_statement, self.entry_mode_semantics),
            ("fallback", self.fallback_statement, self.fallback_semantics),
            ("transaction-id", self.identifier_statement, self.identifier_semantics),
        ):
            with self.subTest(name=name):
                changed_hash = self._expected_hash(semantics)
                changed_entry = statement.entries[0]
                changed_detail = changed_entry.entry_details[0]
                self.assertRegex(changed_hash, _HASH_PATTERN)
                self.assertNotEqual(base_hash, changed_hash)
                self.assertEqual(
                    getattr(changed_detail, "card_transaction_evidence", None), semantics
                )
                self.assertEqual(
                    getattr(changed_detail, "card_transaction_evidence_hash", None),
                    changed_hash,
                )
                self._assert_entry_hash_binding(changed_entry, changed_hash)
                self._assert_accounting_amount_unchanged(self.base_statement, statement)
                self.assertNotEqual(
                    base_detail.source_detail_hash, changed_detail.source_detail_hash
                )
                self.assertNotEqual(
                    base_entry.source_entry_hash, changed_entry.source_entry_hash
                )
                self.assertNotEqual(
                    self.base_statement.normalized_payload_hash,
                    statement.normalized_payload_hash,
                )
                self.assertEqual(
                    self.base_statement.entries[1].source_entry_hash,
                    statement.entries[1].source_entry_hash,
                )

    def test_xml_layout_is_not_detail_card_transaction_semantics(self) -> None:
        """Whitespace-only XML layout changes raw provenance, not CardTx semantics."""
        expected_hash = self._expected_hash(self.base_semantics)
        base_entry = self.base_statement.entries[0]
        layout_entry = self.layout_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        layout_detail = layout_entry.entry_details[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.layout_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(layout_detail, "card_transaction_evidence", None), self.base_semantics
        )
        self.assertEqual(
            getattr(layout_detail, "card_transaction_evidence_hash", None), expected_hash
        )
        self._assert_entry_hash_binding(layout_entry, expected_hash)
        self.assertEqual(base_detail.source_detail_hash, layout_detail.source_detail_hash)
        self.assertEqual(base_entry.source_entry_hash, layout_entry.source_entry_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.layout_statement.normalized_payload_hash,
        )

    def test_changed_detail_card_transaction_requires_explicit_correction(self) -> None:
        """Accepted CardTx evidence cannot be silently replaced on statement replay."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.entry_mode_payload, "changed-entry-mode"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_preserves_detail_card_transaction_without_deriving_amount(self) -> None:
        """Tenant reads expose CardTx evidence without creating posting or value truth."""
        expected_hash = self._expected_hash(self.base_semantics)
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
        detail = entry["entry_details"][0]

        self.assertEqual(detail.get("card_transaction_evidence"), self.base_semantics)
        self.assertEqual(detail.get("card_transaction_evidence_hash"), expected_hash)
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    @staticmethod
    def _parse(payload: bytes) -> object:
        """Parse one V14 statement through the supported adapter boundary."""
        return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    @staticmethod
    def _expected_hash(semantics: dict[str, object]) -> str:
        """Digest detail CardTx semantics under their exact evidence purpose."""
        preimage = json.dumps(
            {
                "evidence_type": _DETAIL_CARD_TRANSACTION_PATH,
                "card_transaction": semantics,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Require source_entry_hash to digest the CardTx-bound detail projection."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        first_detail = details[0]
        if not isinstance(first_detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if first_detail.get("card_transaction_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry exact card_transaction_evidence_hash"
            )
        preimage = json.dumps(
            projection, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if getattr(entry, "source_entry_hash", None) != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must digest the canonical entry projection containing "
                "card_transaction_evidence_hash"
            )

    @staticmethod
    def _assert_accounting_amount_unchanged(base: object, changed: object) -> None:
        """Keep bank-reported CardTx provenance independent from accounting value."""
        base_entry = base.entries[0]
        changed_entry = changed.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]
        if base_entry.entry_amount != changed_entry.entry_amount:
            raise AssertionError("CardTx evidence must not change the entry amount")
        if base_detail.detail_amount != changed_detail.detail_amount:
            raise AssertionError("CardTx evidence must not change the detail amount")
        if base_entry.entry_currency_code != changed_entry.entry_currency_code:
            raise AssertionError("CardTx evidence must not change the entry currency")
        if base_detail.detail_currency_code != changed_detail.detail_currency_code:
            raise AssertionError("CardTx evidence must not change the detail currency")

    def _with_detail_card_transaction(
        self,
        fixture: str,
        semantics: dict[str, object],
        *,
        compact: bool = False,
    ) -> bytes:
        """Insert V14 TxDtls/CardTx after earlier siblings and before InstrCpy/AddtlTxInf."""
        card_xml = self._detail_card_transaction_xml(semantics, compact=compact)
        replacement = f"{self.marker}\n{card_xml}"
        self.assertEqual(fixture.count(self.marker), 1)
        return fixture.replace(self.marker, replacement, 1).encode("utf-8")

    @staticmethod
    def _detail_card_transaction_xml(
        semantics: dict[str, object], *, compact: bool
    ) -> str:
        """Render the admitted non-sensitive Tx/Indv/PmtCntxt CardTx projection."""
        transaction = semantics["transaction"]
        if not isinstance(transaction, dict) or transaction.get("choice") != "Indv":
            raise AssertionError("focused RED supports the individual CardTx branch only")
        context = transaction["payment_context"]
        if not isinstance(context, dict):
            raise AssertionError("payment_context must be a mapping")
        entry_mode = context["card_data_entry_mode"]
        fallback = "true" if context["fallback_indicator"] else "false"
        transaction_identifier = transaction["transaction_identifier"]
        if compact:
            return (
                "            <CardTx><Tx><Indv><PmtCntxt>"
                f"<CardDataNtryMd>{entry_mode}</CardDataNtryMd>"
                f"<FllbckInd>{fallback}</FllbckInd>"
                "</PmtCntxt>"
                f"<TxId>{transaction_identifier}</TxId>"
                "</Indv></Tx></CardTx>"
            )
        return (
            "            <CardTx>\n"
            "              <Tx>\n"
            "                <Indv>\n"
            "                  <PmtCntxt>\n"
            f"                    <CardDataNtryMd>{entry_mode}</CardDataNtryMd>\n"
            f"                    <FllbckInd>{fallback}</FllbckInd>\n"
            "                  </PmtCntxt>\n"
            f"                  <TxId>{transaction_identifier}</TxId>\n"
            "                </Indv>\n"
            "              </Tx>\n"
            "            </CardTx>"
        )

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Build a supported ingest command with a fresh tenant-scoped replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"detail-card-transaction-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
