"""PostgreSQL REDs for camt.053 GroupHeader AdditionalInformation evidence."""

from __future__ import annotations

import hashlib
import json
import re
import unittest
import uuid

import psycopg

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
_GROUP_ADDITIONAL_INFORMATION_PURPOSE = "camt.053.001.14/GrpHdr/AddtlInf"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementGroupAdditionalInformationEvidenceRedTests(unittest.TestCase):
    """Retain GroupHeader narrative as purpose-bound transport evidence only."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare statements that isolate one GroupHeader AddtlInf semantic."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.baseline_payload = self.fixture.encode("utf-8")
        self.first_information = "Bank transport note: treasury delivery confirmed"
        self.second_information = "Bank transport note: controller delivery confirmed"
        self.first_payload = self._with_group_additional_information(self.first_information)
        self.second_payload = self._with_group_additional_information(self.second_information)

        formatting_anchor = (
            f"      <AddtlInf>{self.first_information}</AddtlInf>\n"
            "    </GrpHdr>\n"
        ).encode("utf-8")
        self.assertEqual(self.first_payload.count(formatting_anchor), 1)
        self.reformatted_first_payload = self.first_payload.replace(
            formatting_anchor,
            (
                f"      <AddtlInf>{self.first_information}</AddtlInf>\n"
                "      \n"
                "    </GrpHdr>\n"
            ).encode("utf-8"),
            1,
        )

        self.baseline_statement = self._parse(self.baseline_payload)
        self.first_statement = self._parse(self.first_payload)
        self.second_statement = self._parse(self.second_payload)
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

    def test_group_additional_information_is_material_transport_evidence(self) -> None:
        """Changing only GrpHdr/AddtlInf changes purpose-bound statement evidence."""
        first_hash = self._expected_information_hash(self.first_information)
        second_hash = self._expected_information_hash(self.second_information)

        self.assertEqual(
            self.first_statement.account_identifier_hash,
            self.second_statement.account_identifier_hash,
        )
        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.second_statement.source_artifact_hash,
        )
        self.assertRegex(first_hash, _HASH_PATTERN)
        self.assertRegex(second_hash, _HASH_PATTERN)
        self.assertEqual(
            getattr(
                self.first_statement,
                "group_additional_information_evidence_hash",
                None,
            ),
            first_hash,
        )
        self.assertEqual(
            getattr(
                self.second_statement,
                "group_additional_information_evidence_hash",
                None,
            ),
            second_hash,
        )
        self.assertNotEqual(first_hash, second_hash)
        self._assert_normalized_projection_binding(self.first_statement, first_hash)
        self._assert_normalized_projection_binding(self.second_statement, second_hash)
        self.assertNotEqual(
            self.first_statement.normalized_payload_hash,
            self.second_statement.normalized_payload_hash,
        )

    def test_source_formatting_is_not_group_additional_information_identity(self) -> None:
        """Whitespace outside AddtlInf text may alter raw bytes but not semantic identity."""
        expected_hash = self._expected_information_hash(self.first_information)
        self.assertNotEqual(self.first_payload, self.reformatted_first_payload)
        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.reformatted_first_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(
                self.first_statement,
                "group_additional_information_evidence_hash",
                None,
            ),
            expected_hash,
        )
        self.assertEqual(
            getattr(
                self.reformatted_first_statement,
                "group_additional_information_evidence_hash",
                None,
            ),
            expected_hash,
        )
        self.assertEqual(
            self.first_statement.normalized_payload_hash,
            self.reformatted_first_statement.normalized_payload_hash,
        )

    def test_changed_group_additional_information_requires_explicit_correction(self) -> None:
        """Same statement identity cannot silently replay changed GroupHeader narrative."""
        accepted = accept_bank_statement_evidence(
            self._command(self.first_payload, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(accepted["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.second_payload, "second"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_projection_exposes_digest_not_group_additional_information_text(self) -> None:
        """General lookup may expose the purpose digest, never the GroupHeader narrative."""
        expected_hash = self._expected_information_hash(self.first_information)
        actual = self._ingest_and_lookup(
            self.first_payload,
            self.first_statement,
            self.bank_account_reference,
            "lookup",
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

        self.assertEqual(actual.get("group_additional_information_evidence_hash"), expected_hash)
        serialized_actual = json.dumps(actual, sort_keys=True)
        self.assertNotIn(self.first_information, serialized_actual)
        self._assert_statement_record_identity_server_owned(actual)

        actual_without_evidence = dict(actual)
        baseline_without_evidence = dict(baseline)
        actual_without_evidence.pop("group_additional_information_evidence_hash")
        baseline_without_evidence.pop("group_additional_information_evidence_hash", None)
        for field in (
            "bank_account_reference",
            "bank_statement_record_id",
            "source_artifact_hash",
            "normalized_payload_hash",
            "artifact_store_reference",
        ):
            actual_without_evidence.pop(field, None)
            baseline_without_evidence.pop(field, None)
        self.assertEqual(actual_without_evidence, baseline_without_evidence)

    def _assert_normalized_projection_binding(
        self,
        statement: object,
        information_hash: str,
    ) -> None:
        """Allow GrpHdr/AddtlInf to affect normalized evidence only through its digest."""
        projection = dict(bank_statement._normalized_payload(statement))
        baseline = dict(bank_statement._normalized_payload(self.baseline_statement))
        self.assertEqual(
            projection.get("group_additional_information_evidence_hash"),
            information_hash,
        )
        if "source_artifact_hash" in projection:
            raise AssertionError(
                "canonical normalized statement projection must not carry raw artifact identity"
            )

        projection_without_information = dict(projection)
        projection_without_information.pop("group_additional_information_evidence_hash")
        baseline.pop("group_additional_information_evidence_hash", None)
        self.assertEqual(projection_without_information, baseline)

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

    def _assert_statement_record_identity_server_owned(
        self,
        document: dict[str, object],
    ) -> None:
        """Prove an excluded record UUID cannot be supplied from GroupHeader narrative."""
        retained_record_id = uuid.UUID(str(document["bank_statement_record_id"]))
        narrative_derived_id = uuid.UUID(
            bytes=hashlib.sha256(self.first_information.encode("utf-8")).digest()[:16]
        )
        self.assertNotEqual(retained_record_id, narrative_derived_id)

        connection = psycopg.connect(posting.DATABASE_URL)
        try:
            tenant_id = connection.execute(
                """
                SELECT tenant_account_id
                FROM accounting_core.tenant_account
                WHERE tenant_account_code = %s
                """,
                (self.case.policy.tenant_reference,),
            ).fetchone()[0]
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(tenant_id),),
            )
            bank_account_record_id = connection.execute(
                """
                SELECT bank_account_record_id
                FROM accounting_integration.bank_statement_record
                WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                  AND bank_statement_record_id = %s
                """,
                (retained_record_id,),
            ).fetchone()[0]

            direct_source_hash = self._fresh_hash()
            direct_statement_identity = (
                f"group-additional-server-owned-statement-{uuid.uuid4().hex}"
            )
            direct_idempotency_key = (
                f"group-additional-server-owned-ingest-{uuid.uuid4().hex}"
            )
            direct_normalized_hash = self._fresh_hash()
            self._assert_direct_statement_uniqueness_absent(
                connection,
                narrative_derived_id,
                bank_account_record_id,
                direct_source_hash,
                direct_statement_identity,
                direct_idempotency_key,
            )

            direct_artifact_id = connection.execute(
                """
                INSERT INTO accounting_integration.bank_statement_artifact (
                    tenant_account_id,
                    source_artifact_hash,
                    artifact_store_reference,
                    artifact_byte_length
                )
                VALUES (
                    accounting_core.current_tenant_account_id(),
                    %s,
                    %s,
                    1
                )
                RETURNING bank_statement_artifact_id
                """,
                (
                    direct_source_hash,
                    f"memory:{direct_source_hash}",
                ),
            ).fetchone()[0]

            try:
                directly_retained_id = connection.execute(
                    """
                    INSERT INTO accounting_integration.bank_statement_record (
                        bank_statement_record_id,
                        tenant_account_id,
                        bank_account_record_id,
                        bank_statement_artifact_id,
                        message_definition_identifier,
                        statement_identity_reference,
                        source_artifact_hash,
                        normalized_payload_hash,
                        ingestion_idempotency_key
                    )
                    VALUES (
                        %s,
                        accounting_core.current_tenant_account_id(),
                        %s,
                        %s,
                        'camt.053.001.14',
                        %s,
                        %s,
                        %s,
                        %s
                    )
                    RETURNING bank_statement_record_id
                    """,
                    (
                        narrative_derived_id,
                        bank_account_record_id,
                        direct_artifact_id,
                        direct_statement_identity,
                        direct_source_hash,
                        direct_normalized_hash,
                        direct_idempotency_key,
                    ),
                ).fetchone()[0]
            except psycopg.IntegrityError:
                connection.rollback()
                return

            connection.rollback()
            self.assertNotEqual(directly_retained_id, narrative_derived_id)
        finally:
            connection.close()

    def _assert_direct_statement_uniqueness_absent(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        proposed_record_id: uuid.UUID,
        bank_account_record_id: object,
        source_artifact_hash: str,
        statement_identity_reference: str,
        ingestion_idempotency_key: str,
    ) -> None:
        """Exclude existing uniqueness keys before probing server-owned record identity."""
        record_id_count = connection.execute(
            """
            SELECT count(*)
            FROM accounting_integration.bank_statement_record
            WHERE bank_statement_record_id = %s
            """,
            (proposed_record_id,),
        ).fetchone()[0]
        self.assertEqual(record_id_count, 0)

        artifact_source = connection.execute(
            """
            SELECT 1
            FROM accounting_integration.bank_statement_artifact
            WHERE tenant_account_id = accounting_core.current_tenant_account_id()
              AND source_artifact_hash = %s
            """,
            (source_artifact_hash,),
        ).fetchone()
        self.assertIsNone(artifact_source)

        statement_source = connection.execute(
            """
            SELECT 1
            FROM accounting_integration.bank_statement_record
            WHERE tenant_account_id = accounting_core.current_tenant_account_id()
              AND source_artifact_hash = %s
            """,
            (source_artifact_hash,),
        ).fetchone()
        self.assertIsNone(statement_source)

        statement_identity = connection.execute(
            """
            SELECT 1
            FROM accounting_integration.bank_statement_record
            WHERE tenant_account_id = accounting_core.current_tenant_account_id()
              AND bank_account_record_id = %s
              AND statement_identity_reference = %s
            """,
            (bank_account_record_id, statement_identity_reference),
        ).fetchone()
        self.assertIsNone(statement_identity)

        ingestion_identity = connection.execute(
            """
            SELECT 1
            FROM accounting_integration.bank_statement_record
            WHERE tenant_account_id = accounting_core.current_tenant_account_id()
              AND ingestion_idempotency_key = %s
            """,
            (ingestion_idempotency_key,),
        ).fetchone()
        self.assertIsNone(ingestion_identity)

    @staticmethod
    def _fresh_hash() -> str:
        """Return canonical SHA-256 evidence derived from fresh test entropy."""
        return "sha256:" + hashlib.sha256(uuid.uuid4().bytes).hexdigest()

    def _expected_information_hash(self, information: str) -> str:
        """Return the canonical digest for GroupHeader AdditionalInformation text."""
        preimage = {
            "evidence_type": _GROUP_ADDITIONAL_INFORMATION_PURPOSE,
            "additional_information": information,
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
            "ingestion_idempotency_key": f"group-additional-{suffix}-{uuid.uuid4().hex}",
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
        source_hash = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        self.assertEqual(document["source_artifact_hash"], source_hash)
        self.assertEqual(document["artifact_store_reference"], f"memory:{source_hash}")
        self.assertEqual(document["normalized_payload_hash"], statement.normalized_payload_hash)
        self.assertRegex(document["normalized_payload_hash"], _HASH_PATTERN)
        return document

    def _with_group_additional_information(self, information: str) -> bytes:
        """Insert schema-ordered GrpHdr/AddtlInf immediately before the GroupHeader closes."""
        marker = (
            "      <CreDtTm>2026-08-24T09:00:00+00:00</CreDtTm>\n"
            "    </GrpHdr>"
        )
        if self.fixture.count(marker) != 1:
            raise AssertionError("canonical fixture must contain exactly one GroupHeader terminator")
        replacement = (
            "      <CreDtTm>2026-08-24T09:00:00+00:00</CreDtTm>\n"
            f"      <AddtlInf>{information}</AddtlInf>\n"
            "    </GrpHdr>"
        )
        return self.fixture.replace(marker, replacement, 1).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
