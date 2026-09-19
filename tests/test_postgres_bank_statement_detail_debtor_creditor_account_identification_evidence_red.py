"""PostgreSQL REDs for direct debtor/creditor account identification evidence."""

from __future__ import annotations

import copy
import re
import unittest
import uuid
from decimal import Decimal

from accounting_information_platform import AccountingValidationError, MemoryArtifactStore
from tests import (
    test_postgres_bank_statement_debtor_creditor_party_identification_evidence_red as party,
)

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)
_BASE_ACCOUNT_ID = "DE89370400440532013000"
_CHANGED_ACCOUNT_ID = "DIRECT-COUNTERPARTY-ACCOUNT-002"


class BankStatementDetailDebtorCreditorAccountIdentificationEvidenceRedTests(
    unittest.TestCase
):
    """Retain TransactionParties12 DbtrAcct/CdtrAcct identification without disclosure."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        party.BankStatementDebtorCreditorPartyIdentificationEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one isolated helper and a complete GenericAccountIdentification1."""
        self.helper = party.BankStatementDebtorCreditorPartyIdentificationEvidenceRedTests(
            "setUp"
        )
        self.addCleanup(self.helper.doCleanups)
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)
        self.base_account = {
            "choice": "other",
            "id": _BASE_ACCOUNT_ID,
            "scheme": {"proprietary": "BANK"},
            "issuer": "Counterparty Account Registry",
        }

    def test_account_identification_value_choice_scheme_issuer_and_presence_are_material(
        self,
    ) -> None:
        """CashAccount40.Id semantics change retained counterparty evidence by role."""
        for role in ("debtor", "creditor"):
            baseline = self._parse(self._with_account(role, self.base_account))
            target_index = self.helper._target_entry_index(role)
            untouched_index = 1 - target_index
            baseline_entry = baseline.entries[target_index]
            baseline_detail = baseline_entry.entry_details[0]
            self._assert_financial_truth(role, baseline_entry, baseline_detail)
            self._assert_evidence_chain(baseline, baseline_entry, baseline_detail)

            for semantic, changed_account in self._variants().items():
                with self.subTest(role=role, semantic=semantic):
                    changed = self._parse(self._with_account(role, changed_account))
                    changed_entry = changed.entries[target_index]
                    changed_detail = changed_entry.entry_details[0]
                    self._assert_financial_truth(role, changed_entry, changed_detail)
                    self._assert_evidence_chain(changed, changed_entry, changed_detail)

                    self.assertNotEqual(
                        baseline_entry.counterparty_evidence_hash,
                        changed_entry.counterparty_evidence_hash,
                    )
                    self.assertNotEqual(
                        baseline_detail.source_detail_hash,
                        changed_detail.source_detail_hash,
                    )
                    self.assertNotEqual(
                        baseline_entry.source_entry_hash,
                        changed_entry.source_entry_hash,
                    )
                    self.assertNotEqual(
                        baseline.normalized_payload_hash,
                        changed.normalized_payload_hash,
                    )
                    self.assertEqual(
                        baseline.account_identifier_hash,
                        changed.account_identifier_hash,
                    )
                    self.assertEqual(
                        baseline.entries[untouched_index].source_entry_hash,
                        changed.entries[untouched_index].source_entry_hash,
                    )

    def test_same_scalar_iban_and_other_choice_remains_material(self) -> None:
        """AccountIdentification4Choice discriminator is material even for equal text."""
        iban_account = {
            "choice": "iban",
            "id": _BASE_ACCOUNT_ID,
        }
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                other = self._parse(self._with_account(role, self.base_account))
                iban = self._parse(self._with_account(role, iban_account))
                target_index = self.helper._target_entry_index(role)
                other_entry = other.entries[target_index]
                iban_entry = iban.entries[target_index]
                other_detail = other_entry.entry_details[0]
                iban_detail = iban_entry.entry_details[0]
                self._assert_evidence_chain(other, other_entry, other_detail)
                self._assert_evidence_chain(iban, iban_entry, iban_detail)
                self._assert_financial_truth(role, other_entry, other_detail)
                self._assert_financial_truth(role, iban_entry, iban_detail)

                self.assertNotEqual(
                    other_entry.counterparty_evidence_hash,
                    iban_entry.counterparty_evidence_hash,
                )
                self.assertNotEqual(
                    other_detail.source_detail_hash,
                    iban_detail.source_detail_hash,
                )
                self.assertNotEqual(
                    other_entry.source_entry_hash,
                    iban_entry.source_entry_hash,
                )
                self.assertNotEqual(
                    other.normalized_payload_hash,
                    iban.normalized_payload_hash,
                )
                self.assertEqual(
                    other.account_identifier_hash,
                    iban.account_identifier_hash,
                )

    def test_account_identification_layout_is_representation_only(self) -> None:
        """Whitespace in counterparty account Id changes raw bytes, not semantics."""
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                baseline_payload = self._with_account(role, self.base_account)
                needle = b"                <Id>\n"
                self.assertGreaterEqual(baseline_payload.count(needle), 1)
                formatted_payload = baseline_payload.replace(
                    needle,
                    needle + b"                  \n",
                    1,
                )
                self.assertNotEqual(baseline_payload, formatted_payload)

                baseline = self._parse(baseline_payload)
                formatted = self._parse(formatted_payload)
                target_index = self.helper._target_entry_index(role)
                baseline_entry = baseline.entries[target_index]
                formatted_entry = formatted.entries[target_index]
                baseline_detail = baseline_entry.entry_details[0]
                formatted_detail = formatted_entry.entry_details[0]
                for value in (
                    baseline.source_artifact_hash,
                    formatted.source_artifact_hash,
                    baseline_entry.counterparty_evidence_hash,
                    formatted_entry.counterparty_evidence_hash,
                    baseline_detail.source_detail_hash,
                    formatted_detail.source_detail_hash,
                    baseline_entry.source_entry_hash,
                    formatted_entry.source_entry_hash,
                    baseline.normalized_payload_hash,
                    formatted.normalized_payload_hash,
                ):
                    self._assert_sha256(value)

                self.assertNotEqual(
                    baseline.source_artifact_hash,
                    formatted.source_artifact_hash,
                )
                self.assertEqual(
                    baseline_entry.counterparty_evidence_hash,
                    formatted_entry.counterparty_evidence_hash,
                )
                self.assertEqual(
                    baseline_detail.source_detail_hash,
                    formatted_detail.source_detail_hash,
                )
                self.assertEqual(
                    baseline_entry.source_entry_hash,
                    formatted_entry.source_entry_hash,
                )
                self.assertEqual(
                    baseline.normalized_payload_hash,
                    formatted.normalized_payload_hash,
                )

    def test_every_account_identification_variant_reaches_correction_boundary(self) -> None:
        """Accepted counterparty account identity cannot be silently replaced."""
        for role in ("debtor", "creditor"):
            baseline_payload = self._with_account(role, self.base_account)
            reference = self.helper._register_statement_account(baseline_payload)
            store = MemoryArtifactStore()
            accepted = party.accept_bank_statement_evidence(
                self.helper._command(
                    baseline_payload,
                    reference,
                    f"{role}-account-identification-baseline",
                ),
                party.posting.DATABASE_URL,
                self.helper.case.policy.tenant_reference,
                artifact_store=store,
            )
            self.assertFalse(accepted["replayed"])

            variants = self._variants()
            variants["same-scalar-iban-choice"] = {
                "choice": "iban",
                "id": _BASE_ACCOUNT_ID,
            }
            for semantic, changed_account in variants.items():
                with self.subTest(role=role, semantic=semantic):
                    changed_payload = self._with_account(role, changed_account)
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        _CORRECTION_ERROR,
                    ):
                        party.accept_bank_statement_evidence(
                            self.helper._command(
                                changed_payload,
                                reference,
                                f"{role}-account-identification-{semantic}",
                            ),
                            party.posting.DATABASE_URL,
                            self.helper.case.policy.tenant_reference,
                            artifact_store=store,
                        )

    def test_account_identification_remains_non_reversible_in_buyer_projection(self) -> None:
        """Counterparty account source identity changes digest evidence, not buyer fields."""
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                rich = self.helper._ingest_and_read_target_entry(
                    role,
                    self._with_account(role, self.base_account),
                    f"{role}-account-identification-rich-{uuid.uuid4().hex}",
                )
                absent = self.helper._ingest_and_read_target_entry(
                    role,
                    self._with_account(role, None),
                    f"{role}-account-identification-absent-{uuid.uuid4().hex}",
                )
                self._assert_sha256(rich["counterparty_evidence_hash"])
                self._assert_sha256(absent["counterparty_evidence_hash"])
                self._assert_sha256(rich["source_entry_hash"])
                self._assert_sha256(absent["source_entry_hash"])
                self.assertNotEqual(
                    rich["counterparty_evidence_hash"],
                    absent["counterparty_evidence_hash"],
                )
                self.assertNotEqual(rich["source_entry_hash"], absent["source_entry_hash"])

                rich_public = self.helper._public_projection(rich)
                absent_public = self.helper._public_projection(absent)
                self.assertEqual(rich_public, absent_public)
                source_values = {
                    _BASE_ACCOUNT_ID,
                    "BANK",
                    "Counterparty Account Registry",
                }
                buyer_values = set(self._scalar_leaves(rich_public))
                self.assertTrue(source_values.isdisjoint(buyer_values))

    def _variants(self) -> dict[str, dict[str, object] | None]:
        """Change one GenericAccountIdentification1 source fact at a time."""
        variants: dict[str, dict[str, object] | None] = {}

        changed_id = copy.deepcopy(self.base_account)
        changed_id["id"] = _CHANGED_ACCOUNT_ID
        variants["other-id-value"] = changed_id

        changed_scheme = copy.deepcopy(self.base_account)
        scheme = self._scheme(changed_scheme)
        scheme["proprietary"] = "NATIONAL-ACCOUNT"
        variants["scheme-proprietary-value"] = changed_scheme

        coded_scheme = copy.deepcopy(self.base_account)
        coded_scheme["scheme"] = {"code": "BANK"}
        variants["same-scalar-scheme-choice"] = coded_scheme

        scheme_absent = copy.deepcopy(self.base_account)
        scheme_absent.pop("scheme")
        variants["scheme-absent"] = scheme_absent

        issuer_changed = copy.deepcopy(self.base_account)
        issuer_changed["issuer"] = "Alternate Account Registry"
        variants["issuer-value"] = issuer_changed

        issuer_absent = copy.deepcopy(self.base_account)
        issuer_absent.pop("issuer")
        variants["issuer-absent"] = issuer_absent

        variants["account-id-absent"] = None
        return variants

    @staticmethod
    def _scheme(account: dict[str, object]) -> dict[str, object]:
        """Return the account scheme choice or fail closed when the fixture drifts."""
        value = account.get("scheme")
        if not isinstance(value, dict):
            raise AssertionError("focused counterparty account requires SchmeNm")
        return value

    def _with_account(
        self,
        role: str,
        account: dict[str, object] | None,
    ) -> bytes:
        """Insert optional DbtrAcct/CdtrAcct after a stable direct party."""
        payload = self.helper._with_role_identity(
            role,
            "organisation",
            self.helper.base_identifier,
        ).decode("utf-8")
        role_tag = "Dbtr" if role == "debtor" else "Cdtr"
        account_tag = "DbtrAcct" if role == "debtor" else "CdtrAcct"
        marker = f"              </{role_tag}>\n            </RltdPties>"
        if payload.count(marker) != 1:
            raise AssertionError(f"focused {role_tag} marker must be unique")
        if account is None:
            return payload.encode("utf-8")
        account_xml = self._account_xml(account_tag, account)
        return payload.replace(
            marker,
            f"              </{role_tag}>\n{account_xml}            </RltdPties>",
            1,
        ).encode("utf-8")

    @classmethod
    def _account_xml(cls, tag: str, account: dict[str, object]) -> str:
        """Serialize CashAccount40.Id in TransactionParties12 sequence order."""
        choice = account.get("choice")
        identifier = account.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise AssertionError("counterparty account identification requires source text")
        lines = [f"              <{tag}>\n", "                <Id>\n"]
        if choice == "iban":
            lines.append(f"                  <IBAN>{identifier}</IBAN>\n")
        elif choice == "other":
            lines.extend(
                [
                    "                  <Othr>\n",
                    f"                    <Id>{identifier}</Id>\n",
                ]
            )
            scheme = account.get("scheme")
            if scheme is not None:
                if not isinstance(scheme, dict):
                    raise AssertionError("SchmeNm must be a structured choice")
                lines.append("                    <SchmeNm>\n")
                if isinstance(scheme.get("code"), str):
                    lines.append(f"                      <Cd>{scheme['code']}</Cd>\n")
                elif isinstance(scheme.get("proprietary"), str):
                    lines.append(
                        f"                      <Prtry>{scheme['proprietary']}</Prtry>\n"
                    )
                else:
                    raise AssertionError("SchmeNm requires Cd or Prtry")
                lines.append("                    </SchmeNm>\n")
            issuer = account.get("issuer")
            if issuer is not None:
                if not isinstance(issuer, str):
                    raise AssertionError("counterparty account Issr must be text")
                lines.append(f"                    <Issr>{issuer}</Issr>\n")
            lines.append("                  </Othr>\n")
        else:
            raise AssertionError(f"unsupported AccountIdentification4Choice: {choice}")
        lines.extend(["                </Id>\n", f"              </{tag}>\n"])
        return "".join(lines)

    @staticmethod
    def _parse(payload: bytes) -> object:
        """Parse one V14 source through the supported statement boundary."""
        return party.parse_bank_statement_payload(
            payload,
            party.CAMT053_MESSAGE_DEFINITION,
        )

    @classmethod
    def _scalar_leaves(cls, value: object) -> list[str]:
        """Return exact buyer-visible scalar leaves without substring heuristics."""
        leaves: list[str] = []
        if isinstance(value, dict):
            for item in value.values():
                leaves.extend(cls._scalar_leaves(item))
        elif isinstance(value, list):
            for item in value:
                leaves.extend(cls._scalar_leaves(item))
        elif value is not None:
            leaves.append(str(value))
        return leaves

    def _assert_evidence_chain(self, statement: object, entry: object, detail: object) -> None:
        """Require canonical source identities across the retained evidence chain."""
        for value in (
            getattr(entry, "counterparty_evidence_hash"),
            getattr(detail, "source_detail_hash"),
            getattr(entry, "source_entry_hash"),
            getattr(statement, "normalized_payload_hash"),
            getattr(statement, "account_identifier_hash"),
        ):
            self._assert_sha256(value)

    @staticmethod
    def _assert_sha256(value: object) -> None:
        """Require one canonical SHA-256 identity."""
        if not isinstance(value, str) or _HASH_PATTERN.fullmatch(value) is None:
            raise AssertionError(f"expected canonical SHA-256 identity, got {value!r}")

    def _assert_financial_truth(self, role: str, entry: object, detail: object) -> None:
        """Keep counterparty account evidence independent from accounting facts."""
        self.assertEqual(
            getattr(entry, "entry_amount"),
            self.helper._expected_entry_amount(role),
        )
        self.assertEqual(getattr(entry, "entry_currency_code"), "KRW")
        self.assertEqual(
            getattr(detail, "detail_amount"),
            self.helper._expected_detail_amount(role),
        )
        self.assertEqual(getattr(detail, "detail_currency_code"), "KRW")


if __name__ == "__main__":
    unittest.main()
