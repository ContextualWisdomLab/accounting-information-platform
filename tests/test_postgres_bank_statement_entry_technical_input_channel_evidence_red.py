"""PostgreSQL REDs for camt.053 entry technical-input-channel provenance."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
import uuid
from unittest.mock import patch

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
_ENTRY_TECHNICAL_INPUT_CHANNEL_PURPOSE = (
    "camt.053.001.14/Stmt/Ntry/TechInptChanl"
)
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementEntryTechnicalInputChannelEvidenceRedTests(unittest.TestCase):
    """Retain bank-reported input-channel provenance without making it accounting truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare source-real statements that isolate one TechInptChanl semantic."""
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
        self.fixture = fixture
        self.marker = marker
        self.assertEqual(fixture.count(marker), 1)

        self.web_code_payload = self._with_technical_input_channel(
            fixture,
            marker,
            channel_choice="Cd",
            channel_value="WEBI",
        )
        self.fax_code_payload = self._with_technical_input_channel(
            fixture,
            marker,
            channel_choice="Cd",
            channel_value="FAXI",
        )
        self.proprietary_web_payload = self._with_technical_input_channel(
            fixture,
            marker,
            channel_choice="Prtry",
            channel_value="WEBI",
        )

        formatting_anchor = (
            "        <TechInptChanl>\n"
            "          <Cd>WEBI</Cd>\n"
            "        </TechInptChanl>\n"
        ).encode("utf-8")
        self.assertEqual(self.web_code_payload.count(formatting_anchor), 1)
        self.reformatted_payload = self.web_code_payload.replace(
            formatting_anchor,
            (
                "        <TechInptChanl><Cd>WEBI</Cd></TechInptChanl>\n"
            ).encode("utf-8"),
            1,
        )

        self.web_code_statement = parse_bank_statement_payload(
            self.web_code_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.fax_code_statement = parse_bank_statement_payload(
            self.fax_code_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.proprietary_web_statement = parse_bank_statement_payload(
            self.proprietary_web_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.reformatted_statement = parse_bank_statement_payload(
            self.reformatted_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.web_code_statement.account_currency_code,
                "account_identifier_hash": self.web_code_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_technical_input_channel_value_and_choice_are_material_entry_evidence(self) -> None:
        """Channel value and TechnicalInputChannel1Choice discriminator are material."""
        variants = (
            (
                self.web_code_statement,
                self._expected_channel_hash("Cd", "WEBI"),
            ),
            (
                self.fax_code_statement,
                self._expected_channel_hash("Cd", "FAXI"),
            ),
            (
                self.proprietary_web_statement,
                self._expected_channel_hash("Prtry", "WEBI"),
            ),
        )

        hashes: list[str] = []
        for statement, expected_hash in variants:
            with self.subTest(expected_hash=expected_hash):
                entry = statement.entries[0]
                self.assertRegex(expected_hash, _HASH_PATTERN)
                self.assertEqual(
                    getattr(entry, "entry_technical_input_channel_evidence_hash", None),
                    expected_hash,
                )
                self._assert_entry_hash_binding(entry, expected_hash)
                hashes.append(expected_hash)

        self.assertEqual(len(set(hashes)), len(hashes))
        for changed_statement in (
            self.fax_code_statement,
            self.proprietary_web_statement,
        ):
            self.assertEqual(
                self.web_code_statement.account_identifier_hash,
                changed_statement.account_identifier_hash,
            )
            self.assertNotEqual(
                self.web_code_statement.entries[0].source_entry_hash,
                changed_statement.entries[0].source_entry_hash,
            )
            self.assertNotEqual(
                self.web_code_statement.normalized_payload_hash,
                changed_statement.normalized_payload_hash,
            )
            self.assertEqual(
                self.web_code_statement.entries[1].source_entry_hash,
                changed_statement.entries[1].source_entry_hash,
            )

    def test_code_choice_routes_pinned_external_code_evidence_before_normalization(self) -> None:
        """TechInptChanl/Cd participates in versioned external-code admission."""
        hostile = self._with_technical_input_channel(
            self.fixture,
            self.marker,
            channel_choice="Cd",
            channel_value="ZZZZ",
        )
        schema_artifact = self._artifact(
            "message_schema",
            "iso20022/fixtures/camt.053.001.14.xsd",
            "a",
            "camt.053.001.14",
        )
        external_code_artifact = self._artifact(
            "iso20022_external_code_sets",
            "iso20022/fixtures/iso20022-external-code-sets.json",
            "b",
            "August 2026 (v3)",
        )
        transaction_code_artifact = self._artifact(
            "bank_transaction_code_combinations",
            "iso20022/fixtures/bank-transaction-code-combinations.xlsx",
            "c",
            "30 November 2025 (v1)",
        )
        controlled_manifest = {
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "artifacts": [
                schema_artifact,
                external_code_artifact,
                transaction_code_artifact,
            ],
        }
        schema_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        semantic_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def accept_schema(*args: object, **kwargs: object) -> None:
            self.assertTrue(any(value == hostile for value in (*args, *kwargs.values())))
            schema_calls.append((args, kwargs))

        def reject_unknown_channel_code(*args: object, **kwargs: object) -> None:
            rendered_contract = repr((args, kwargs))
            self.assertIn(external_code_artifact["local_package_path"], rendered_contract)
            self.assertIn(external_code_artifact["sha256"], rendered_contract)
            self.assertIn(external_code_artifact["source_version"], rendered_contract)
            self.assertTrue(any(value == hostile for value in (*args, *kwargs.values())))
            self.assertIn(b"<TechInptChanl>", hostile)
            self.assertIn(b"<Cd>ZZZZ</Cd>", hostile)
            self.assertEqual(len(schema_calls), 1)
            semantic_calls.append((args, kwargs))
            raise AccountingValidationError("unknown-technical-input-channel-sentinel")

        with (
            patch.object(
                bank_statement,
                "load_adapter_manifest",
                return_value=controlled_manifest,
            ),
            patch.object(
                bank_statement,
                "_validate_message_schema",
                side_effect=accept_schema,
                create=True,
            ),
            patch.object(
                bank_statement,
                "_validate_external_code_evidence",
                side_effect=reject_unknown_channel_code,
                create=True,
            ),
            patch.object(
                bank_statement,
                "_normalize_statement",
                side_effect=AssertionError(
                    "normalization ran before technical-input-channel code admission"
                ),
            ),
        ):
            with self.assertRaisesRegex(
                AccountingValidationError,
                "unknown-technical-input-channel-sentinel",
            ):
                bank_statement.parse_bank_statement_payload(
                    hostile,
                    CAMT053_MESSAGE_DEFINITION,
                )

        self.assertEqual(len(schema_calls), 1)
        self.assertEqual(len(semantic_calls), 1)

    def test_xml_formatting_does_not_change_technical_input_channel_semantics(self) -> None:
        """Element layout differences must not alter normalized channel evidence."""
        expected_hash = self._expected_channel_hash("Cd", "WEBI")
        first_entry = self.web_code_statement.entries[0]
        reformatted_entry = self.reformatted_statement.entries[0]

        self.assertNotEqual(
            self.web_code_statement.source_artifact_hash,
            self.reformatted_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(first_entry, "entry_technical_input_channel_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(reformatted_entry, "entry_technical_input_channel_evidence_hash", None),
            expected_hash,
        )
        self._assert_entry_hash_binding(first_entry, expected_hash)
        self._assert_entry_hash_binding(reformatted_entry, expected_hash)
        self.assertEqual(first_entry.source_entry_hash, reformatted_entry.source_entry_hash)
        self.assertEqual(
            self.web_code_statement.normalized_payload_hash,
            self.reformatted_statement.normalized_payload_hash,
        )

    def test_changed_technical_input_channel_requires_explicit_statement_correction(self) -> None:
        """A material entry channel change cannot silently replay one statement."""
        accepted = accept_bank_statement_evidence(
            self._command(self.web_code_payload, "web-code"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.fax_code_payload, "fax-code"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_read_preserves_exact_technical_input_channel_provenance(self) -> None:
        """Supported reads retain channel discriminator, value, and purpose digest."""
        expected_hash = self._expected_channel_hash("Cd", "WEBI")
        accepted = accept_bank_statement_evidence(
            self._command(self.web_code_payload, "lookup"),
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

        self.assertEqual(
            entry.get("entry_technical_input_channel_evidence_hash"),
            expected_hash,
        )
        self.assertEqual(
            entry.get("technical_input_channel_evidence"),
            {
                "channel_choice": "Cd",
                "channel_value": "WEBI",
            },
        )

    @staticmethod
    def _assert_entry_hash_binding(entry: object, expected_hash: str) -> None:
        """Bind canonical entry identity to parsed channel semantics, not raw XML."""
        projection = dict(bank_statement._entry_payload(entry))
        if projection.get("entry_technical_input_channel_evidence_hash") != expected_hash:
            raise AssertionError(
                "canonical entry projection must carry the exact "
                "entry_technical_input_channel_evidence_hash"
            )
        if "source_artifact_hash" in projection:
            raise AssertionError(
                "canonical entry projection must not use raw artifact identity"
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
    def _expected_channel_hash(channel_choice: str, channel_value: str) -> str:
        """Digest one TechnicalInputChannel1Choice semantic value."""
        preimage = json.dumps(
            {
                "evidence_type": _ENTRY_TECHNICAL_INPUT_CHANNEL_PURPOSE,
                "channel_choice": channel_choice,
                "channel_value": channel_value,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _artifact(
        role: str,
        path: str,
        digest_character: str,
        source_version: str,
    ) -> dict[str, object]:
        """Build one controlled manifest artifact for admission-order assertions."""
        return {
            "local_package_path": path,
            "artifact_role": role,
            "sha256": digest_character * 64,
            "byte_length": 123,
            "source_version": source_version,
        }

    @staticmethod
    def _with_technical_input_channel(
        fixture: str,
        marker: str,
        *,
        channel_choice: str,
        channel_value: str,
    ) -> bytes:
        """Insert one entry-level TechnicalInputChannel1Choice after BkTxCd."""
        if channel_choice not in {"Cd", "Prtry"}:
            raise AssertionError("technical input channel choice must be Cd or Prtry")
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
            "        <TechInptChanl>\n"
            f"          <{channel_choice}>{channel_value}</{channel_choice}>\n"
            "        </TechInptChanl>\n"
            "        <NtryDtls>\n",
            1,
        ).encode("utf-8")

    def _command(self, payload: bytes, suffix: str) -> dict[str, object]:
        """Return one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"entry-technical-input-channel-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
