"""Focused REDs for SecuritiesAccount19 optional-field evidence semantics."""

from __future__ import annotations

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
from tests import test_postgres_bank_statement_detail_safekeeping_account_evidence_red as safekeeping
from tests import test_postgres_posting as posting

_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailSafekeepingAccountOptionalityReviewRedTests(unittest.TestCase):
    """Preserve optional SecuritiesAccount19 presence without inventing securities truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one source-real camt.053 statement and optionality variants."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.marker = (
            "            <RmtInf>\n"
            "              <Ustrd>Invoice 1001</Ustrd>\n"
            "            </RmtInf>\n"
        )
        self.assertEqual(self.fixture.count(self.marker), 1)

        self.base = {
            "identification": "SAFEKEEP-ACC-001",
            "type": {
                "identification": "CUST",
                "issuer": "CWL-BANK",
                "scheme_name": "CUSTODY",
            },
            "name": "Client custody KRW",
        }
        self.variants: dict[str, dict[str, object] | None] = {
            "safekeeping-account-absent": None,
            "type-absent": {
                "identification": self.base["identification"],
                "name": self.base["name"],
            },
            "type-scheme-name-absent": {
                "identification": self.base["identification"],
                "type": {
                    "identification": "CUST",
                    "issuer": "CWL-BANK",
                },
                "name": self.base["name"],
            },
            "name-absent": {
                "identification": self.base["identification"],
                "type": dict(self.base["type"]),
            },
        }
        self.base_payload = self._payload(self.base)
        self.variant_payloads = {
            label: self._payload(value) for label, value in self.variants.items()
        }
        self.base_statement = self._parse(self.base_payload)
        self.bank_account_reference = self._register_statement_account(self.base_payload)
        self.store = MemoryArtifactStore()

    def test_optional_presence_changes_canonical_safekeeping_evidence(self) -> None:
        """SfkpgAcct, Tp, Tp/SchmeNm and Nm presence are material source facts."""
        base_entry = self.base_statement.entries[0]
        base_detail = base_entry.entry_details[0]
        base_sibling_entry = self.base_statement.entries[1]
        base_digest = getattr(base_detail, "safekeeping_account_evidence_hash", None)
        self._assert_sha256(base_digest)
        self.assertEqual(base_digest, safekeeping.BankStatementDetailSafekeepingAccountEvidenceRedTests._expected_hash(self.base))
        for value in (
            base_detail.source_detail_hash,
            base_entry.source_entry_hash,
            self.base_statement.normalized_payload_hash,
            self.base_statement.account_identifier_hash,
            base_sibling_entry.source_entry_hash,
        ):
            self._assert_sha256(value)
        self._assert_exact_amount(base_entry, base_detail)

        for label, expected in self.variants.items():
            with self.subTest(semantic=label):
                changed = self._parse(self.variant_payloads[label])
                changed_entry = changed.entries[0]
                changed_detail = changed_entry.entry_details[0]
                changed_sibling_entry = changed.entries[1]
                changed_digest = getattr(
                    changed_detail,
                    "safekeeping_account_evidence_hash",
                    None,
                )

                if expected is None:
                    self.assertIsNone(changed_digest)
                else:
                    self._assert_sha256(changed_digest)
                    self.assertEqual(
                        changed_digest,
                        safekeeping.BankStatementDetailSafekeepingAccountEvidenceRedTests._expected_hash(expected),
                    )
                for value in (
                    changed_detail.source_detail_hash,
                    changed_entry.source_entry_hash,
                    changed.normalized_payload_hash,
                    changed.account_identifier_hash,
                    changed_sibling_entry.source_entry_hash,
                ):
                    self._assert_sha256(value)
                self.assertNotEqual(base_digest, changed_digest)
                self.assertNotEqual(
                    base_detail.source_detail_hash,
                    changed_detail.source_detail_hash,
                )
                self.assertNotEqual(
                    base_entry.source_entry_hash,
                    changed_entry.source_entry_hash,
                )
                self.assertNotEqual(
                    self.base_statement.normalized_payload_hash,
                    changed.normalized_payload_hash,
                )
                self.assertEqual(
                    self.base_statement.account_identifier_hash,
                    changed.account_identifier_hash,
                )
                self.assertEqual(
                    base_sibling_entry.source_entry_hash,
                    changed_sibling_entry.source_entry_hash,
                )
                self._assert_exact_amount(changed_entry, changed_detail)

    def test_optional_presence_changes_reach_explicit_correction_boundary(self) -> None:
        """Accepted safekeeping optionality cannot silently change on replay."""
        accepted = accept_bank_statement_evidence(
            self._command(self.base_payload, self.bank_account_reference, "baseline"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        for label, payload in self.variant_payloads.items():
            with self.subTest(semantic=label):
                with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
                    accept_bank_statement_evidence(
                        self._command(
                            payload,
                            self.bank_account_reference,
                            f"changed-{label}",
                        ),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=self.store,
                    )

    def test_buyer_read_preserves_optional_absence_without_synthesizing_fields(self) -> None:
        """Lookup round-trips the source population instead of manufacturing optional facts."""
        for label, expected in self.variants.items():
            with self.subTest(semantic=label):
                payload = self._with_unique_statement_id(
                    self.variant_payloads[label],
                    label,
                )
                reference = self._register_statement_account(payload)
                accepted = accept_bank_statement_evidence(
                    self._command(payload, reference, f"lookup-{label}"),
                    posting.DATABASE_URL,
                    self.case.policy.tenant_reference,
                    artifact_store=MemoryArtifactStore(),
                )
                document = lookup_bank_statement_entries(
                    posting.DATABASE_URL,
                    self.case.policy.tenant_reference,
                    str(accepted["bank_statement_record_id"]),
                )
                entry = document["bank_statement_entries"][0]
                detail = entry["entry_details"][0]
                actual = detail.get("safekeeping_account")

                if expected is None:
                    self.assertIsNone(actual)
                    self.assertIsNone(detail.get("safekeeping_account_evidence_hash"))
                else:
                    self.assertEqual(actual, expected)
                    self.assertEqual(
                        detail.get("safekeeping_account_evidence_hash"),
                        safekeeping.BankStatementDetailSafekeepingAccountEvidenceRedTests._expected_hash(expected),
                    )
                    if label == "type-absent":
                        self.assertNotIn("type", actual)
                    elif label == "type-scheme-name-absent":
                        account_type = actual.get("type")
                        if not isinstance(account_type, dict):
                            raise AssertionError("type must remain a mapping when present")
                        self.assertNotIn("scheme_name", account_type)
                    elif label == "name-absent":
                        self.assertNotIn("name", actual)

                self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
                self.assertEqual(entry["entry_currency_code"], "KRW")
                self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
                self.assertEqual(detail["detail_currency_code"], "KRW")

    def _payload(self, value: dict[str, object] | None) -> bytes:
        """Insert one optional SecuritiesAccount19 population after the first RmtInf."""
        if value is None:
            return self.fixture.encode("utf-8")
        xml = self._safekeeping_account_xml(value)
        return self.fixture.replace(self.marker, self.marker + xml, 1).encode("utf-8")

    @staticmethod
    def _safekeeping_account_xml(value: dict[str, object]) -> str:
        """Serialize SecuritiesAccount19 while preserving its optional Tp/SchmeNm/Nm fields."""
        identification = value.get("identification")
        if not isinstance(identification, str):
            raise AssertionError("SecuritiesAccount19 requires Id text")
        lines = [
            "            <SfkpgAcct>",
            f"              <Id>{identification}</Id>",
        ]

        account_type = value.get("type")
        if account_type is not None:
            if not isinstance(account_type, dict):
                raise AssertionError("SecuritiesAccount19 Tp must be a mapping when present")
            type_id = account_type.get("identification")
            issuer = account_type.get("issuer")
            if not isinstance(type_id, str) or not isinstance(issuer, str):
                raise AssertionError("GenericIdentification30 requires Id and Issr")
            lines.extend(
                [
                    "              <Tp>",
                    f"                <Id>{type_id}</Id>",
                    f"                <Issr>{issuer}</Issr>",
                ]
            )
            scheme_name = account_type.get("scheme_name")
            if scheme_name is not None:
                if not isinstance(scheme_name, str):
                    raise AssertionError("GenericIdentification30 SchmeNm must be text")
                lines.append(f"                <SchmeNm>{scheme_name}</SchmeNm>")
            lines.append("              </Tp>")

        name = value.get("name")
        if name is not None:
            if not isinstance(name, str):
                raise AssertionError("SecuritiesAccount19 Nm must be text")
            lines.append(f"              <Nm>{name}</Nm>")
        lines.append("            </SfkpgAcct>")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _parse(payload: bytes) -> object:
        """Parse one V14 statement through the supported adapter boundary."""
        return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    def _register_statement_account(self, payload: bytes) -> str:
        """Register only the statement-owner account required for supported ingest."""
        statement = self._parse(payload)
        reference = f"urn:cwl:bank_account:safekeeping-optionality:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": reference,
                "account_currency_code": statement.account_currency_code,
                "account_identifier_hash": statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        return reference

    def _command(
        self,
        payload: bytes,
        account_reference: str,
        suffix: str,
    ) -> dict[str, object]:
        """Build one supported ingest command with an isolated replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": account_reference,
            "ingestion_idempotency_key": (
                f"detail-safekeeping-optionality-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

    def _with_unique_statement_id(self, payload: bytes, suffix: str) -> bytes:
        """Give lookup variants independent statement identity without changing owner truth."""
        old = b"<Id>STMT-2026-08-24-001</Id>"
        new = f"<Id>STMT-SAFEKEEP-{suffix}-{uuid.uuid4().hex[:12]}</Id>".encode("utf-8")
        if payload.count(old) != 1:
            raise AssertionError("canonical statement Id marker must occur exactly once")
        return payload.replace(old, new, 1)

    @staticmethod
    def _assert_sha256(value: object) -> None:
        """Require canonical purpose-bound SHA-256 evidence."""
        if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
            raise AssertionError("evidence hash must be canonical SHA-256")

    @staticmethod
    def _assert_exact_amount(entry: object, detail: object) -> None:
        """Keep exact accounting facts independent from safekeeping provenance optionality."""
        if getattr(entry, "entry_amount", None) != Decimal("25000.00"):
            raise AssertionError("entry amount must remain exactly 25000.00")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("entry currency must remain KRW")
        if getattr(detail, "detail_amount", None) != Decimal("25000.00"):
            raise AssertionError("detail amount must remain exactly 25000.00")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("detail currency must remain KRW")


if __name__ == "__main__":
    unittest.main()
