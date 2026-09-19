"""PostgreSQL REDs for camt.053 transaction-detail underlying-allocation evidence."""

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
_UNDERLYING_ALLOCATION_PATH = (
    "camt.053.001.14/Stmt/Ntry/NtryDtls/TxDtls/UndrlygAllcn"
)
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailUnderlyingAllocationEvidenceRedTests(unittest.TestCase):
    """Retain bank-reported allocation evidence without turning it into posting truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare two ordered TransactionAllocation2 records and causal variants."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = (
            "            <RmtInf>\n"
            "              <Ustrd>Invoice 1001</Ustrd>\n"
            "            </RmtInf>\n"
        )
        self.assertEqual(fixture.count(marker), 1)

        self.base = [
            {
                "amount": "10000.00",
                "currency": "KRW",
                "credit_debit_code": "CRDT",
                "account": {
                    "choice": "other",
                    "identification": "ALLOC-ACC-001",
                },
                "purpose": {"choice": "code", "value": "GDDS"},
                "reference": "ALLOC-REF-001",
                "related_references": [
                    {
                        "choice": "securities_settlement_transaction_identification",
                        "value": "SETTLE-001",
                    },
                    {
                        "choice": "account_servicer_transaction_identification",
                        "value": "AS-001",
                    },
                ],
            },
            {
                "amount": "15000.00",
                "currency": "KRW",
                "credit_debit_code": "CRDT",
                "account": {
                    "choice": "other",
                    "identification": "ALLOC-ACC-002",
                },
                "purpose": {"choice": "proprietary", "value": "RESIDUAL"},
                "reference": "ALLOC-REF-002",
                "related_references": [
                    {
                        "choice": "intra_balance_movement_identification",
                        "value": "IBM-002",
                    }
                ],
            },
        ]
        self.expected_variants = {
            "allocation_split": [
                {**self.base[0], "amount": "9000.00"},
                {**self.base[1], "amount": "16000.00"},
            ],
            "account_identification": [
                {
                    **self.base[0],
                    "account": {
                        **self.base[0]["account"],
                        "identification": "ALLOC-ACC-003",
                    },
                },
                self.base[1],
            ],
            "purpose_value": [
                {
                    **self.base[0],
                    "purpose": {"choice": "code", "value": "SALA"},
                },
                self.base[1],
            ],
            "purpose_choice": [
                {
                    **self.base[0],
                    "purpose": {"choice": "proprietary", "value": "GDDS"},
                },
                self.base[1],
            ],
            "reference": [
                {**self.base[0], "reference": "ALLOC-REF-003"},
                self.base[1],
            ],
            "related_reference_value": [
                {
                    **self.base[0],
                    "related_references": [
                        {
                            "choice": "securities_settlement_transaction_identification",
                            "value": "SETTLE-002",
                        },
                        self.base[0]["related_references"][1],
                    ],
                },
                self.base[1],
            ],
            "related_reference_choice": [
                {
                    **self.base[0],
                    "related_references": [
                        {
                            "choice": "account_servicer_transaction_identification",
                            "value": "SETTLE-001",
                        },
                        self.base[0]["related_references"][1],
                    ],
                },
                self.base[1],
            ],
            "related_reference_order": [
                {
                    **self.base[0],
                    "related_references": list(
                        reversed(self.base[0]["related_references"])
                    ),
                },
                self.base[1],
            ],
            "allocation_order": list(reversed(self.base)),
        }

        self.base_payload = self._with_allocations(fixture, marker, self.base)
        self.base_statement = self._parse(self.base_payload)
        self.variant_payloads = {
            label: self._with_allocations(fixture, marker, allocations)
            for label, allocations in self.expected_variants.items()
        }
        self.variants = {
            label: self._parse(payload)
            for label, payload in self.variant_payloads.items()
        }

        allocation_xml = self._allocations_xml(self.base)
        self.assertEqual(self.base_payload.count(allocation_xml.encode("utf-8")), 1)
        reformatted_xml = allocation_xml.replace(
            "            <UndrlygAllcn>\n",
            "            <UndrlygAllcn>\n              \n",
            1,
        )
        self.reformatted_payload = self.base_payload.replace(
            allocation_xml.encode("utf-8"), reformatted_xml.encode("utf-8"), 1
        )
        self.reformatted_statement = self._parse(self.reformatted_payload)

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

    def test_allocation_semantics_and_source_order_are_material_to_identity(self) -> None:
        """Allocation split, account, purpose, refs, and order each alter evidence identity."""
        base_hash = self._expected_hash(self.base)
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        self.assertRegex(base_hash, _HASH_PATTERN)
        self.assertEqual(
            getattr(base_detail, "underlying_allocation_evidence_hash", None), base_hash
        )
        self._assert_entry_hash_binding(base_entry, base_hash)

        for label, statement in self.variants.items():
            with self.subTest(label=label):
                expected_hash = self._expected_hash(self.expected_variants[label])
                entry = statement.entries[0]
                detail = entry.entry_details[0]
                self.assertRegex(expected_hash, _HASH_PATTERN)
                self.assertNotEqual(base_hash, expected_hash)
                self.assertEqual(
                    getattr(detail, "underlying_allocation_evidence_hash", None),
                    expected_hash,
                )
                self._assert_entry_hash_binding(entry, expected_hash)
                self.assertEqual(
                    self.base_statement.account_identifier_hash,
                    statement.account_identifier_hash,
                )
                self.assertEqual(base_entry.entry_amount, entry.entry_amount)
                self.assertEqual(base_detail.detail_amount, detail.detail_amount)
                self.assertEqual(base_entry.entry_currency_code, entry.entry_currency_code)
                self.assertEqual(base_detail.detail_currency_code, detail.detail_currency_code)
                self.assertNotEqual(base_detail.source_detail_hash, detail.source_detail_hash)
                self.assertNotEqual(base_entry.source_entry_hash, entry.source_entry_hash)
                self.assertNotEqual(
                    self.base_statement.normalized_payload_hash,
                    statement.normalized_payload_hash,
                )
                self.assertEqual(
                    self.base_statement.entries[1].source_entry_hash,
                    statement.entries[1].source_entry_hash,
                )

    def test_xml_layout_does_not_change_underlying_allocation_semantics(self) -> None:
        """Layout-only XML changes raw provenance without changing allocation identity."""
        expected_hash = self._expected_hash(self.base)
        base_entry = self.base_statement.entries[0]
        reformatted_entry = self.reformatted_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        reformatted_detail = reformatted_entry.entry_details[0]

        self.assertNotEqual(
            self.base_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(base_detail, "underlying_allocation_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(reformatted_detail, "underlying_allocation_evidence_hash", None),
            expected_hash,
        )
        self._assert_entry_hash_binding(base_entry, expected_hash)
        self._assert_entry_hash_binding(reformatted_entry, expected_hash)
        self.assertEqual(base_detail.source_detail_hash, reformatted_detail.source_detail_hash)
        self.assertEqual(base_entry.source_entry_hash, reformatted_entry.source_entry_hash)
        self.assertEqual(
            self.base_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_underlying_allocation_requires_explicit_statement_correction(self) -> None:
        """Accepted allocation provenance cannot be silently replaced under one statement id."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.variant_payloads["allocation_split"], "changed"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_preserves_allocations_without_recomputing_accounting_amount(self) -> None:
        """Tenant reads expose ordered allocation evidence while entry/detail amount remains 25000."""
        expected_hash = self._expected_hash(self.base)
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

        self.assertEqual(detail.get("underlying_allocation_evidence_hash"), expected_hash)
        self.assertEqual(detail.get("underlying_allocations"), self.base)
        self.assertEqual(entry["entry_amount"], "25000")
        self.assertEqual(entry["entry_currency_code"], "KRW")
        self.assertEqual(detail["detail_amount"], "25000")
        self.assertEqual(detail["detail_currency_code"], "KRW")

    @staticmethod
    def _parse(payload: bytes) -> object:
        """Parse one V14 statement through the supported adapter boundary."""
        return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Require source_entry_hash to digest the exact allocation-bound detail projection."""
        projection = dict(bank_statement._entry_payload(entry))
        details = projection.get("details")
        if not isinstance(details, list) or not details:
            raise AssertionError("canonical entry projection must retain transaction details")
        first_detail = details[0]
        if not isinstance(first_detail, dict):
            raise AssertionError("canonical entry detail projection must be a mapping")
        if first_detail.get("underlying_allocation_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry the exact "
                "underlying_allocation_evidence_hash"
            )
        preimage = json.dumps(
            projection, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        expected_entry_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if getattr(entry, "source_entry_hash", None) != expected_entry_hash:
            raise AssertionError(
                "source_entry_hash must digest the canonical entry projection containing "
                "underlying_allocation_evidence_hash"
            )

    @staticmethod
    def _expected_hash(allocations: list[dict[str, object]]) -> str:
        """Digest admitted source-ordered TransactionAllocation2 evidence."""
        preimage = json.dumps(
            {
                "evidence_type": _UNDERLYING_ALLOCATION_PATH,
                "underlying_allocations": allocations,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @classmethod
    def _allocations_xml(cls, allocations: list[dict[str, object]]) -> str:
        """Serialize source-ordered TransactionAllocation2 values in V14 schema order."""
        return "".join(cls._allocation_xml(allocation) for allocation in allocations)

    @staticmethod
    def _allocation_xml(allocation: dict[str, object]) -> str:
        """Serialize one TransactionAllocation2 with required amount, account, purpose, and ref."""
        account = allocation["account"]
        purpose = allocation["purpose"]
        related_references = allocation["related_references"]
        if not isinstance(account, dict) or account.get("choice") != "other":
            raise AssertionError("focused allocation account must use AccountIdentification4Choice/Othr")
        if not isinstance(purpose, dict):
            raise AssertionError("allocation purpose must preserve Purpose2Choice")
        purpose_choice = purpose.get("choice")
        if purpose_choice not in {"code", "proprietary"}:
            raise AssertionError("unsupported focused Purpose2Choice discriminator")
        purpose_tag = "Cd" if purpose_choice == "code" else "Prtry"
        reference_tags = {
            "securities_settlement_transaction_identification": "SctiesSttlmTxId",
            "intra_position_movement_identification": "IntraPosMvmntId",
            "intra_balance_movement_identification": "IntraBalMvmntId",
            "account_servicer_transaction_identification": "AcctSvcrTxId",
        }
        refs_xml = ""
        if not isinstance(related_references, list):
            raise AssertionError("allocation related_references must be source-ordered")
        for related in related_references:
            if not isinstance(related, dict):
                raise AssertionError("allocation related reference must be a mapping")
            tag = reference_tags.get(str(related.get("choice")))
            if tag is None:
                raise AssertionError("unsupported focused References80Choice discriminator")
            refs_xml += (
                "              <RltdRefs>\n"
                f"                <{tag}>{related['value']}</{tag}>\n"
                "              </RltdRefs>\n"
            )
        return (
            "            <UndrlygAllcn>\n"
            f"              <Amt Ccy=\"{allocation['currency']}\">{allocation['amount']}</Amt>\n"
            f"              <CdtDbtInd>{allocation['credit_debit_code']}</CdtDbtInd>\n"
            "              <Acct>\n"
            "                <Id>\n"
            "                  <Othr>\n"
            f"                    <Id>{account['identification']}</Id>\n"
            "                  </Othr>\n"
            "                </Id>\n"
            "              </Acct>\n"
            "              <Purp>\n"
            f"                <{purpose_tag}>{purpose['value']}</{purpose_tag}>\n"
            "              </Purp>\n"
            f"              <Ref>{allocation['reference']}</Ref>\n"
            f"{refs_xml}"
            "            </UndrlygAllcn>\n"
        )

    @classmethod
    def _with_allocations(
        cls,
        fixture: str,
        marker: str,
        allocations: list[dict[str, object]],
    ) -> bytes:
        """Insert UndrlygAllcn after RmtInf while later V14 siblings remain absent."""
        if fixture.count(marker) != 1:
            raise AssertionError("canonical first-detail RmtInf marker must occur exactly once")
        return fixture.replace(
            marker,
            marker + cls._allocations_xml(allocations),
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Build one supported ingest command with a unique replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"detail-underlying-allocation-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
