"""PostgreSQL REDs for camt.053 transaction-detail related-date evidence."""

from __future__ import annotations

import copy
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
_RELATED_DATES_PATH = "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/RltdDts"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailRelatedDatesEvidenceRedTests(unittest.TestCase):
    """Retain bank-reported transaction dates as evidence, never posting authority."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Build schema-valid V14 related-date variants with isolated material changes."""
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

        self.base_semantics: dict[str, object] = {
            "acceptance_datetime": "2026-08-23T09:00:00+00:00",
            "trade_activity_contractual_settlement_date": "2026-08-26",
            "trade_date": "2026-08-22",
            "interbank_settlement_date": "2026-08-23",
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
            "transaction_datetime": "2026-08-23T10:00:00+00:00",
            "proprietary_dates": [
                {
                    "type": "BANK_BOOKING_CUTOFF",
                    "date": {
                        "choice": "DtTm",
                        "value": "2026-08-23T16:00:00+00:00",
                    },
                },
                {
                    "type": "BANK_VALUE_OVERRIDE",
                    "date": {"choice": "Dt", "value": "2026-08-24"},
                },
            ],
        }
        variants = {
            "acceptance_datetime": copy.deepcopy(self.base_semantics),
            "trade_activity_contractual_settlement_date": copy.deepcopy(
                self.base_semantics
            ),
            "trade_date": copy.deepcopy(self.base_semantics),
            "interbank_settlement_date": copy.deepcopy(self.base_semantics),
            "start_date": copy.deepcopy(self.base_semantics),
            "end_date": copy.deepcopy(self.base_semantics),
            "transaction_datetime": copy.deepcopy(self.base_semantics),
            "proprietary_type": copy.deepcopy(self.base_semantics),
            "proprietary_date_value": copy.deepcopy(self.base_semantics),
            "proprietary_date_choice": copy.deepcopy(self.base_semantics),
            "proprietary_order": copy.deepcopy(self.base_semantics),
        }
        variants["acceptance_datetime"]["acceptance_datetime"] = (
            "2026-08-23T09:05:00+00:00"
        )
        variants["trade_activity_contractual_settlement_date"][
            "trade_activity_contractual_settlement_date"
        ] = "2026-08-27"
        variants["trade_date"]["trade_date"] = "2026-08-21"
        variants["interbank_settlement_date"]["interbank_settlement_date"] = (
            "2026-08-24"
        )
        variants["start_date"]["start_date"] = "2026-08-02"
        variants["end_date"]["end_date"] = "2026-09-01"
        variants["transaction_datetime"]["transaction_datetime"] = (
            "2026-08-23T10:05:00+00:00"
        )
        variants["proprietary_type"]["proprietary_dates"][0]["type"] = (
            "BANK_SETTLEMENT_CUTOFF"
        )
        variants["proprietary_date_value"]["proprietary_dates"][0]["date"][
            "value"
        ] = "2026-08-23T16:05:00+00:00"
        variants["proprietary_date_choice"]["proprietary_dates"][1]["date"] = {
            "choice": "DtTm",
            "value": "2026-08-24T00:00:00+00:00",
        }
        variants["proprietary_order"]["proprietary_dates"].reverse()
        self.variants = variants

        self.base_payload = self._with_related_dates(fixture, self.base_semantics)
        self.base_statement = self._parse(self.base_payload)
        self.variant_payloads = {
            name: self._with_related_dates(fixture, semantics)
            for name, semantics in self.variants.items()
        }
        self.variant_statements = {
            name: self._parse(payload) for name, payload in self.variant_payloads.items()
        }
        self.layout_payload = self._with_related_dates(
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

    def test_all_v14_related_date_fields_are_material_to_hash_chain(self) -> None:
        """Every admitted TransactionDates3 scalar and proprietary date stays material."""
        base_hash = self._expected_hash(self.base_semantics)
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]

        self.assertRegex(base_hash, _HASH_PATTERN)
        self.assertEqual(getattr(base_detail, "related_dates", None), self.base_semantics)
        self.assertEqual(
            getattr(base_detail, "related_dates_evidence_hash", None), base_hash
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
                self.assertEqual(
                    getattr(changed_detail, "related_dates", None), semantics
                )
                self.assertEqual(
                    getattr(changed_detail, "related_dates_evidence_hash", None),
                    changed_hash,
                )
                self._assert_entry_hash_binding(changed_entry, changed_hash)
                self._assert_accounting_amount_unchanged(
                    self.base_statement, statement
                )
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

    def test_xml_layout_is_not_related_date_semantics(self) -> None:
        """Whitespace-only XML changes raw provenance, not related-date semantics."""
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
            getattr(layout_detail, "related_dates", None), self.base_semantics
        )
        self.assertEqual(
            getattr(layout_detail, "related_dates_evidence_hash", None), expected_hash
        )
        self._assert_entry_hash_binding(layout_entry, expected_hash)
        self.assertEqual(base_detail.source_detail_hash, layout_detail.source_detail_hash)
        self.assertEqual(base_entry.source_entry_hash, layout_entry.source_entry_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.layout_statement.normalized_payload_hash,
        )

    def test_changed_related_date_requires_explicit_statement_correction(self) -> None:
        """Accepted related-date evidence cannot be silently replaced on replay."""
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
                    self.variant_payloads["transaction_datetime"],
                    "changed-transaction-datetime",
                ),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_preserves_related_dates_without_deriving_accounting_time(self) -> None:
        """Tenant reads expose date evidence without changing booked accounting facts."""
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

        self.assertEqual(detail.get("related_dates"), self.base_semantics)
        self.assertEqual(detail.get("related_dates_evidence_hash"), expected_hash)
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
        """Digest related-date semantics under their exact evidence purpose."""
        preimage = json.dumps(
            {"evidence_type": _RELATED_DATES_PATH, "related_dates": semantics},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Require source_entry_hash to digest the date-bound canonical projection."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        first_detail = details[0]
        if not isinstance(first_detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if first_detail.get("related_dates_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry exact related_dates_evidence_hash"
            )
        preimage = json.dumps(
            projection, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if getattr(entry, "source_entry_hash", None) != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must digest the canonical entry projection containing "
                "related_dates_evidence_hash"
            )

    @staticmethod
    def _assert_accounting_amount_unchanged(base: object, changed: object) -> None:
        """Keep reported accounting value independent from transaction-date evidence."""
        base_entry = base.entries[0]
        changed_entry = changed.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]
        if base_entry.entry_amount != changed_entry.entry_amount:
            raise AssertionError("related dates must not change the entry amount")
        if base_detail.detail_amount != changed_detail.detail_amount:
            raise AssertionError("related dates must not change the detail amount")
        if base_entry.entry_currency_code != changed_entry.entry_currency_code:
            raise AssertionError("related dates must not change the entry currency")
        if base_detail.detail_currency_code != changed_detail.detail_currency_code:
            raise AssertionError("related dates must not change the detail currency")

    def _with_related_dates(
        self,
        fixture: str,
        semantics: dict[str, object],
        *,
        compact: bool = False,
    ) -> bytes:
        """Insert V14 RltdDts after RmtInf and before later optional siblings."""
        xml = self._related_dates_xml(semantics, compact=compact)
        replacement = f"{self.marker}\n{xml}"
        self.assertEqual(fixture.count(self.marker), 1)
        return fixture.replace(self.marker, replacement, 1).encode("utf-8")

    @staticmethod
    def _related_dates_xml(semantics: dict[str, object], *, compact: bool) -> str:
        """Render TransactionDates3 in exact XSD sequence, including ProprietaryDate3."""
        tag_by_key = (
            ("acceptance_datetime", "AccptncDtTm"),
            (
                "trade_activity_contractual_settlement_date",
                "TradActvtyCtrctlSttlmDt",
            ),
            ("trade_date", "TradDt"),
            ("interbank_settlement_date", "IntrBkSttlmDt"),
            ("start_date", "StartDt"),
            ("end_date", "EndDt"),
            ("transaction_datetime", "TxDtTm"),
        )
        children = [
            f"<{tag}>{semantics[key]}</{tag}>" for key, tag in tag_by_key
        ]
        for proprietary in semantics["proprietary_dates"]:
            date = proprietary["date"]
            children.append(
                "<Prtry>"
                f"<Tp>{proprietary['type']}</Tp>"
                "<Dt>"
                f"<{date['choice']}>{date['value']}</{date['choice']}>"
                "</Dt>"
                "</Prtry>"
            )
        if compact:
            return f"            <RltdDts>{''.join(children)}</RltdDts>"
        rendered = "\n".join(f"              {child}" for child in children)
        return "            <RltdDts>\n" f"{rendered}\n" "            </RltdDts>"

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh tenant-scoped replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"related-dates-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
