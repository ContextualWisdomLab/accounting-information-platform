"""REDs for structured referred-document line issuer preservation."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
import uuid
from datetime import timezone
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
from tests import test_postgres_posting as posting

_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)
_STRUCTURED_EVIDENCE_KEY = "structured_referred_document_evidence"


class BankStatementStructuredLineIssuerEvidenceRedTests(unittest.TestCase):
    """Retain LineDtls identification issuer as reconciliation source evidence."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare statements that differ only in one line identification issuer."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "<Ustrd>Invoice 1001</Ustrd>"
        if fixture.count(marker) != 1:
            raise AssertionError("canonical Ustrd marker must occur exactly once")

        self.document_number = "INV-2026-1001"
        self.line_related_date = "2026-09-01"
        self.first_line_number = "Stockitem1"
        self.second_line_number = "Stockitem2"
        self.line_type_issuer = "ISO"
        self.changed_line_type_issuer = "ISO-LOCAL"

        self.base_payload = self._with_line_issuer(
            fixture,
            marker,
            self.line_type_issuer,
        )
        self.changed_payload = self._with_line_issuer(
            fixture,
            marker,
            self.changed_line_type_issuer,
        )
        self.base_statement = parse_bank_statement_payload(
            self.base_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_statement = parse_bank_statement_payload(
            self.changed_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        self.bank_account_reference = (
            f"urn:cwl:bank_account:structured-line-issuer:{uuid.uuid4().hex}"
        )
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

    def test_line_issuer_is_material_to_each_canonical_evidence_hash(self) -> None:
        """Bind the line-type issuer to detail, entry, and statement evidence identity."""
        self.assertEqual(
            self.changed_payload.decode("utf-8").replace(
                self.changed_line_type_issuer,
                self.line_type_issuer,
            ),
            self.base_payload.decode("utf-8"),
        )

        base_entry = self.base_statement.entries[0]
        changed_entry = self.changed_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        changed_detail = changed_entry.entry_details[0]
        base_projection = self._structured_projection(self.line_type_issuer)
        changed_projection = self._structured_projection(self.changed_line_type_issuer)

        self.assertNotEqual(base_projection, changed_projection)
        for value in (
            base_detail.source_detail_hash,
            changed_detail.source_detail_hash,
            base_entry.source_entry_hash,
            changed_entry.source_entry_hash,
            self.base_statement.normalized_payload_hash,
            self.changed_statement.normalized_payload_hash,
            self.base_statement.account_identifier_hash,
            self.changed_statement.account_identifier_hash,
            self.base_statement.entries[1].source_entry_hash,
            self.changed_statement.entries[1].source_entry_hash,
        ):
            self._assert_sha256(value)

        self.assertEqual(
            base_detail.source_detail_hash,
            self._expected_detail_hash(base_detail, base_projection),
        )
        self.assertEqual(
            changed_detail.source_detail_hash,
            self._expected_detail_hash(changed_detail, changed_projection),
        )
        self.assertEqual(
            base_entry.source_entry_hash,
            self._expected_entry_hash(base_entry, {1: base_projection}),
        )
        self.assertEqual(
            changed_entry.source_entry_hash,
            self._expected_entry_hash(changed_entry, {1: changed_projection}),
        )
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self._expected_statement_hash(
                self.base_statement,
                {(1, 1): base_projection},
            ),
        )
        self.assertEqual(
            self.changed_statement.normalized_payload_hash,
            self._expected_statement_hash(
                self.changed_statement,
                {(1, 1): changed_projection},
            ),
        )

        self.assertNotEqual(base_detail.source_detail_hash, changed_detail.source_detail_hash)
        self.assertNotEqual(base_entry.source_entry_hash, changed_entry.source_entry_hash)
        self.assertNotEqual(
            self.base_statement.normalized_payload_hash,
            self.changed_statement.normalized_payload_hash,
        )
        self.assertEqual(
            self.base_statement.account_identifier_hash,
            self.changed_statement.account_identifier_hash,
        )
        self.assertEqual(
            self.base_statement.entries[1].source_entry_hash,
            self.changed_statement.entries[1].source_entry_hash,
        )
        self.assertEqual(
            self.base_statement.entries[1].source_entry_hash,
            self._expected_entry_hash(self.base_statement.entries[1], {}),
        )
        self._assert_exact_transaction_amount(base_entry, base_detail)
        self._assert_exact_transaction_amount(changed_entry, changed_detail)

    def test_changed_line_issuer_requires_explicit_statement_correction(self) -> None:
        """Issuer-only source changes cannot silently replay one statement identity."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "line-issuer-base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.changed_payload, "line-issuer-changed"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_keeps_line_type_issuer_bound_to_each_line(self) -> None:
        """Buyer reads retain issuer with the line type and number it qualifies."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "line-issuer-lookup"),
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

        self.assertEqual(
            detail.get(_STRUCTURED_EVIDENCE_KEY),
            self._structured_projection(self.line_type_issuer),
        )
        self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
        self.assertEqual(detail["detail_currency_code"], "KRW")

    def _structured_projection(self, second_line_issuer: str) -> list[dict[str, object]]:
        """Return the ordered buyer/hash projection with issuer inside line identity."""
        return [
            {
                "document_type_code": "CINV",
                "document_number": self.document_number,
                "related_date": self.line_related_date,
                "line_details": [
                    {
                        "line_type_code": "SKNB",
                        "line_type_issuer": self.line_type_issuer,
                        "line_number": self.first_line_number,
                        "related_date": self.line_related_date,
                        "description": "Annual support service",
                        "due_payable_amount": {
                            "amount": "10000.05",
                            "currency_code": "KRW",
                        },
                        "discount_applied_amounts": [
                            {
                                "type_code": "APDS",
                                "amount": "300",
                                "currency_code": "KRW",
                            }
                        ],
                        "remitted_amount": {
                            "amount": "9700.05",
                            "currency_code": "KRW",
                        },
                    },
                    {
                        "line_type_code": "SKNB",
                        "line_type_issuer": second_line_issuer,
                        "line_number": self.second_line_number,
                        "related_date": self.line_related_date,
                        "description": "Quarterly data service",
                        "due_payable_amount": {
                            "amount": "5100.1",
                            "currency_code": "KRW",
                        },
                        "discount_applied_amounts": [
                            {
                                "type_code": "APDS",
                                "amount": "100",
                                "currency_code": "KRW",
                            }
                        ],
                        "remitted_amount": {
                            "amount": "5000.1",
                            "currency_code": "KRW",
                        },
                    },
                ],
            }
        ]

    def _expected_detail_hash(
        self,
        detail: object,
        projection: list[dict[str, object]],
    ) -> str:
        """Hash the existing detail identity plus the ordered structured projection."""
        payload: dict[str, object] = {
            "locator": getattr(detail, "source_locator_path"),
            "amount": self._decimal_text(getattr(detail, "detail_amount")),
            "currency": getattr(detail, "detail_currency_code"),
            "credit_debit": getattr(detail, "credit_debit_code"),
            "end_to_end": getattr(detail, "end_to_end_reference"),
            "remittance": getattr(detail, "remittance_evidence_text"),
        }
        if projection:
            payload[_STRUCTURED_EVIDENCE_KEY] = projection
        return self._hash_json(payload)

    def _expected_entry_hash(
        self,
        entry: object,
        projections: dict[int, list[dict[str, object]]],
    ) -> str:
        """Hash an entry with structured projection bound to each detail position."""
        return self._hash_json(self._entry_payload(entry, projections))

    def _expected_statement_hash(
        self,
        statement: object,
        projections: dict[tuple[int, int], list[dict[str, object]]],
    ) -> str:
        """Hash the statement with the same ordered projection carried through entries."""
        entries: list[dict[str, object]] = []
        for entry in getattr(statement, "entries"):
            detail_projections = {
                detail_sequence: projection
                for (entry_sequence, detail_sequence), projection in projections.items()
                if entry_sequence == getattr(entry, "entry_sequence_number")
            }
            entries.append(self._entry_payload(entry, detail_projections))

        payload = {
            "message_definition_identifier": getattr(
                statement,
                "message_definition_identifier",
            ),
            "statement_identity_reference": getattr(statement, "statement_identity_reference"),
            "electronic_sequence_number": getattr(statement, "electronic_sequence_number"),
            "legal_sequence_number": getattr(statement, "legal_sequence_number"),
            "period_start_at": self._timestamp_or_none(getattr(statement, "period_start_at")),
            "period_end_at": self._timestamp_or_none(getattr(statement, "period_end_at")),
            "opening_balance_hash": getattr(statement, "opening_balance_hash"),
            "closing_balance_hash": getattr(statement, "closing_balance_hash"),
            "account_currency_code": getattr(statement, "account_currency_code"),
            "entries": entries,
        }
        return self._hash_json(payload)

    def _entry_payload(
        self,
        entry: object,
        projections: dict[int, list[dict[str, object]]],
    ) -> dict[str, object]:
        """Return the entry hash preimage with optional structured detail evidence."""
        detail_payloads: list[dict[str, object]] = []
        for detail in getattr(entry, "entry_details"):
            detail_payload: dict[str, object] = {
                "detail_sequence_number": getattr(detail, "detail_sequence_number"),
                "source_locator_path": getattr(detail, "source_locator_path"),
                "detail_amount": self._decimal_text(getattr(detail, "detail_amount")),
                "detail_currency_code": getattr(detail, "detail_currency_code"),
                "credit_debit_code": getattr(detail, "credit_debit_code"),
                "end_to_end_reference": getattr(detail, "end_to_end_reference"),
                "remittance_evidence_text": getattr(detail, "remittance_evidence_text"),
            }
            projection = projections.get(getattr(detail, "detail_sequence_number"), [])
            if projection:
                detail_payload[_STRUCTURED_EVIDENCE_KEY] = projection
            detail_payloads.append(detail_payload)

        return {
            "source_entry_identity": getattr(entry, "source_entry_identity"),
            "entry_sequence_number": getattr(entry, "entry_sequence_number"),
            "source_locator_path": getattr(entry, "source_locator_path"),
            "booking_occurred_at": self._timestamp_or_none(
                getattr(entry, "booking_occurred_at"),
            ),
            "value_occurred_at": self._timestamp_or_none(
                getattr(entry, "value_occurred_at"),
            ),
            "entry_amount": self._decimal_text(getattr(entry, "entry_amount")),
            "entry_currency_code": getattr(entry, "entry_currency_code"),
            "credit_debit_code": getattr(entry, "credit_debit_code"),
            "reversal_indicator": getattr(entry, "reversal_indicator"),
            "bank_transaction_domain_code": getattr(entry, "bank_transaction_domain_code"),
            "bank_transaction_family_code": getattr(entry, "bank_transaction_family_code"),
            "bank_transaction_subfamily_code": getattr(
                entry,
                "bank_transaction_subfamily_code",
            ),
            "end_to_end_reference": getattr(entry, "end_to_end_reference"),
            "account_servicer_reference": getattr(entry, "account_servicer_reference"),
            "mandate_reference": getattr(entry, "mandate_reference"),
            "cheque_reference": getattr(entry, "cheque_reference"),
            "remittance_evidence_text": getattr(entry, "remittance_evidence_text"),
            "counterparty_evidence_hash": getattr(entry, "counterparty_evidence_hash"),
            "details": detail_payloads,
        }

    def _with_line_issuer(
        self,
        fixture: str,
        marker: str,
        second_line_issuer: str,
    ) -> bytes:
        """Insert two line identities while varying only the second type issuer."""
        structured = (
            f"{marker}\n"
            "              <Strd>\n"
            "                <RfrdDocInf>\n"
            "                  <Tp>\n"
            "                    <CdOrPrtry>\n"
            "                      <Cd>CINV</Cd>\n"
            "                    </CdOrPrtry>\n"
            "                  </Tp>\n"
            f"                  <Nb>{self.document_number}</Nb>\n"
            f"                  <RltdDt>{self.line_related_date}</RltdDt>\n"
            "                  <LineDtls>\n"
            "                    <Id>\n"
            "                      <Tp>\n"
            "                        <CdOrPrtry>\n"
            "                          <Cd>SKNB</Cd>\n"
            "                        </CdOrPrtry>\n"
            f"                        <Issr>{self.line_type_issuer}</Issr>\n"
            "                      </Tp>\n"
            f"                      <Nb>{self.first_line_number}</Nb>\n"
            f"                      <RltdDt>{self.line_related_date}</RltdDt>\n"
            "                    </Id>\n"
            "                    <Desc>Annual support service</Desc>\n"
            "                    <Amt>\n"
            "                      <DuePyblAmt Ccy=\"KRW\">10000.05</DuePyblAmt>\n"
            "                      <DscntApldAmt>\n"
            "                        <Tp><Cd>APDS</Cd></Tp>\n"
            "                        <Amt Ccy=\"KRW\">300.00</Amt>\n"
            "                      </DscntApldAmt>\n"
            "                      <RmtdAmt Ccy=\"KRW\">9700.05</RmtdAmt>\n"
            "                    </Amt>\n"
            "                  </LineDtls>\n"
            "                  <LineDtls>\n"
            "                    <Id>\n"
            "                      <Tp>\n"
            "                        <CdOrPrtry>\n"
            "                          <Cd>SKNB</Cd>\n"
            "                        </CdOrPrtry>\n"
            f"                        <Issr>{second_line_issuer}</Issr>\n"
            "                      </Tp>\n"
            f"                      <Nb>{self.second_line_number}</Nb>\n"
            f"                      <RltdDt>{self.line_related_date}</RltdDt>\n"
            "                    </Id>\n"
            "                    <Desc>Quarterly data service</Desc>\n"
            "                    <Amt>\n"
            "                      <DuePyblAmt Ccy=\"KRW\">5100.10</DuePyblAmt>\n"
            "                      <DscntApldAmt>\n"
            "                        <Tp><Cd>APDS</Cd></Tp>\n"
            "                        <Amt Ccy=\"KRW\">100.00</Amt>\n"
            "                      </DscntApldAmt>\n"
            "                      <RmtdAmt Ccy=\"KRW\">5000.10</RmtdAmt>\n"
            "                    </Amt>\n"
            "                  </LineDtls>\n"
            "                </RfrdDocInf>\n"
            "              </Strd>"
        )
        return fixture.replace(marker, structured, 1).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with a fresh replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"structured-line-issuer-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

    @staticmethod
    def _assert_sha256(value: object) -> None:
        """Require canonical digest syntax before equality comparisons."""
        if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
            raise AssertionError("evidence hash must be canonical SHA-256")

    @staticmethod
    def _assert_exact_transaction_amount(entry: object, detail: object) -> None:
        """Keep line-identification metadata separate from transaction amount truth."""
        if getattr(entry, "entry_amount", None) != Decimal("25000.00"):
            raise AssertionError("entry amount must remain exactly 25000.00")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("entry currency must remain KRW")
        if getattr(detail, "detail_amount", None) != Decimal("25000.00"):
            raise AssertionError("detail amount must remain exactly 25000.00")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("detail currency must remain KRW")

    @staticmethod
    def _decimal_text(value: object) -> str:
        """Match production canonical Decimal spelling used in evidence hashes."""
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
        text = format(amount, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text

    @staticmethod
    def _timestamp_or_none(value: object) -> str | None:
        """Match production UTC timestamp spelling used in evidence hashes."""
        if value is None:
            return None
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _hash_json(payload: dict[str, object]) -> str:
        """Hash one canonical JSON preimage without weakening production semantics."""
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


if __name__ == "__main__":
    unittest.main()
