"""PostgreSQL REDs for complete camt.053 transaction-detail amount evidence."""

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
_AMOUNT_DETAILS_PATH = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/AmtDtls"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailAmountDetailsEvidenceRedTests(unittest.TestCase):
    """Preserve V14 amount/exchange provenance without deriving accounting value."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Build schema-valid AmountAndCurrencyExchange4 variants."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        self.marker = (
            "            <AmtDtls>\n"
            "              <TxAmt>\n"
            '                <Amt Ccy="KRW">25000.00</Amt>\n'
            "              </TxAmt>\n"
            "            </AmtDtls>"
        )
        self.assertEqual(fixture.count(self.marker), 1)

        self.base_semantics: dict[str, object] = {
            "instructed_amount": {
                "amount": "18.75",
                "currency_code": "USD",
                "currency_exchange": {
                    "source_currency_code": "USD",
                    "target_currency_code": "KRW",
                    "unit_currency_code": "USD",
                    "exchange_rate": "1333.333333",
                    "contract_identification": "FX-CONTRACT-001",
                    "quotation_datetime": "2026-08-23T08:55:00+00:00",
                    "exchange_rate_base": "1",
                },
            },
            "transaction_amount": {
                "amount": "25000.00",
                "currency_code": "KRW",
                "currency_exchange": None,
            },
            "countervalue_amount": {
                "amount": "18.75",
                "currency_code": "USD",
                "currency_exchange": None,
            },
            "announced_posting_amount": {
                "amount": "25001.00",
                "currency_code": "KRW",
                "currency_exchange": None,
            },
            "proprietary_amounts": [
                {
                    "type": "BANK_GROSS",
                    "amount": "18.80",
                    "currency_code": "USD",
                    "currency_exchange": None,
                },
                {
                    "type": "BANK_NET",
                    "amount": "18.70",
                    "currency_code": "USD",
                    "currency_exchange": None,
                },
            ],
        }

        variant_builders = {
            "instructed_amount_value": lambda data: data["instructed_amount"].update(
                {"amount": "18.76"}
            ),
            "instructed_amount_currency": lambda data: data["instructed_amount"].update(
                {"currency_code": "EUR"}
            ),
            "exchange_source_currency": lambda data: data["instructed_amount"][
                "currency_exchange"
            ].update({"source_currency_code": "EUR"}),
            "exchange_target_currency": lambda data: data["instructed_amount"][
                "currency_exchange"
            ].update({"target_currency_code": "JPY"}),
            "exchange_unit_currency": lambda data: data["instructed_amount"][
                "currency_exchange"
            ].update({"unit_currency_code": "EUR"}),
            "exchange_rate": lambda data: data["instructed_amount"][
                "currency_exchange"
            ].update({"exchange_rate": "1333.333334"}),
            "exchange_contract_identification": lambda data: data["instructed_amount"][
                "currency_exchange"
            ].update({"contract_identification": "FX-CONTRACT-002"}),
            "exchange_quotation_datetime": lambda data: data["instructed_amount"][
                "currency_exchange"
            ].update({"quotation_datetime": "2026-08-23T08:56:00+00:00"}),
            "exchange_rate_base": lambda data: data["instructed_amount"][
                "currency_exchange"
            ].update({"exchange_rate_base": "100"}),
            "countervalue_amount_value": lambda data: data["countervalue_amount"].update(
                {"amount": "18.76"}
            ),
            "announced_posting_amount_value": lambda data: data[
                "announced_posting_amount"
            ].update({"amount": "25002.00"}),
            "proprietary_type": lambda data: data["proprietary_amounts"][0].update(
                {"type": "BANK_SETTLEMENT_GROSS"}
            ),
            "proprietary_amount_value": lambda data: data["proprietary_amounts"][
                1
            ].update({"amount": "18.71"}),
            "proprietary_amount_currency": lambda data: data["proprietary_amounts"][
                1
            ].update({"currency_code": "EUR"}),
            "proprietary_order": lambda data: data["proprietary_amounts"].reverse(),
        }
        self.variants: dict[str, dict[str, object]] = {}
        for name, mutate in variant_builders.items():
            semantics = copy.deepcopy(self.base_semantics)
            mutate(semantics)
            self.variants[name] = semantics

        self.base_payload = self._with_amount_details(fixture, self.base_semantics)
        self.base_statement = self._parse(self.base_payload)
        self.variant_payloads = {
            name: self._with_amount_details(fixture, semantics)
            for name, semantics in self.variants.items()
        }
        self.variant_statements = {
            name: self._parse(payload) for name, payload in self.variant_payloads.items()
        }
        self.layout_payload = self._with_amount_details(
            fixture, self.base_semantics, compact=True
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

    def test_complete_amount_details_are_material_to_hash_chain(self) -> None:
        """Every admitted sibling, nested FX scalar, and proprietary order stays material."""
        base_hash = self._expected_hash(self.base_semantics)
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]

        self.assertRegex(base_hash, _HASH_PATTERN)
        self.assertEqual(base_entry.entry_amount, Decimal("25000.00"))
        self.assertEqual(base_entry.entry_currency_code, "KRW")
        self.assertEqual(base_detail.detail_amount, Decimal("25000.00"))
        self.assertEqual(base_detail.detail_currency_code, "KRW")
        self.assertEqual(getattr(base_detail, "amount_details", None), self.base_semantics)
        self.assertEqual(
            getattr(base_detail, "amount_details_evidence_hash", None), base_hash
        )
        self._assert_entry_hash_binding(base_entry, base_hash)

        for name, statement in self.variant_statements.items():
            semantics = self.variants[name]
            with self.subTest(name=name):
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
                    getattr(changed_detail, "amount_details", None), semantics
                )
                self.assertEqual(
                    getattr(changed_detail, "amount_details_evidence_hash", None),
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

    def test_xml_layout_is_not_amount_detail_semantics(self) -> None:
        """Whitespace-only XML changes raw provenance, not amount-detail semantics."""
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
            getattr(layout_detail, "amount_details", None), self.base_semantics
        )
        self.assertEqual(
            getattr(layout_detail, "amount_details_evidence_hash", None), expected_hash
        )
        self._assert_entry_hash_binding(layout_entry, expected_hash)
        self.assertEqual(base_detail.source_detail_hash, layout_detail.source_detail_hash)
        self.assertEqual(base_entry.source_entry_hash, layout_entry.source_entry_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.layout_statement.normalized_payload_hash,
        )

    def test_changed_amount_detail_requires_explicit_statement_correction(self) -> None:
        """Accepted bank amount provenance cannot be silently replaced on replay."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(
                    self.variant_payloads["exchange_contract_identification"],
                    "changed-exchange-contract",
                ),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_preserves_amount_details_without_revaluing_entry(self) -> None:
        """Tenant reads expose source amounts without deriving a new accounting amount."""
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

        self.assertEqual(detail.get("amount_details"), self.base_semantics)
        self.assertEqual(detail.get("amount_details_evidence_hash"), expected_hash)
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
        """Digest all AmountAndCurrencyExchange4 evidence under one exact purpose."""
        preimage = json.dumps(
            {"evidence_type": _AMOUNT_DETAILS_PATH, "amount_details": semantics},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Require source_entry_hash to digest the amount-details-bound projection."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        first_detail = details[0]
        if not isinstance(first_detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if first_detail.get("amount_details_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry exact amount_details_evidence_hash"
            )
        preimage = json.dumps(
            projection, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if getattr(entry, "source_entry_hash", None) != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must digest the canonical entry projection containing "
                "amount_details_evidence_hash"
            )

    def _with_amount_details(
        self,
        fixture: str,
        semantics: dict[str, object],
        *,
        compact: bool = False,
    ) -> bytes:
        """Replace canonical TxAmt-only AmtDtls with full V14 admitted siblings."""
        xml = self._amount_details_xml(semantics, compact=compact)
        self.assertEqual(fixture.count(self.marker), 1)
        return fixture.replace(self.marker, xml, 1).encode("utf-8")

    @classmethod
    def _amount_details_xml(
        cls, semantics: dict[str, object], *, compact: bool
    ) -> str:
        """Render AmountAndCurrencyExchange4 in schema order."""
        children = [
            cls._amount_branch_xml("InstdAmt", semantics["instructed_amount"]),
            cls._amount_branch_xml("TxAmt", semantics["transaction_amount"]),
            cls._amount_branch_xml("CntrValAmt", semantics["countervalue_amount"]),
            cls._amount_branch_xml(
                "AnncdPstngAmt", semantics["announced_posting_amount"]
            ),
        ]
        children.extend(
            cls._proprietary_amount_xml(item)
            for item in semantics["proprietary_amounts"]
        )
        if compact:
            return f"            <AmtDtls>{''.join(children)}</AmtDtls>"
        rendered = "\n".join(cls._indent_xml(child, 14) for child in children)
        return "            <AmtDtls>\n" f"{rendered}\n" "            </AmtDtls>"

    @classmethod
    def _amount_branch_xml(cls, tag: str, branch: object) -> str:
        """Render AmountAndCurrencyExchangeDetails5."""
        if not isinstance(branch, dict):
            raise AssertionError(f"{tag} semantics must be a mapping")
        amount = (
            f'<Amt Ccy="{branch["currency_code"]}">{branch["amount"]}</Amt>'
        )
        exchange = branch.get("currency_exchange")
        exchange_xml = "" if exchange is None else cls._currency_exchange_xml(exchange)
        return f"<{tag}>{amount}{exchange_xml}</{tag}>"

    @classmethod
    def _proprietary_amount_xml(cls, branch: object) -> str:
        """Render AmountAndCurrencyExchangeDetails6 with its required type."""
        if not isinstance(branch, dict):
            raise AssertionError("proprietary amount semantics must be a mapping")
        amount = (
            f'<Amt Ccy="{branch["currency_code"]}">{branch["amount"]}</Amt>'
        )
        exchange = branch.get("currency_exchange")
        exchange_xml = "" if exchange is None else cls._currency_exchange_xml(exchange)
        return (
            "<PrtryAmt>"
            f"<Tp>{branch['type']}</Tp>"
            f"{amount}"
            f"{exchange_xml}"
            "</PrtryAmt>"
        )

    @staticmethod
    def _currency_exchange_xml(exchange: object) -> str:
        """Render CurrencyExchange24 in exact V14 sequence."""
        if not isinstance(exchange, dict):
            raise AssertionError("currency exchange semantics must be a mapping")
        return (
            "<CcyXchg>"
            f"<SrcCcy>{exchange['source_currency_code']}</SrcCcy>"
            f"<TrgtCcy>{exchange['target_currency_code']}</TrgtCcy>"
            f"<UnitCcy>{exchange['unit_currency_code']}</UnitCcy>"
            f"<XchgRate>{exchange['exchange_rate']}</XchgRate>"
            f"<CtrctId>{exchange['contract_identification']}</CtrctId>"
            f"<QtnDt>{exchange['quotation_datetime']}</QtnDt>"
            f"<XchgRateBase>{exchange['exchange_rate_base']}</XchgRateBase>"
            "</CcyXchg>"
        )

    @staticmethod
    def _indent_xml(xml: str, spaces: int) -> str:
        """Indent one compact XML child without changing its logical content."""
        return f"{' ' * spaces}{xml}"

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh tenant-scoped replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"amount-details-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
