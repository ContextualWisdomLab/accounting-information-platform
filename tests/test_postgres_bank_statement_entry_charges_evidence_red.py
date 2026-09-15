"""PostgreSQL REDs for camt.053 entry-level charge provenance."""

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
_ENTRY_CHARGES_PURPOSE = "camt.053.001.14/Stmt/Ntry/Chrgs"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementEntryChargesEvidenceRedTests(unittest.TestCase):
    """Retain bank-reported entry charges without turning them into posting policy."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare statements that isolate focused Charges15 semantics."""
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

        self.base_payload = self._with_charges(
            fixture,
            marker,
            total_amount="25.00",
            record_amount="20.00",
            credit_debit_code="DBIT",
            charge_included="true",
        )
        self.changed_total_payload = self._with_charges(
            fixture,
            marker,
            total_amount="26.00",
            record_amount="20.00",
            credit_debit_code="DBIT",
            charge_included="true",
        )
        self.changed_record_amount_payload = self._with_charges(
            fixture,
            marker,
            total_amount="25.00",
            record_amount="21.00",
            credit_debit_code="DBIT",
            charge_included="true",
        )
        self.changed_direction_payload = self._with_charges(
            fixture,
            marker,
            total_amount="25.00",
            record_amount="20.00",
            credit_debit_code="CRDT",
            charge_included="true",
        )
        self.changed_included_payload = self._with_charges(
            fixture,
            marker,
            total_amount="25.00",
            record_amount="20.00",
            credit_debit_code="DBIT",
            charge_included="false",
        )
        self.equivalent_boolean_payload = self._with_charges(
            fixture,
            marker,
            total_amount="25.00",
            record_amount="20.00",
            credit_debit_code="DBIT",
            charge_included="1",
        )

        formatting_anchor = (
            "        <Chrgs>\n"
            "          <TtlChrgsAndTaxAmt Ccy=\"KRW\">25.00</TtlChrgsAndTaxAmt>\n"
            "          <Rcrd>\n"
            "            <Amt Ccy=\"KRW\">20.00</Amt>\n"
            "            <CdtDbtInd>DBIT</CdtDbtInd>\n"
            "            <ChrgInclInd>true</ChrgInclInd>\n"
            "          </Rcrd>\n"
            "        </Chrgs>\n"
        ).encode("utf-8")
        self.assertEqual(self.base_payload.count(formatting_anchor), 1)
        self.reformatted_payload = self.base_payload.replace(
            formatting_anchor,
            (
                "        <Chrgs><TtlChrgsAndTaxAmt Ccy=\"KRW\">25.00</TtlChrgsAndTaxAmt>\n"
                "          <Rcrd><Amt Ccy=\"KRW\">20.00</Amt>"
                "<CdtDbtInd>DBIT</CdtDbtInd>"
                "<ChrgInclInd>true</ChrgInclInd></Rcrd></Chrgs>\n"
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
        self.changed_included_statement = parse_bank_statement_payload(
            self.changed_included_payload, CAMT053_MESSAGE_DEFINITION
        )
        self.equivalent_boolean_statement = parse_bank_statement_payload(
            self.equivalent_boolean_payload, CAMT053_MESSAGE_DEFINITION
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

    def test_entry_charge_semantics_are_independently_material_evidence(self) -> None:
        """Total, record amount, direction, and inclusion flag are independently material."""
        variants = (
            (self.base_statement, self._expected_hash("25.00", "20.00", "DBIT", True)),
            (
                self.changed_total_statement,
                self._expected_hash("26.00", "20.00", "DBIT", True),
            ),
            (
                self.changed_record_amount_statement,
                self._expected_hash("25.00", "21.00", "DBIT", True),
            ),
            (
                self.changed_direction_statement,
                self._expected_hash("25.00", "20.00", "CRDT", True),
            ),
            (
                self.changed_included_statement,
                self._expected_hash("25.00", "20.00", "DBIT", False),
            ),
        )

        expected_hashes: list[str] = []
        for statement, expected_hash in variants:
            with self.subTest(expected_hash=expected_hash):
                entry = statement.entries[0]
                self.assertRegex(expected_hash, _HASH_PATTERN)
                self.assertEqual(
                    getattr(entry, "entry_charges_evidence_hash", None),
                    expected_hash,
                )
                self._assert_entry_hash_binding(entry, expected_hash)
                expected_hashes.append(expected_hash)

        self.assertEqual(len(set(expected_hashes)), len(expected_hashes))
        for changed in (
            self.changed_total_statement,
            self.changed_record_amount_statement,
            self.changed_direction_statement,
            self.changed_included_statement,
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

    def test_boolean_lexical_and_xml_layout_variants_preserve_charge_semantics(self) -> None:
        """XML layout and xs:boolean true/1 lexical variants must normalize identically."""
        expected_hash = self._expected_hash("25.00", "20.00", "DBIT", True)
        base_entry = self.base_statement.entries[0]

        for statement in (
            self.equivalent_boolean_statement,
            self.reformatted_statement,
        ):
            with self.subTest(source_hash=statement.source_artifact_hash):
                entry = statement.entries[0]
                self.assertNotEqual(
                    self.base_statement.source_artifact_hash,
                    statement.source_artifact_hash,
                )
                self.assertEqual(
                    getattr(entry, "entry_charges_evidence_hash", None),
                    expected_hash,
                )
                self._assert_entry_hash_binding(entry, expected_hash)
                self.assertEqual(base_entry.source_entry_hash, entry.source_entry_hash)
                self.assertEqual(
                    self.base_statement.normalized_payload_hash,
                    statement.normalized_payload_hash,
                )

    def test_changed_entry_charges_require_explicit_statement_correction(self) -> None:
        """Every focused material charge fact must cross the supported correction boundary."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        for suffix, changed_payload in (
            ("changed-total", self.changed_total_payload),
            ("changed-record-amount", self.changed_record_amount_payload),
            ("changed-direction", self.changed_direction_payload),
            ("changed-included", self.changed_included_payload),
        ):
            with self.subTest(suffix=suffix):
                with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                    accept_bank_statement_evidence(
                        self._command(changed_payload, suffix),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_buyer_read_preserves_exact_entry_charge_provenance(self) -> None:
        """Supported reads retain exact reported charge semantics and purpose digest."""
        expected_hash = self._expected_hash("25.00", "20.00", "DBIT", True)
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

        self.assertEqual(entry.get("entry_charges_evidence_hash"), expected_hash)
        self.assertEqual(
            entry.get("charges_evidence"),
            {
                "total_charges_and_tax_amount": {
                    "amount": "25.00",
                    "currency_code": "KRW",
                },
                "records": [
                    {
                        "amount": "20.00",
                        "currency_code": "KRW",
                        "credit_debit_code": "DBIT",
                        "charge_included": True,
                    }
                ],
            },
        )

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Bind source_entry_hash to semantic charge evidence, never XML layout."""
        projection = dict(bank_statement._entry_payload(entry))
        if projection.get("entry_charges_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry the exact entry_charges_evidence_hash"
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
        charge_included: bool,
    ) -> str:
        """Digest the complete focused Charges15 semantics."""
        preimage = json.dumps(
            {
                "evidence_type": _ENTRY_CHARGES_PURPOSE,
                "total_charges_and_tax_amount": {
                    "amount": total_amount,
                    "currency_code": "KRW",
                },
                "records": [
                    {
                        "amount": record_amount,
                        "currency_code": "KRW",
                        "credit_debit_code": credit_debit_code,
                        "charge_included": charge_included,
                    }
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _with_charges(
        fixture: str,
        marker: str,
        *,
        total_amount: str,
        record_amount: str,
        credit_debit_code: str,
        charge_included: str,
    ) -> bytes:
        """Insert one entry-level Charges15 immediately after BkTxCd."""
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
            "        <Chrgs>\n"
            f"          <TtlChrgsAndTaxAmt Ccy=\"KRW\">{total_amount}</TtlChrgsAndTaxAmt>\n"
            "          <Rcrd>\n"
            f"            <Amt Ccy=\"KRW\">{record_amount}</Amt>\n"
            f"            <CdtDbtInd>{credit_debit_code}</CdtDbtInd>\n"
            f"            <ChrgInclInd>{charge_included}</ChrgInclInd>\n"
            "          </Rcrd>\n"
            "        </Chrgs>\n"
            "        <NtryDtls>\n",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Build a supported ingest command with an independent idempotency key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": f"entry-charges-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
