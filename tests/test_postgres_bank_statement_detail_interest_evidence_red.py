"""PostgreSQL REDs for camt.053 transaction-detail interest provenance."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import unittest
import uuid
from decimal import Decimal

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
_DETAIL_INTEREST_PURPOSE = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/Intrst"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailInterestEvidenceRedTests(unittest.TestCase):
    """Preserve TxDtls interest provenance without turning it into accounting policy."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Build independently material TransactionInterest4 variants."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        self.marker = "            </AmtDtls>\n            <RltdPties>"
        self.assertEqual(fixture.count(self.marker), 1)

        self.base_semantics: dict[str, object] = {
            "total_interest_and_tax_amount": {
                "amount": "125.00",
                "currency_code": "KRW",
            },
            "records": [
                {
                    "amount": "100.00",
                    "currency_code": "KRW",
                    "credit_debit_code": "CRDT",
                },
                {
                    "amount": "25.00",
                    "currency_code": "KRW",
                    "credit_debit_code": "CRDT",
                },
            ],
        }
        variant_builders = {
            "total_amount": lambda data: data["total_interest_and_tax_amount"].update(
                {"amount": "126.00"}
            ),
            "total_currency": lambda data: data[
                "total_interest_and_tax_amount"
            ].update({"currency_code": "USD"}),
            "record_0_amount": lambda data: data["records"][0].update(
                {"amount": "100.01"}
            ),
            "record_0_currency": lambda data: data["records"][0].update(
                {"currency_code": "USD"}
            ),
            "record_0_direction": lambda data: data["records"][0].update(
                {"credit_debit_code": "DBIT"}
            ),
            "record_1_amount": lambda data: data["records"][1].update(
                {"amount": "25.01"}
            ),
            "record_1_currency": lambda data: data["records"][1].update(
                {"currency_code": "USD"}
            ),
            "record_1_direction": lambda data: data["records"][1].update(
                {"credit_debit_code": "DBIT"}
            ),
            "record_order": lambda data: data["records"].reverse(),
        }
        self.variants: dict[str, dict[str, object]] = {}
        for name, mutate in variant_builders.items():
            semantics = copy.deepcopy(self.base_semantics)
            mutate(semantics)
            self.variants[name] = semantics

        self.base_payload = self._with_interest(fixture, self.base_semantics)
        self.base_statement = self._parse(self.base_payload)
        self.variant_payloads = {
            name: self._with_interest(fixture, semantics)
            for name, semantics in self.variants.items()
        }
        self.variant_statements = {
            name: self._parse(payload) for name, payload in self.variant_payloads.items()
        }
        self.layout_payload = self._with_interest(
            fixture,
            self.base_semantics,
            compact_first_record=True,
        )
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

    def test_complete_detail_interest_is_material_to_hash_chain(self) -> None:
        """Total, per-record scalars, positions, and order remain material evidence."""
        base_hash = self._expected_hash(self.base_semantics)
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]

        self.assertRegex(base_hash, _HASH_PATTERN)
        self.assertEqual(base_entry.entry_amount, Decimal("25000.00"))
        self.assertEqual(base_entry.entry_currency_code, "KRW")
        self.assertEqual(base_detail.detail_amount, Decimal("25000.00"))
        self.assertEqual(base_detail.detail_currency_code, "KRW")
        self.assertEqual(getattr(base_detail, "interest_evidence", None), self.base_semantics)
        self.assertEqual(
            getattr(base_detail, "detail_interest_evidence_hash", None), base_hash
        )
        self._assert_entry_hash_binding(base_entry, base_hash)

        for name, statement in self.variant_statements.items():
            with self.subTest(name=name):
                semantics = self.variants[name]
                changed_hash = self._expected_hash(semantics)
                changed_entry = statement.entries[0]
                changed_detail = changed_entry.entry_details[0]

                self.assertRegex(changed_hash, _HASH_PATTERN)
                self.assertNotEqual(base_hash, changed_hash)
                self.assertEqual(changed_entry.entry_amount, Decimal("25000.00"))
                self.assertEqual(changed_entry.entry_currency_code, "KRW")
                self.assertEqual(changed_detail.detail_amount, Decimal("25000.00"))
                self.assertEqual(changed_detail.detail_currency_code, "KRW")
                self.assertEqual(
                    getattr(changed_detail, "interest_evidence", None), semantics
                )
                self.assertEqual(
                    getattr(changed_detail, "detail_interest_evidence_hash", None),
                    changed_hash,
                )
                self._assert_entry_hash_binding(changed_entry, changed_hash)
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

    def test_xml_layout_is_not_detail_interest_semantics(self) -> None:
        """Whitespace-only changes alter raw provenance, not interest semantic identity."""
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
            getattr(layout_detail, "interest_evidence", None), self.base_semantics
        )
        self.assertEqual(
            getattr(layout_detail, "detail_interest_evidence_hash", None), expected_hash
        )
        self._assert_entry_hash_binding(layout_entry, expected_hash)
        self.assertEqual(base_detail.source_detail_hash, layout_detail.source_detail_hash)
        self.assertEqual(base_entry.source_entry_hash, layout_entry.source_entry_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.layout_statement.normalized_payload_hash,
        )

    def test_changed_detail_interest_requires_explicit_statement_correction(self) -> None:
        """Accepted bank interest provenance cannot be silently replaced on replay."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        for name, payload in self.variant_payloads.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                    accept_bank_statement_evidence(
                        self._command(payload, name),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_buyer_read_preserves_interest_without_changing_transaction_amount(self) -> None:
        """Tenant reads expose interest evidence while accounting amount stays 25000 KRW."""
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

        self.assertEqual(detail.get("interest_evidence"), self.base_semantics)
        self.assertEqual(detail.get("detail_interest_evidence_hash"), expected_hash)
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
        """Digest source-ordered transaction-detail interest semantics."""
        preimage = json.dumps(
            {"evidence_type": _DETAIL_INTEREST_PURPOSE, "interest_evidence": semantics},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Require source_entry_hash to bind the exact detail-interest purpose digest."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        first_detail = details[0]
        if not isinstance(first_detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if first_detail.get("detail_interest_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry exact detail_interest_evidence_hash"
            )
        preimage = json.dumps(
            projection,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if getattr(entry, "source_entry_hash", None) != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must digest the canonical entry projection containing "
                "detail_interest_evidence_hash"
            )

    def _with_interest(
        self,
        fixture: str,
        semantics: dict[str, object],
        *,
        compact_first_record: bool = False,
    ) -> bytes:
        """Insert TxDtls/Intrst after AmtDtls and before later optional siblings."""
        xml = self._interest_xml(
            semantics,
            compact_first_record=compact_first_record,
        )
        return fixture.replace(
            self.marker,
            "            </AmtDtls>\n" + xml + "            <RltdPties>",
            1,
        ).encode("utf-8")

    @staticmethod
    def _interest_xml(
        semantics: dict[str, object],
        *,
        compact_first_record: bool,
    ) -> str:
        """Render the admitted TransactionInterest4 subset in schema order."""
        total = semantics["total_interest_and_tax_amount"]
        records = semantics["records"]
        if not isinstance(total, dict) or not isinstance(records, list):
            raise AssertionError("interest semantics must contain total and records")

        rendered = [
            "            <Intrst>\n",
            (
                "              <TtlIntrstAndTaxAmt "
                f'Ccy="{total["currency_code"]}">{total["amount"]}'
                "</TtlIntrstAndTaxAmt>\n"
            ),
        ]
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                raise AssertionError("interest record semantics must be a mapping")
            if compact_first_record and index == 0:
                rendered.append(
                    "              <Rcrd>"
                    f'<Amt Ccy="{record["currency_code"]}">{record["amount"]}</Amt>'
                    f'<CdtDbtInd>{record["credit_debit_code"]}</CdtDbtInd>'
                    "</Rcrd>\n"
                )
            else:
                rendered.extend(
                    [
                        "              <Rcrd>\n",
                        (
                            "                <Amt "
                            f'Ccy="{record["currency_code"]}">{record["amount"]}</Amt>\n'
                        ),
                        f"                <CdtDbtInd>{record['credit_debit_code']}</CdtDbtInd>\n",
                        "              </Rcrd>\n",
                    ]
                )
        rendered.append("            </Intrst>\n")
        return "".join(rendered)

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh tenant-scoped replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"detail-interest-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
