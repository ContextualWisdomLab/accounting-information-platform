"""Review-repair REDs for camt.053 debtor/creditor account identification evidence."""

from __future__ import annotations

import json
import re
import unittest
import uuid
from decimal import Decimal

from accounting_information_platform import (
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
    lookup_bank_statement_entries,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting


class BankStatementDebtorCreditorAccountIdentificationChoiceReviewRedTests(unittest.TestCase):
    """Close exact-head hash and buyer-privacy oracle gaps without changing production."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.same_scalar_identifier = "DE89370400440532013000"

    def test_whitespace_oracle_requires_canonical_detail_hashes(self) -> None:
        """Representation-only whitespace still requires canonical retained detail evidence."""
        baseline = self._other_identification(
            "ACCT-001",
            scheme_kind="Prtry",
            scheme_value="LOCAL",
            issuer="BANK-A",
        )
        spaced = baseline.replace("<Prtry>LOCAL</Prtry>", "<Prtry>  LOCAL  </Prtry>")
        self.assertNotEqual(baseline, spaced)

        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                left = parse_bank_statement_payload(
                    self._with_role_account(role, baseline), CAMT053_MESSAGE_DEFINITION
                )
                right = parse_bank_statement_payload(
                    self._with_role_account(role, spaced), CAMT053_MESSAGE_DEFINITION
                )
                target_index = self._target_entry_index(role)
                left_detail = left.entries[target_index].entry_details[0]
                right_detail = right.entries[target_index].entry_details[0]
                digest_key = f"{role}_account_evidence_hash"

                self.assertNotEqual(left.source_artifact_hash, right.source_artifact_hash)
                self._assert_sha256(left_detail.source_detail_hash)
                self._assert_sha256(right_detail.source_detail_hash)
                self._assert_sha256(getattr(left_detail, digest_key, None))
                self._assert_sha256(getattr(right_detail, digest_key, None))
                self.assertEqual(left_detail.source_detail_hash, right_detail.source_detail_hash)
                self.assertEqual(
                    getattr(left_detail, digest_key), getattr(right_detail, digest_key)
                )
                self.assertEqual(
                    left.entries[target_index].source_entry_hash,
                    right.entries[target_index].source_entry_hash,
                )
                self.assertEqual(left.normalized_payload_hash, right.normalized_payload_hash)
                self._assert_exact_amount(role, right_detail)

    def test_buyer_privacy_covers_complete_other_identification_population(self) -> None:
        """Buyer differential excludes Othr Id, scheme choice/value, and issuer disclosure."""
        scheme_value = "CWLPRIVACYSCHEME9821"
        issuer = "CWLPRIVACYISSUER9821"
        rich_other = self._other_identification(
            self.same_scalar_identifier,
            scheme_kind="Prtry",
            scheme_value=scheme_value,
            issuer=issuer,
        )
        iban = self._iban_identification(self.same_scalar_identifier)

        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                rich_detail = self._ingest_and_read_target_detail(
                    role,
                    self._with_role_account(role, rich_other),
                    "privacy-rich-other",
                )
                iban_detail = self._ingest_and_read_target_detail(
                    role,
                    self._with_role_account(role, iban),
                    "privacy-iban",
                )
                digest_key = f"{role}_account_evidence_hash"

                for projection in (rich_detail, iban_detail):
                    self._assert_sha256(projection[digest_key])
                    self._assert_sha256(projection["source_detail_hash"])
                    self._assert_exact_amount(role, projection)

                self.assertNotEqual(rich_detail[digest_key], iban_detail[digest_key])
                self.assertNotEqual(
                    rich_detail["source_detail_hash"], iban_detail["source_detail_hash"]
                )

                rich_public = dict(rich_detail)
                iban_public = dict(iban_detail)
                for projection in (rich_public, iban_public):
                    projection.pop(digest_key)
                    projection.pop("source_detail_hash")
                self.assertEqual(rich_public, iban_public)

                serialized = json.dumps(
                    {"rich_other": rich_detail, "iban": iban_detail},
                    sort_keys=True,
                    default=str,
                )
                self.assertNotIn(self.same_scalar_identifier, serialized)
                self.assertNotIn(scheme_value, serialized)
                self.assertNotIn(issuer, serialized)

    @staticmethod
    def _other_identification(
        identifier: str,
        *,
        scheme_kind: str | None = None,
        scheme_value: str | None = None,
        issuer: str | None = None,
    ) -> str:
        scheme = ""
        if scheme_kind is not None:
            if scheme_kind not in {"Cd", "Prtry"} or scheme_value is None:
                raise AssertionError("scheme requires Cd|Prtry and a value")
            scheme = (
                "\n                    <SchmeNm>\n"
                f"                      <{scheme_kind}>{scheme_value}</{scheme_kind}>\n"
                "                    </SchmeNm>"
            )
        issuer_fragment = "" if issuer is None else f"\n                    <Issr>{issuer}</Issr>"
        return (
            "                <Id>\n"
            "                  <Othr>\n"
            f"                    <Id>{identifier}</Id>"
            f"{scheme}{issuer_fragment}\n"
            "                  </Othr>\n"
            "                </Id>"
        )

    @staticmethod
    def _iban_identification(identifier: str) -> str:
        return (
            "                <Id>\n"
            f"                  <IBAN>{identifier}</IBAN>\n"
            "                </Id>"
        )

    def _with_role_account(self, role: str, identification: str) -> bytes:
        if role == "debtor":
            marker = "              </Dbtr>\n            </RltdPties>"
            self.assertEqual(self.fixture.count(marker), 1)
            replacement = (
                "              </Dbtr>\n"
                "              <DbtrAcct>\n"
                f"{identification}\n"
                "              </DbtrAcct>\n"
                "            </RltdPties>"
            )
        elif role == "creditor":
            marker = (
                "            <AmtDtls>\n"
                "              <TxAmt>\n"
                "                <Amt Ccy=\"KRW\">6000.00</Amt>\n"
                "              </TxAmt>\n"
                "            </AmtDtls>\n"
                "            <RmtInf>"
            )
            self.assertEqual(self.fixture.count(marker), 1)
            replacement = (
                "            <AmtDtls>\n"
                "              <TxAmt>\n"
                "                <Amt Ccy=\"KRW\">6000.00</Amt>\n"
                "              </TxAmt>\n"
                "            </AmtDtls>\n"
                "            <RltdPties>\n"
                "              <CdtrAcct>\n"
                f"{identification}\n"
                "              </CdtrAcct>\n"
                "            </RltdPties>\n"
                "            <RmtInf>"
            )
        else:
            raise AssertionError(f"unsupported role: {role}")
        return self.fixture.replace(marker, replacement, 1).encode("utf-8")

    def _register_statement_account(self, payload: bytes) -> str:
        parsed = parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)
        reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": reference,
                "account_currency_code": parsed.account_currency_code,
                "account_identifier_hash": parsed.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        return reference

    def _ingest_and_read_target_detail(
        self, role: str, payload: bytes, suffix: str
    ) -> dict[str, object]:
        reference = self._register_statement_account(payload)
        accepted = accept_bank_statement_evidence(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": reference,
                "ingestion_idempotency_key": (
                    f"related-account-identification-review-{suffix}-{role}-{uuid.uuid4().hex}"
                ),
                "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
                "statement_payload": payload.decode("utf-8"),
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=MemoryArtifactStore(),
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        return document["bank_statement_entries"][self._target_entry_index(role)][
            "entry_details"
        ][0]

    @staticmethod
    def _target_entry_index(role: str) -> int:
        return 0 if role == "debtor" else 1

    def _assert_exact_amount(self, role: str, detail: dict[str, object] | object) -> None:
        if isinstance(detail, dict):
            amount = Decimal(str(detail["detail_amount"]))
            currency = detail["detail_currency_code"]
        else:
            amount = detail.detail_amount
            currency = detail.detail_currency_code
        expected = Decimal("25000.00") if role == "debtor" else Decimal("6000.00")
        self.assertEqual(amount, expected)
        self.assertEqual(currency, "KRW")

    def _assert_sha256(self, value: object) -> None:
        self.assertIsInstance(value, str)
        self.assertIsNotNone(re.fullmatch(r"sha256:[0-9a-f]{64}", value))


if __name__ == "__main__":
    unittest.main()
