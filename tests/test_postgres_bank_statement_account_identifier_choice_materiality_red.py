"""PostgreSQL RED for account-identification choice materiality."""

from __future__ import annotations

import unittest
import uuid

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    accept_bank_account_record,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting


class BankStatementAccountIdentifierChoiceMaterialityRedTests(unittest.TestCase):
    """Keep typed ISO account identities distinct despite matching identifier text."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare lawful account-identification variants with one shared lexical ID."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.marker = (
            "<Id>\n"
            "          <Othr>\n"
            "            <Id>acct-opaque-fixture-only</Id>\n"
            "          </Othr>\n"
            "        </Id>"
        )
        self.assertEqual(self.fixture.count(self.marker), 1)
        self.same_identifier = "DE89370400440532013000"
        self.iban_payload = self._payload_with_account_identifier(
            "<Id>\n"
            f"          <IBAN>{self.same_identifier}</IBAN>\n"
            "        </Id>"
        )
        self.other_payload = self._payload_with_account_identifier(
            self._other_identifier_xml()
        )
        self.other_bban_payload = self._payload_with_account_identifier(
            self._other_identifier_xml(scheme_code="BBAN", issuer="BANK-A")
        )
        self.other_upic_payload = self._payload_with_account_identifier(
            self._other_identifier_xml(scheme_code="UPIC", issuer="BANK-A")
        )
        self.other_bban_proprietary_payload = self._payload_with_account_identifier(
            self._other_identifier_xml(
                scheme_proprietary="BBAN",
                issuer="BANK-A",
            )
        )
        self.other_other_issuer_payload = self._payload_with_account_identifier(
            self._other_identifier_xml(scheme_code="BBAN", issuer="BANK-B")
        )
        self.iban_statement = self._parse(self.iban_payload)
        self.other_statement = self._parse(self.other_payload)
        self.other_bban_statement = self._parse(self.other_bban_payload)
        self.other_upic_statement = self._parse(self.other_upic_payload)
        self.other_bban_proprietary_statement = self._parse(
            self.other_bban_proprietary_payload
        )
        self.other_other_issuer_statement = self._parse(
            self.other_other_issuer_payload
        )
        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        self.store = MemoryArtifactStore()

    def test_identifier_choice_discriminator_is_material_to_account_and_statement_identity(self) -> None:
        """The same text under IBAN and Other cannot provenance-alias."""
        self._assert_material_identity_difference(
            self.iban_statement,
            self.other_statement,
        )

    def test_other_scheme_name_is_material_to_account_and_statement_identity(self) -> None:
        """A changed Other scheme cannot alias when its account ID text is unchanged."""
        self._assert_material_identity_difference(
            self.other_bban_statement,
            self.other_upic_statement,
        )

    def test_other_scheme_choice_is_material_to_account_and_statement_identity(self) -> None:
        """Coded and proprietary schemes with equal text remain distinct evidence."""
        self._assert_material_identity_difference(
            self.other_bban_statement,
            self.other_bban_proprietary_statement,
        )

    def test_other_issuer_is_material_to_account_and_statement_identity(self) -> None:
        """A changed Other issuer cannot alias when ID and scheme are unchanged."""
        self._assert_material_identity_difference(
            self.other_bban_statement,
            self.other_other_issuer_statement,
        )

    def test_other_choice_cannot_reuse_an_account_registered_from_iban_choice(self) -> None:
        """Supported ingest binds the durable bank account to the exact identifier choice."""
        self._assert_variant_rejected_from_registered_identity(
            registered_statement=self.iban_statement,
            registered_payload=self.iban_payload,
            hostile_payload=self.other_payload,
            registered_suffix="iban",
            hostile_suffix="other",
        )

    def test_other_scheme_cannot_reuse_an_account_registered_from_another_scheme(self) -> None:
        """Supported ingest binds the durable account to retained Other scheme evidence."""
        self._assert_variant_rejected_from_registered_identity(
            registered_statement=self.other_bban_statement,
            registered_payload=self.other_bban_payload,
            hostile_payload=self.other_upic_payload,
            registered_suffix="other-bban",
            hostile_suffix="other-upic",
        )

    def test_other_scheme_choice_cannot_reuse_an_account_registered_from_code(self) -> None:
        """Supported ingest binds the durable account to the scheme choice itself."""
        self._assert_variant_rejected_from_registered_identity(
            registered_statement=self.other_bban_statement,
            registered_payload=self.other_bban_payload,
            hostile_payload=self.other_bban_proprietary_payload,
            registered_suffix="scheme-code",
            hostile_suffix="scheme-proprietary",
        )

    def test_other_issuer_cannot_reuse_an_account_registered_from_another_issuer(self) -> None:
        """Supported ingest binds the durable account to retained Other issuer evidence."""
        self._assert_variant_rejected_from_registered_identity(
            registered_statement=self.other_bban_statement,
            registered_payload=self.other_bban_payload,
            hostile_payload=self.other_other_issuer_payload,
            registered_suffix="issuer-a",
            hostile_suffix="issuer-b",
        )

    def _assert_material_identity_difference(self, left: object, right: object) -> None:
        """Require one typed source-evidence change to propagate through identity hashes."""
        self.assertNotEqual(left.source_artifact_hash, right.source_artifact_hash)
        self.assertNotEqual(left.account_identifier_hash, right.account_identifier_hash)
        self.assertNotEqual(left.normalized_payload_hash, right.normalized_payload_hash)

    def _assert_variant_rejected_from_registered_identity(
        self,
        *,
        registered_statement: object,
        registered_payload: bytes,
        hostile_payload: bytes,
        registered_suffix: str,
        hostile_suffix: str,
    ) -> None:
        """Prove supported ingest cannot reinterpret a typed account identity in place."""
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": "KRW",
                "account_identifier_hash": registered_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        first = accept_bank_statement_evidence(
            self._command(registered_payload, registered_suffix),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(first["replayed"])

        with self.assertRaisesRegex(
            AccountingValidationError,
            r"^statement account identifier does not match the registered bank account\.",
        ):
            accept_bank_statement_evidence(
                self._command(hostile_payload, hostile_suffix),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def _payload_with_account_identifier(self, account_identifier_xml: str) -> bytes:
        """Replace only the canonical statement account-identification choice."""
        return self.fixture.replace(
            self.marker,
            account_identifier_xml,
            1,
        ).encode("utf-8")

    def _other_identifier_xml(
        self,
        *,
        scheme_code: str | None = None,
        scheme_proprietary: str | None = None,
        issuer: str | None = None,
    ) -> str:
        """Build one schema-shaped GenericAccountIdentification1 fixture fragment."""
        self.assertFalse(
            scheme_code is not None and scheme_proprietary is not None,
            "AccountSchemeName1Choice fixture must select exactly one scheme form.",
        )
        lines = [
            "<Id>",
            "          <Othr>",
            f"            <Id>{self.same_identifier}</Id>",
        ]
        if scheme_code is not None or scheme_proprietary is not None:
            lines.append("            <SchmeNm>")
            if scheme_code is not None:
                lines.append(f"              <Cd>{scheme_code}</Cd>")
            else:
                lines.append(f"              <Prtry>{scheme_proprietary}</Prtry>")
            lines.append("            </SchmeNm>")
        if issuer is not None:
            lines.append(f"            <Issr>{issuer}</Issr>")
        lines.extend(["          </Othr>", "        </Id>"])
        return "\n".join(lines)

    @staticmethod
    def _parse(payload: bytes) -> object:
        """Parse one candidate with the repository-pinned camt.053 revision."""
        return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"account-id-choice-materiality-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
