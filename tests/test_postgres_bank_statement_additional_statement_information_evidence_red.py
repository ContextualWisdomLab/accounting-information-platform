"""PostgreSQL REDs for camt.053 additional-statement-information evidence."""

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
_ADDITIONAL_STATEMENT_INFORMATION_PURPOSE = "camt.053.001.14/Stmt/AddtlStmtInf"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementAdditionalStatementInformationEvidenceRedTests(unittest.TestCase):
    """Retain statement-level bank narrative as purpose-bound evidence only."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare statements that isolate one AddtlStmtInf semantic."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        fixture = load_canonical_statement_fixture().decode("utf-8")
        marker = "      </Ntry>\n    </Stmt>\n"
        self.assertEqual(fixture.count(marker), 1)

        self.baseline_payload = fixture.encode("utf-8")
        self.first_information = "Bank statement note: sweep completed"
        self.second_information = "Bank statement note: sweep pending"
        self.first_payload = self._with_additional_statement_information(
            fixture,
            marker,
            self.first_information,
        )
        self.second_payload = self._with_additional_statement_information(
            fixture,
            marker,
            self.second_information,
        )

        formatting_anchor = (
            f"      <AddtlStmtInf>{self.first_information}</AddtlStmtInf>\n"
            "    </Stmt>\n"
        ).encode("utf-8")
        self.assertEqual(self.first_payload.count(formatting_anchor), 1)
        self.reformatted_first_payload = self.first_payload.replace(
            formatting_anchor,
            (
                f"      <AddtlStmtInf>{self.first_information}</AddtlStmtInf>\n"
                "      \n"
                "    </Stmt>\n"
            ).encode("utf-8"),
            1,
        )

        self.baseline_statement = parse_bank_statement_payload(
            self.baseline_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.first_statement = parse_bank_statement_payload(
            self.first_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.second_statement = parse_bank_statement_payload(
            self.second_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        self.reformatted_first_statement = parse_bank_statement_payload(
            self.reformatted_first_payload,
            CAMT053_MESSAGE_DEFINITION,
        )

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

    def test_additional_statement_information_is_material_statement_evidence(self) -> None:
        """Changing only AddtlStmtInf changes purpose-bound statement evidence."""
        first_hash = self._expected_information_hash(self.first_information)
        second_hash = self._expected_information_hash(self.second_information)

        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.second_statement.source_artifact_hash,
        )
        self.assertEqual(
            self.first_statement.account_identifier_hash,
            self.second_statement.account_identifier_hash,
        )
        self.assertRegex(first_hash, _HASH_PATTERN)
        self.assertRegex(second_hash, _HASH_PATTERN)
        self.assertEqual(
            getattr(
                self.first_statement,
                "statement_additional_information_evidence_hash",
                None,
            ),
            first_hash,
        )
        self.assertEqual(
            getattr(
                self.second_statement,
                "statement_additional_information_evidence_hash",
                None,
            ),
            second_hash,
        )
        self.assertNotEqual(first_hash, second_hash)
        self._assert_normalized_hash_binding(self.first_statement, first_hash)
        self._assert_normalized_hash_binding(self.second_statement, second_hash)
        self.assertNotEqual(
            self.first_statement.normalized_payload_hash,
            self.second_statement.normalized_payload_hash,
        )

    def test_source_formatting_cannot_change_semantically_equal_statement_information(self) -> None:
        """XML layout outside AddtlStmtInf text must not leak into semantic identity."""
        expected_hash = self._expected_information_hash(self.first_information)
        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.reformatted_first_statement.source_artifact_hash,
        )
        self.assertEqual(
            self.first_statement.account_identifier_hash,
            self.reformatted_first_statement.account_identifier_hash,
        )
        self.assertEqual(
            getattr(
                self.first_statement,
                "statement_additional_information_evidence_hash",
                None,
            ),
            expected_hash,
        )
        self.assertEqual(
            getattr(
                self.reformatted_first_statement,
                "statement_additional_information_evidence_hash",
                None,
            ),
            expected_hash,
        )
        self._assert_normalized_hash_binding(self.first_statement, expected_hash)
        self._assert_normalized_hash_binding(
            self.reformatted_first_statement,
            expected_hash,
        )
        self.assertEqual(
            self.first_statement.normalized_payload_hash,
            self.reformatted_first_statement.normalized_payload_hash,
        )

    def test_changed_additional_statement_information_requires_correction(self) -> None:
        """Same statement identity cannot silently replay changed bank narrative."""
        first = accept_bank_statement_evidence(
            self._command(self.first_payload, "first"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=self.store,
        )
        self.assertFalse(first["replayed"])

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(self.second_payload, "second"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=self.store,
            )

    def test_buyer_projection_differs_from_no_information_baseline_only_by_digest(self) -> None:
        """AddtlStmtInf may add its digest, never another reversible buyer projection."""
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

        self.assertEqual(
            actual.get("statement_additional_information_evidence_hash"),
            expected_hash,
        )
        self._assert_statement_record_identity_server_owned(actual)
        baseline_without_evidence = dict(baseline)
        baseline_without_evidence.pop("statement_additional_information_evidence_hash", None)
        actual_without_evidence = dict(actual)
        actual_without_evidence.pop("statement_additional_information_evidence_hash")

        variable_identity_fields = (
            "bank_account_reference",
            "bank_statement_record_id",
            "source_artifact_hash",
            "normalized_payload_hash",
            "artifact_store_reference",
        )
        for field in variable_identity_fields:
            actual_without_evidence.pop(field)
            baseline_without_evidence.pop(field)
        self.assertEqual(actual_without_evidence, baseline_without_evidence)

    def _assert_normalized_hash_binding(
        self,
        statement: object,
        information_hash: str,
    ) -> None:
        """Allow only the purpose digest to distinguish normalized statement projection."""
        projection = dict(bank_statement._normalized_payload(statement))
        baseline_projection = dict(bank_statement._normalized_payload(self.baseline_statement))
        self.assertEqual(
            projection.get("statement_additional_information_evidence_hash"),
            information_hash,
        )
        if "source_artifact_hash" in projection:
            raise AssertionError(
                "canonical normalized statement projection must not carry raw artifact identity"
            )

        actual_without_evidence = dict(projection)
        baseline_without_evidence = dict(baseline_projection)
        actual_without_evidence.pop("statement_additional_information_evidence_hash")
        baseline_without_evidence.pop("statement_additional_information_evidence_hash", None)
        self.assertEqual(actual_without_evidence, baseline_without_evidence)

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

    def _ingest_and_lookup(
        self,
        payload: bytes,
        statement: object,
        bank_account_reference: str,
        suffix: str,
        store: MemoryArtifactStore,
    ) -> dict[str, object]:
        """Ingest one statement and validate every buyer field allowed to vary by record."""
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
        self.assertRegex(source_hash, _HASH_PATTERN)
        self.assertEqual(document["source_artifact_hash"], source_hash)
        self.assertEqual(document["normalized_payload_hash"], statement.normalized_payload_hash)
        self.assertRegex(document["normalized_payload_hash"], _HASH_PATTERN)
        self.assertEqual(document["artifact_store_reference"], f"memory:{source_hash}")
        self.assertEqual(document["bank_account_reference"], bank_account_reference)
        uuid.UUID(str(document["bank_statement_record_id"]))
        return document

    def _assert_statement_record_identity_server_owned(
        self,
        document: dict[str, object],
    ) -> None:
        """Prove an excluded record UUID cannot be supplied through statement admission."""
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
                        f"additional-information-server-owned-{uuid.uuid4().hex}",
                        direct_source_hash,
                        self._fresh_hash(),
                        f"additional-information-server-owned-{uuid.uuid4().hex}",
                    ),
                ).fetchone()[0]
            except psycopg.IntegrityError:
                connection.rollback()
                return

            connection.rollback()
            self.assertNotEqual(directly_retained_id, narrative_derived_id)
        finally:
            connection.close()

    @staticmethod
    def _fresh_hash() -> str:
        """Return canonical SHA-256 evidence derived from fresh test entropy."""
        return "sha256:" + hashlib.sha256(uuid.uuid4().bytes).hexdigest()

    @staticmethod
    def _expected_information_hash(value: str) -> str:
        """Digest the exact Max500Text semantic retained for this focused RED."""
        preimage = json.dumps(
            {
                "evidence_type": _ADDITIONAL_STATEMENT_INFORMATION_PURPOSE,
                "text": value,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(preimage).hexdigest()}"

    @staticmethod
    def _with_additional_statement_information(
        fixture: str,
        marker: str,
        value: str,
    ) -> bytes:
        """Insert one lawful AddtlStmtInf after the final entry."""
        return fixture.replace(
            marker,
            "      </Ntry>\n"
            f"      <AddtlStmtInf>{value}</AddtlStmtInf>\n"
            "    </Stmt>\n",
            1,
        ).encode("utf-8")

    def _command(
        self,
        payload: bytes,
        suffix: str,
        *,
        bank_account_reference: str | None = None,
    ) -> dict[str, object]:
        """Return one supported ingest command with an independent replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": bank_account_reference or self.bank_account_reference,
            "ingestion_idempotency_key": (
                f"additional-statement-information-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
