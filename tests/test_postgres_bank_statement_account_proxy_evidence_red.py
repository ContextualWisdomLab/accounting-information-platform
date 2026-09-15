"""PostgreSQL REDs for camt.053 statement-account proxy evidence."""

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
    lookup_bank_statement,
    parse_bank_statement_payload,
)
from accounting_information_platform import bank_statement
from tests import test_postgres_posting as posting

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_ACCOUNT_PROXY_EVIDENCE_PURPOSE = "camt.053.001.14/Stmt/Acct/Prxy"


class BankStatementAccountProxyEvidenceRedTests(unittest.TestCase):
    """Retain Acct/Prxy identity evidence without collapsing proxy type semantics."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare lawful proxy variants over the same primary account identifier."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "        <Ccy>KRW</Ccy>\n      </Acct>"
        self.assertEqual(fixture.count(marker), 1)

        self.email_proxy = "treasury@example.com"
        self.other_email_proxy = "reserve@example.com"
        self.code_payload = self._with_account_proxy(
            fixture,
            marker,
            type_choice="Cd",
            type_value="EMAL",
            proxy_identifier=self.email_proxy,
        )
        self.phone_type_payload = self._with_account_proxy(
            fixture,
            marker,
            type_choice="Cd",
            type_value="TELE",
            proxy_identifier=self.email_proxy,
        )
        self.proprietary_payload = self._with_account_proxy(
            fixture,
            marker,
            type_choice="Prtry",
            type_value="EMAL",
            proxy_identifier=self.email_proxy,
        )
        self.changed_identifier_payload = self._with_account_proxy(
            fixture,
            marker,
            type_choice="Cd",
            type_value="EMAL",
            proxy_identifier=self.other_email_proxy,
        )

        formatting_anchor = (
            "        <Prxy>\n"
            "          <Tp>\n"
            "            <Cd>EMAL</Cd>\n"
        ).encode("utf-8")
        self.assertEqual(self.code_payload.count(formatting_anchor), 1)
        self.reformatted_code_payload = self.code_payload.replace(
            formatting_anchor,
            (
                "        <Prxy>\n"
                "          \n"
                "          <Tp>\n"
                "            <Cd>EMAL</Cd>\n"
            ).encode("utf-8"),
            1,
        )

        self.code_statement = parse_bank_statement_payload(
            self.code_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.phone_type_statement = parse_bank_statement_payload(
            self.phone_type_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.proprietary_statement = parse_bank_statement_payload(
            self.proprietary_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.changed_identifier_statement = parse_bank_statement_payload(
            self.changed_identifier_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.reformatted_code_statement = parse_bank_statement_payload(
            self.reformatted_code_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.code_statement.account_currency_code,
                "account_identifier_hash": self.code_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_proxy_identifier_is_material_statement_evidence(self) -> None:
        """Changing only Prxy/Id changes proxy and normalized-statement evidence."""
        self._assert_proxy_evidence_difference(
            self.code_statement,
            self.changed_identifier_statement,
            left_choice="Cd",
            left_type="EMAL",
            left_identifier=self.email_proxy,
            right_choice="Cd",
            right_type="EMAL",
            right_identifier=self.other_email_proxy,
        )

    def test_proxy_type_value_is_material_when_identifier_matches(self) -> None:
        """Changing Prxy/Tp/Cd changes evidence even with an identical proxy identifier."""
        self._assert_proxy_evidence_difference(
            self.code_statement,
            self.phone_type_statement,
            left_choice="Cd",
            left_type="EMAL",
            left_identifier=self.email_proxy,
            right_choice="Cd",
            right_type="TELE",
            right_identifier=self.email_proxy,
        )

    def test_proxy_type_choice_discriminator_is_material_when_text_matches(self) -> None:
        """Prxy/Tp/Cd and Prtry cannot provenance-alias merely because text matches."""
        self._assert_proxy_evidence_difference(
            self.code_statement,
            self.proprietary_statement,
            left_choice="Cd",
            left_type="EMAL",
            left_identifier=self.email_proxy,
            right_choice="Prtry",
            right_type="EMAL",
            right_identifier=self.email_proxy,
        )

    def test_source_formatting_cannot_change_semantically_equal_proxy_evidence(self) -> None:
        """Insignificant XML formatting must not leak into normalized proxy identity."""
        expected_hash = self._expected_account_proxy_hash(
            "Cd",
            "EMAL",
            self.email_proxy,
        )
        self.assertNotEqual(
            self.code_statement.source_artifact_hash,
            self.reformatted_code_statement.source_artifact_hash,
        )
        self.assertEqual(
            self.code_statement.account_identifier_hash,
            self.reformatted_code_statement.account_identifier_hash,
        )
        self.assertEqual(
            getattr(self.code_statement, "account_proxy_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(self.reformatted_code_statement, "account_proxy_evidence_hash", None),
            expected_hash,
        )
        self._assert_normalized_hash_binding(self.code_statement, expected_hash)
        self._assert_normalized_hash_binding(
            self.reformatted_code_statement,
            expected_hash,
        )
        self.assertEqual(
            self.code_statement.normalized_payload_hash,
            self.reformatted_code_statement.normalized_payload_hash,
        )

    def test_changed_account_proxy_requires_statement_correction(self) -> None:
        """The same statement identity cannot silently replay changed proxy evidence."""
        first = accept_bank_statement_evidence(
            self._command(self.code_payload, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(first["replayed"])

        with self.assertRaisesRegex(
            AccountingValidationError,
            (
                r"^statement identity already exists with different entry evidence\. "
                r"Use an explicit correction contract, then retry ingest\.$"
            ),
        ):
            accept_bank_statement_evidence(
                self._command(self.changed_identifier_payload, "second"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_statement_lookup_exposes_same_purpose_bound_account_proxy_hash(self) -> None:
        """Buyer reads expose the exact proxy-evidence digest admitted at normalization."""
        expected_hash = self._expected_account_proxy_hash(
            "Cd",
            "EMAL",
            self.email_proxy,
        )
        self.assertEqual(
            getattr(self.code_statement, "account_proxy_evidence_hash", None),
            expected_hash,
        )

        accepted = accept_bank_statement_evidence(
            self._command(self.code_payload, "lookup"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        document = lookup_bank_statement(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        self.assertEqual(document.get("account_proxy_evidence_hash"), expected_hash)

    def _assert_proxy_evidence_difference(
        self,
        left: object,
        right: object,
        *,
        left_choice: str,
        left_type: str,
        left_identifier: str,
        right_choice: str,
        right_type: str,
        right_identifier: str,
    ) -> None:
        """Bind proxy and statement digests to exact parsed Prxy semantics."""
        expected_left = self._expected_account_proxy_hash(
            left_choice,
            left_type,
            left_identifier,
        )
        expected_right = self._expected_account_proxy_hash(
            right_choice,
            right_type,
            right_identifier,
        )
        left_hash = getattr(left, "account_proxy_evidence_hash", None)
        right_hash = getattr(right, "account_proxy_evidence_hash", None)

        self.assertNotEqual(left.source_artifact_hash, right.source_artifact_hash)
        self.assertEqual(left.account_identifier_hash, right.account_identifier_hash)
        self.assertRegex(expected_left, _HASH_PATTERN)
        self.assertRegex(expected_right, _HASH_PATTERN)
        self.assertEqual(left_hash, expected_left)
        self.assertEqual(right_hash, expected_right)
        self.assertNotEqual(expected_left, expected_right)
        self._assert_normalized_hash_binding(left, expected_left)
        self._assert_normalized_hash_binding(right, expected_right)
        self.assertNotEqual(left.normalized_payload_hash, right.normalized_payload_hash)

    @staticmethod
    def _assert_normalized_hash_binding(statement: object, proxy_hash: str) -> None:
        """Bind normalized statement identity to semantic proxy evidence only."""
        projection = dict(bank_statement._normalized_payload(statement))
        if projection.get("account_proxy_evidence_hash") != proxy_hash:
            raise AssertionError(
                "canonical normalized statement projection must carry the exact "
                "account_proxy_evidence_hash"
            )
        if "source_artifact_hash" in projection:
            raise AssertionError(
                "canonical normalized statement projection must not carry raw artifact identity"
            )
        preimage = json.dumps(
            projection,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        expected_statement_hash = f"sha256:{hashlib.sha256(preimage).hexdigest()}"
        if statement.normalized_payload_hash != expected_statement_hash:
            raise AssertionError(
                "normalized_payload_hash must be the digest of the canonical normalized projection"
            )

    @staticmethod
    def _expected_account_proxy_hash(
        type_choice: str,
        type_value: str,
        proxy_identifier: str,
    ) -> str:
        """Return the digest of only the admitted Prxy type-choice and identifier semantics."""
        preimage = json.dumps(
            {
                "evidence_type": _ACCOUNT_PROXY_EVIDENCE_PURPOSE,
                "identifier": proxy_identifier,
                "type_choice": type_choice,
                "type_value": type_value,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _with_account_proxy(
        fixture: str,
        marker: str,
        *,
        type_choice: str,
        type_value: str,
        proxy_identifier: str,
    ) -> bytes:
        """Insert one lawful ProxyAccountIdentification1 after account currency."""
        return fixture.replace(
            marker,
            "        <Ccy>KRW</Ccy>\n"
            "        <Prxy>\n"
            "          <Tp>\n"
            f"            <{type_choice}>{type_value}</{type_choice}>\n"
            "          </Tp>\n"
            f"          <Id>{proxy_identifier}</Id>\n"
            "        </Prxy>\n"
            "      </Acct>",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"account-proxy-evidence-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
