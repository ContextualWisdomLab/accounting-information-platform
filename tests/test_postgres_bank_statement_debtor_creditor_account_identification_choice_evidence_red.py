"""PostgreSQL REDs for camt.053 debtor/creditor account identification evidence."""

from __future__ import annotations

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
from tests import test_postgres_posting as posting


class BankStatementDebtorCreditorAccountIdentificationChoiceEvidenceRedTests(unittest.TestCase):
    """Preserve complete related-account identification semantics without disclosure."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one nested PostgreSQL fixture and the pinned CAMT.053 statement."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.same_scalar_identifier = "DE89370400440532013000"

    def test_complete_other_identification_changes_detail_entry_and_statement_identity(self) -> None:
        """Each material GenericAccountIdentification1 semantic affects admitted identity."""
        cases = self._material_identification_pairs()
        for role in ("debtor", "creditor"):
            target_index = self._target_entry_index(role)
            untouched_index = 1 - target_index
            expected_amount = self._expected_detail_amount(role)
            for label, left_identification, right_identification in cases:
                with self.subTest(role=role, semantic=label):
                    left = parse_bank_statement_payload(
                        self._with_role_account(role, left_identification),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    right = parse_bank_statement_payload(
                        self._with_role_account(role, right_identification),
                        CAMT053_MESSAGE_DEFINITION,
                    )
                    left_detail = left.entries[target_index].entry_details[0]
                    right_detail = right.entries[target_index].entry_details[0]

                    self.assertEqual(left_detail.detail_amount, expected_amount)
                    self.assertEqual(right_detail.detail_amount, expected_amount)
                    self.assertEqual(left_detail.detail_currency_code, "KRW")
                    self.assertEqual(right_detail.detail_currency_code, "KRW")
                    self._assert_sha256(left_detail.source_detail_hash)
                    self._assert_sha256(right_detail.source_detail_hash)
                    self.assertNotEqual(left_detail.source_detail_hash, right_detail.source_detail_hash)
                    self.assertNotEqual(
                        left.entries[target_index].source_entry_hash,
                        right.entries[target_index].source_entry_hash,
                    )
                    self.assertNotEqual(left.normalized_payload_hash, right.normalized_payload_hash)
                    self.assertEqual(left.account_identifier_hash, right.account_identifier_hash)
                    self.assertEqual(
                        left.entries[untouched_index].source_entry_hash,
                        right.entries[untouched_index].source_entry_hash,
                    )

    def test_other_identification_whitespace_is_representation_only(self) -> None:
        """Whitespace around a proprietary scheme changes raw bytes, not account semantics."""
        baseline = self._other_identification(
            "ACCT-001",
            scheme_kind="Prtry",
            scheme_value="LOCAL",
            issuer="BANK-A",
        )
        spaced = baseline.replace("<Prtry>LOCAL</Prtry>", "<Prtry>  LOCAL  </Prtry>")
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

                self.assertNotEqual(left.source_artifact_hash, right.source_artifact_hash)
                self.assertEqual(left_detail.source_detail_hash, right_detail.source_detail_hash)
                self.assertEqual(
                    left.entries[target_index].source_entry_hash,
                    right.entries[target_index].source_entry_hash,
                )
                self.assertEqual(left.normalized_payload_hash, right.normalized_payload_hash)

    def test_material_related_account_change_reaches_explicit_correction_boundary(self) -> None:
        """An accepted statement cannot silently replay a changed related-account identity."""
        baseline = self._other_identification(
            "ACCT-001",
            scheme_kind="Prtry",
            scheme_value="LOCAL",
            issuer="BANK-A",
        )
        changed = self._other_identification(
            "ACCT-002",
            scheme_kind="Prtry",
            scheme_value="LOCAL",
            issuer="BANK-A",
        )
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                bank_account_reference = self._register_statement_account(
                    self._with_role_account(role, baseline)
                )
                store = MemoryArtifactStore()
                accept_bank_statement_evidence(
                    self._command(
                        self._with_role_account(role, baseline),
                        bank_account_reference,
                        f"{role}-baseline",
                    ),
                    posting.DATABASE_URL,
                    self.case.policy.tenant_reference,
                    artifact_store=store,
                )
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    r"statement identity already exists with different entry evidence",
                ):
                    accept_bank_statement_evidence(
                        self._command(
                            self._with_role_account(role, changed),
                            bank_account_reference,
                            f"{role}-changed",
                        ),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=store,
                    )

    def test_buyer_digest_preserves_choice_without_reversible_account_projection(self) -> None:
        """IBAN/Othr choice is purpose-bound evidence and never a reversible buyer field."""
        other_identification = self._other_identification(self.same_scalar_identifier)
        iban_identification = self._iban_identification(self.same_scalar_identifier)
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                other_detail = self._ingest_and_read_target_detail(
                    role,
                    self._with_role_account(role, other_identification),
                    "other",
                )
                iban_detail = self._ingest_and_read_target_detail(
                    role,
                    self._with_role_account(role, iban_identification),
                    "iban",
                )
                digest_key = f"{role}_account_evidence_hash"
                for projection in (other_detail, iban_detail):
                    self._assert_sha256(projection[digest_key])
                    self._assert_sha256(projection["source_detail_hash"])
                self.assertNotEqual(other_detail[digest_key], iban_detail[digest_key])
                self.assertNotEqual(
                    other_detail["source_detail_hash"], iban_detail["source_detail_hash"]
                )

                other_public = dict(other_detail)
                iban_public = dict(iban_detail)
                other_public.pop(digest_key)
                iban_public.pop(digest_key)
                other_public.pop("source_detail_hash")
                iban_public.pop("source_detail_hash")
                self.assertEqual(other_public, iban_public)

                serialized = json.dumps(
                    {"other": other_detail, "iban": iban_detail},
                    sort_keys=True,
                    default=str,
                )
                self.assertNotIn(self.same_scalar_identifier, serialized)

    def test_buyer_digest_is_material_for_other_identification_scheme_and_issuer(self) -> None:
        """Purpose digests retain Othr Id, scheme choice/value/absence, and issuer semantics."""
        baseline = self._other_identification(
            "ACCT-001",
            scheme_kind="Prtry",
            scheme_value="BBAN",
            issuer="BANK-A",
        )
        variants = [
            self._other_identification(
                "ACCT-002", scheme_kind="Prtry", scheme_value="BBAN", issuer="BANK-A"
            ),
            self._other_identification(
                "ACCT-001", scheme_kind="Prtry", scheme_value="LOCAL", issuer="BANK-A"
            ),
            self._other_identification(
                "ACCT-001", scheme_kind="Cd", scheme_value="BBAN", issuer="BANK-A"
            ),
            self._other_identification("ACCT-001", issuer="BANK-A"),
            self._other_identification(
                "ACCT-001", scheme_kind="Prtry", scheme_value="BBAN", issuer="BANK-B"
            ),
            self._other_identification(
                "ACCT-001", scheme_kind="Prtry", scheme_value="BBAN"
            ),
        ]
        for role in ("debtor", "creditor"):
            baseline_detail = self._ingest_and_read_target_detail(
                role,
                self._with_role_account(role, baseline),
                "digest-baseline",
            )
            digest_key = f"{role}_account_evidence_hash"
            self._assert_sha256(baseline_detail[digest_key])
            for index, variant in enumerate(variants):
                with self.subTest(role=role, variant=index):
                    variant_detail = self._ingest_and_read_target_detail(
                        role,
                        self._with_role_account(role, variant),
                        f"digest-variant-{index}",
                    )
                    self._assert_sha256(variant_detail[digest_key])
                    self.assertNotEqual(
                        baseline_detail[digest_key], variant_detail[digest_key]
                    )
                    self.assertEqual(
                        Decimal(str(variant_detail["detail_amount"])),
                        self._expected_detail_amount(role),
                    )
                    self.assertEqual(variant_detail["detail_currency_code"], "KRW")

    def _material_identification_pairs(self) -> list[tuple[str, str, str]]:
        baseline = self._other_identification(
            "ACCT-001",
            scheme_kind="Prtry",
            scheme_value="BBAN",
            issuer="BANK-A",
        )
        return [
            (
                "other_id",
                baseline,
                self._other_identification(
                    "ACCT-002", scheme_kind="Prtry", scheme_value="BBAN", issuer="BANK-A"
                ),
            ),
            (
                "scheme_value",
                baseline,
                self._other_identification(
                    "ACCT-001", scheme_kind="Prtry", scheme_value="LOCAL", issuer="BANK-A"
                ),
            ),
            (
                "scheme_choice",
                baseline,
                self._other_identification(
                    "ACCT-001", scheme_kind="Cd", scheme_value="BBAN", issuer="BANK-A"
                ),
            ),
            (
                "scheme_absence",
                baseline,
                self._other_identification("ACCT-001", issuer="BANK-A"),
            ),
            (
                "issuer_value",
                baseline,
                self._other_identification(
                    "ACCT-001", scheme_kind="Prtry", scheme_value="BBAN", issuer="BANK-B"
                ),
            ),
            (
                "issuer_absence",
                baseline,
                self._other_identification(
                    "ACCT-001", scheme_kind="Prtry", scheme_value="BBAN"
                ),
            ),
            (
                "identification_choice",
                self._other_identification(self.same_scalar_identifier),
                self._iban_identification(self.same_scalar_identifier),
            ),
        ]

    @staticmethod
    def _other_identification(
        identifier: str,
        *,
        scheme_kind: str | None = None,
        scheme_value: str | None = None,
        issuer: str | None = None,
    ) -> str:
        """Return one AccountIdentification4Choice/Othr fragment in schema sequence."""
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
        """Return one AccountIdentification4Choice/IBAN fragment."""
        return (
            "                <Id>\n"
            f"                  <IBAN>{identifier}</IBAN>\n"
            "                </Id>"
        )

    def _with_role_account(self, role: str, identification: str) -> bytes:
        """Insert one debtor or creditor CashAccount40 into a valid transaction detail."""
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
        """Register the statement owner account without copying transaction-account truth."""
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
        """Ingest one statement under a fresh owner account and return the target detail."""
        reference = self._register_statement_account(payload)
        accepted = accept_bank_statement_evidence(
            self._command(payload, reference, suffix),
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

    def _command(
        self, payload: bytes, bank_account_reference: str, suffix: str
    ) -> dict[str, object]:
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": bank_account_reference,
            "ingestion_idempotency_key": (
                f"related-account-identification-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

    @staticmethod
    def _target_entry_index(role: str) -> int:
        return 0 if role == "debtor" else 1

    @staticmethod
    def _expected_detail_amount(role: str) -> Decimal:
        return Decimal("25000.00") if role == "debtor" else Decimal("6000.00")

    def _assert_sha256(self, value: object) -> None:
        self.assertIsInstance(value, str)
        self.assertIsNotNone(re.fullmatch(r"sha256:[0-9a-f]{64}", value))


if __name__ == "__main__":
    unittest.main()
