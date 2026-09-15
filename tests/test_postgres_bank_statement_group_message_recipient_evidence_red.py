"""PostgreSQL REDs for camt.053 GroupHeader MessageRecipient evidence."""

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
_MESSAGE_RECIPIENT_PURPOSE = "camt.053.001.14/GrpHdr/MsgRcpt"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementGroupMessageRecipientEvidenceRedTests(unittest.TestCase):
    """Retain MsgRcpt provenance without exposing recipient PII in general reads."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare statements that isolate GroupHeader recipient semantics."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.baseline_payload = self.fixture.encode("utf-8")
        self.first_recipient_name = "Contextual Wisdom Treasury"
        self.second_recipient_name = "Contextual Wisdom Controller"
        self.first_recipient_identifier = "CWL-REPORTING-RECIPIENT-001"
        self.second_recipient_identifier = "CWL-REPORTING-RECIPIENT-002"

        self.first_payload = self._with_message_recipient(
            self.first_recipient_name,
            self.first_recipient_identifier,
        )
        self.name_changed_payload = self._with_message_recipient(
            self.second_recipient_name,
            self.first_recipient_identifier,
        )
        self.identifier_changed_payload = self._with_message_recipient(
            self.first_recipient_name,
            self.second_recipient_identifier,
        )
        formatting_anchor = (
            f"        <Nm>{self.first_recipient_name}</Nm>\n"
            "        <Id>\n"
        ).encode("utf-8")
        self.assertEqual(self.first_payload.count(formatting_anchor), 1)
        self.reformatted_first_payload = self.first_payload.replace(
            formatting_anchor,
            (
                f"        <Nm>{self.first_recipient_name}</Nm>\n"
                "        \n"
                "        <Id>\n"
            ).encode("utf-8"),
            1,
        )

        self.baseline_statement = self._parse(self.baseline_payload)
        self.first_statement = self._parse(self.first_payload)
        self.name_changed_statement = self._parse(self.name_changed_payload)
        self.identifier_changed_statement = self._parse(self.identifier_changed_payload)
        self.reformatted_first_statement = self._parse(self.reformatted_first_payload)

        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": self.bank_account_reference,
                "account_currency_code": self.first_statement.account_currency_code,
                "account_identifier_hash": self.first_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        self.store = MemoryArtifactStore()

    def test_message_recipient_semantics_change_purpose_bound_statement_evidence(self) -> None:
        """Recipient name and OrgId/Othr identifier are independently material provenance."""
        first_hash = self._expected_recipient_hash(
            self.first_recipient_name,
            self.first_recipient_identifier,
        )
        for label, changed_statement, changed_name, changed_identifier in (
            (
                "name",
                self.name_changed_statement,
                self.second_recipient_name,
                self.first_recipient_identifier,
            ),
            (
                "organisation-identifier",
                self.identifier_changed_statement,
                self.first_recipient_name,
                self.second_recipient_identifier,
            ),
        ):
            with self.subTest(field=label):
                changed_hash = self._expected_recipient_hash(
                    changed_name,
                    changed_identifier,
                )
                self.assertEqual(
                    self.first_statement.account_identifier_hash,
                    changed_statement.account_identifier_hash,
                )
                self.assertNotEqual(
                    self.first_statement.source_artifact_hash,
                    changed_statement.source_artifact_hash,
                )
                self.assertRegex(first_hash, _HASH_PATTERN)
                self.assertRegex(changed_hash, _HASH_PATTERN)
                self.assertEqual(
                    getattr(
                        self.first_statement,
                        "group_message_recipient_evidence_hash",
                        None,
                    ),
                    first_hash,
                )
                self.assertEqual(
                    getattr(
                        changed_statement,
                        "group_message_recipient_evidence_hash",
                        None,
                    ),
                    changed_hash,
                )
                self.assertNotEqual(first_hash, changed_hash)
                self._assert_normalized_projection_binding(
                    self.first_statement,
                    first_hash,
                )
                self._assert_normalized_projection_binding(
                    changed_statement,
                    changed_hash,
                )
                self.assertNotEqual(
                    self.first_statement.normalized_payload_hash,
                    changed_statement.normalized_payload_hash,
                )

    def test_message_recipient_formatting_is_not_semantic_identity(self) -> None:
        """Whitespace outside MsgRcpt values may change raw bytes but not recipient semantics."""
        expected_hash = self._expected_recipient_hash(
            self.first_recipient_name,
            self.first_recipient_identifier,
        )
        self.assertNotEqual(self.first_payload, self.reformatted_first_payload)
        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.reformatted_first_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(self.first_statement, "group_message_recipient_evidence_hash", None),
            expected_hash,
        )
        self.assertEqual(
            getattr(
                self.reformatted_first_statement,
                "group_message_recipient_evidence_hash",
                None,
            ),
            expected_hash,
        )
        self.assertEqual(
            self.first_statement.normalized_payload_hash,
            self.reformatted_first_statement.normalized_payload_hash,
        )

    def test_changed_message_recipient_requires_explicit_correction(self) -> None:
        """A same-identity statement cannot silently replay changed recipient provenance."""
        accept_bank_statement_evidence(
            self._command(self.first_payload, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.identifier_changed_payload, "identifier-changed"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_projection_exposes_only_recipient_digest(self) -> None:
        """General statement lookup must not disclose MsgRcpt name or client identifier."""
        expected_hash = self._expected_recipient_hash(
            self.first_recipient_name,
            self.first_recipient_identifier,
        )
        actual = self._ingest_and_lookup(
            self.first_payload,
            self.first_statement,
            self.bank_account_reference,
            "recipient",
            self.store,
        )

        baseline_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": baseline_account_reference,
                "account_currency_code": self.baseline_statement.account_currency_code,
                "account_identifier_hash": self.baseline_statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        baseline = self._ingest_and_lookup(
            self.baseline_payload,
            self.baseline_statement,
            baseline_account_reference,
            "baseline",
            MemoryArtifactStore(),
        )

        self.assertEqual(actual.get("group_message_recipient_evidence_hash"), expected_hash)
        serialized_actual = json.dumps(actual, sort_keys=True)
        self.assertNotIn(self.first_recipient_name, serialized_actual)
        self.assertNotIn(self.first_recipient_identifier, serialized_actual)

        actual_without_recipient = dict(actual)
        actual_without_recipient.pop("group_message_recipient_evidence_hash")
        baseline_without_recipient = dict(baseline)
        baseline_without_recipient.pop("group_message_recipient_evidence_hash", None)
        for field in (
            "bank_account_reference",
            "bank_statement_record_id",
            "source_artifact_hash",
            "normalized_payload_hash",
            "artifact_store_reference",
        ):
            actual_without_recipient.pop(field, None)
            baseline_without_recipient.pop(field, None)
        self.assertEqual(actual_without_recipient, baseline_without_recipient)

    def _assert_normalized_projection_binding(
        self,
        statement: object,
        recipient_hash: str,
    ) -> None:
        """Allow MsgRcpt to change normalized projection only through its purpose digest."""
        projection = dict(bank_statement._normalized_payload(statement))
        baseline = dict(bank_statement._normalized_payload(self.baseline_statement))
        self.assertEqual(
            projection.get("group_message_recipient_evidence_hash"),
            recipient_hash,
        )
        if "source_artifact_hash" in projection:
            raise AssertionError(
                "canonical normalized statement projection must not carry raw artifact identity"
            )

        projection_without_recipient = dict(projection)
        projection_without_recipient.pop("group_message_recipient_evidence_hash")
        baseline.pop("group_message_recipient_evidence_hash", None)
        self.assertEqual(projection_without_recipient, baseline)

        serialized_projection = json.dumps(
            projection,
            separators=(",", ":"),
            sort_keys=True,
        )
        expected_statement_hash = (
            "sha256:"
            + hashlib.sha256(serialized_projection.encode("utf-8")).hexdigest()
        )
        self.assertEqual(statement.normalized_payload_hash, expected_statement_hash)

    def _expected_recipient_hash(
        self,
        recipient_name: str,
        recipient_identifier: str,
    ) -> str:
        """Return the semantic digest for the admitted OrgId/Othr recipient branch."""
        preimage = {
            "evidence_type": _MESSAGE_RECIPIENT_PURPOSE,
            "name": recipient_name,
            "identification_choice": "OrgId/Othr",
            "organisation_identifier": recipient_identifier,
        }
        canonical = json.dumps(preimage, separators=(",", ":"), sort_keys=True)
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _parse(self, payload: bytes):
        """Parse one source payload through the supported camt.053 adapter."""
        return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    def _command(
        self,
        payload: bytes,
        suffix: str,
        *,
        bank_account_reference: str | None = None,
    ) -> dict[str, object]:
        """Build one supported ingest command with a fresh replay identity."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": bank_account_reference or self.bank_account_reference,
            "ingestion_idempotency_key": f"group-recipient-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

    def _ingest_and_lookup(
        self,
        payload: bytes,
        statement: object,
        bank_account_reference: str,
        suffix: str,
        store: MemoryArtifactStore,
    ) -> dict[str, object]:
        """Ingest one statement and return its tenant-scoped buyer projection."""
        accepted = accept_bank_statement_evidence(
            self._command(
                payload,
                suffix,
                bank_account_reference=bank_account_reference,
            ),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=store,
        )
        document = lookup_bank_statement(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        self.assertEqual(document["normalized_payload_hash"], statement.normalized_payload_hash)
        return document

    def _with_message_recipient(
        self,
        recipient_name: str,
        recipient_identifier: str,
    ) -> bytes:
        """Insert one schema-shaped PartyIdentification recipient after group creation time."""
        marker = (
            "      <CreDtTm>2026-08-24T09:00:00+00:00</CreDtTm>\n"
            "    </GrpHdr>"
        )
        if self.fixture.count(marker) != 1:
            raise AssertionError("canonical fixture must contain exactly one GroupHeader terminator")
        replacement = (
            "      <CreDtTm>2026-08-24T09:00:00+00:00</CreDtTm>\n"
            "      <MsgRcpt>\n"
            f"        <Nm>{recipient_name}</Nm>\n"
            "        <Id>\n"
            "          <OrgId>\n"
            "            <Othr>\n"
            f"              <Id>{recipient_identifier}</Id>\n"
            "            </Othr>\n"
            "          </OrgId>\n"
            "        </Id>\n"
            "      </MsgRcpt>\n"
            "    </GrpHdr>"
        )
        return self.fixture.replace(marker, replacement, 1).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
