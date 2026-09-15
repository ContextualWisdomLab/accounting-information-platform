"""PostgreSQL REDs for camt.053 entry additional-information evidence."""

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
    lookup_bank_statement_entries,
    parse_bank_statement_payload,
)
from accounting_information_platform import bank_statement
from tests import test_postgres_posting as posting

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_ENTRY_ADDITIONAL_INFORMATION_PURPOSE = "camt.053.001.14/Stmt/Ntry/AddtlNtryInf"
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementEntryAdditionalInformationEvidenceRedTests(unittest.TestCase):
    """Keep AddtlNtryInf as purpose-bound entry provenance, not accounting truth."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare statements differing only in the first entry's additional information."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.baseline_payload = self.fixture.encode("utf-8")
        self.first_information = "Bank entry note: treasury trace accepted"
        self.second_information = "Bank entry note: controller trace accepted"
        self.first_payload = self._with_additional_entry_information(self.first_information)
        self.second_payload = self._with_additional_entry_information(self.second_information)

        formatting_anchor = (
            f"        <AddtlNtryInf>{self.first_information}</AddtlNtryInf>\n"
            "      </Ntry>\n"
        ).encode("utf-8")
        self.assertEqual(self.first_payload.count(formatting_anchor), 1)
        self.reformatted_first_payload = self.first_payload.replace(
            formatting_anchor,
            (
                f"        <AddtlNtryInf>{self.first_information}</AddtlNtryInf>\n"
                "        \n"
                "      </Ntry>\n"
            ).encode("utf-8"),
            1,
        )

        self.baseline_statement = self._parse(self.baseline_payload)
        self.first_statement = self._parse(self.first_payload)
        self.second_statement = self._parse(self.second_payload)
        self.reformatted_first_statement = self._parse(self.reformatted_first_payload)

        self.bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        self._register_account(self.bank_account_reference, self.first_statement)
        self.store = MemoryArtifactStore()

    def test_additional_entry_information_is_material_entry_evidence(self) -> None:
        """Changing only AddtlNtryInf changes entry and statement evidence, not account identity."""
        first_hash = self._expected_information_hash(self.first_information)
        second_hash = self._expected_information_hash(self.second_information)

        self.assertEqual(
            self.first_statement.account_identifier_hash,
            self.second_statement.account_identifier_hash,
        )
        self.assertEqual(
            self.first_statement.entries[1].source_entry_hash,
            self.second_statement.entries[1].source_entry_hash,
        )
        self.assertEqual(
            getattr(
                self.first_statement.entries[0],
                "additional_entry_information_evidence_hash",
                None,
            ),
            first_hash,
        )
        self.assertEqual(
            getattr(
                self.second_statement.entries[0],
                "additional_entry_information_evidence_hash",
                None,
            ),
            second_hash,
        )
        self.assertNotEqual(first_hash, second_hash)
        self._assert_entry_projection_binding(self.first_statement.entries[0], first_hash)
        self._assert_entry_projection_binding(self.second_statement.entries[0], second_hash)
        self.assertNotEqual(
            self.first_statement.entries[0].source_entry_hash,
            self.second_statement.entries[0].source_entry_hash,
        )
        self.assertNotEqual(
            self.first_statement.normalized_payload_hash,
            self.second_statement.normalized_payload_hash,
        )

    def test_source_formatting_is_not_additional_entry_information_identity(self) -> None:
        """XML layout outside Max500Text may change raw bytes but not normalized evidence."""
        expected_hash = self._expected_information_hash(self.first_information)
        self.assertNotEqual(self.first_payload, self.reformatted_first_payload)
        self.assertNotEqual(
            self.first_statement.source_artifact_hash,
            self.reformatted_first_statement.source_artifact_hash,
        )
        self.assertEqual(
            getattr(
                self.first_statement.entries[0],
                "additional_entry_information_evidence_hash",
                None,
            ),
            expected_hash,
        )
        self.assertEqual(
            getattr(
                self.reformatted_first_statement.entries[0],
                "additional_entry_information_evidence_hash",
                None,
            ),
            expected_hash,
        )
        self.assertEqual(
            self.first_statement.entries[0].source_entry_hash,
            self.reformatted_first_statement.entries[0].source_entry_hash,
        )
        self.assertEqual(
            self.first_statement.normalized_payload_hash,
            self.reformatted_first_statement.normalized_payload_hash,
        )

    def test_changed_additional_entry_information_requires_explicit_correction(self) -> None:
        """Same statement identity cannot silently replay changed entry narrative evidence."""
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

    def test_buyer_entry_projection_exposes_digest_not_additional_information_text(self) -> None:
        """General entry reads may expose the purpose digest, not reversible narrative fields."""
        expected_hash = self._expected_information_hash(self.first_information)
        actual = self._ingest_and_lookup_first_entry(
            self.first_payload,
            self.bank_account_reference,
            "lookup",
            self.store,
        )

        baseline_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        self._register_account(baseline_account_reference, self.baseline_statement)
        baseline_payload = self.baseline_payload + b"\n "
        baseline = self._ingest_and_lookup_first_entry(
            baseline_payload,
            baseline_account_reference,
            "baseline",
            MemoryArtifactStore(),
        )

        self.assertEqual(actual.get("additional_entry_information_evidence_hash"), expected_hash)
        self.assertNotIn(self.first_information, json.dumps(actual, sort_keys=True))
        self._assert_entry_row_binding(actual)
        self._assert_entry_row_binding(baseline)
        self.assertRegex(str(actual["source_entry_hash"]), _HASH_PATTERN)
        self.assertRegex(str(baseline["source_entry_hash"]), _HASH_PATTERN)

        actual_without_private_evidence = dict(actual)
        baseline_without_private_evidence = dict(baseline)
        actual_without_private_evidence.pop("additional_entry_information_evidence_hash")
        baseline_without_private_evidence.pop("additional_entry_information_evidence_hash", None)
        for field in ("bank_statement_entry_id", "source_entry_hash"):
            actual_without_private_evidence.pop(field)
            baseline_without_private_evidence.pop(field)
        self.assertEqual(actual_without_private_evidence, baseline_without_private_evidence)

    def _assert_entry_projection_binding(self, entry: object, information_hash: str) -> None:
        """Bind AddtlNtryInf to the canonical entry payload through only its purpose digest."""
        projection = dict(bank_statement._entry_payload(entry))
        baseline = dict(bank_statement._entry_payload(self.baseline_statement.entries[0]))
        self.assertEqual(
            projection.get("additional_entry_information_evidence_hash"),
            information_hash,
        )
        projection_without_information = dict(projection)
        projection_without_information.pop("additional_entry_information_evidence_hash")
        baseline.pop("additional_entry_information_evidence_hash", None)
        self.assertEqual(projection_without_information, baseline)

        expected_entry_hash = "sha256:" + hashlib.sha256(
            json.dumps(projection, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        self.assertEqual(entry.source_entry_hash, expected_entry_hash)

    def _assert_entry_row_binding(self, entry: dict[str, object]) -> None:
        """Prove the buyer entry identifier is a retained PostgreSQL row identity before exclusion."""
        entry_id = uuid.UUID(str(entry["bank_statement_entry_id"]))
        with psycopg.connect(posting.DATABASE_URL) as connection:
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
            retained = connection.execute(
                """
                SELECT bank_statement_entry_id
                FROM accounting_integration.bank_statement_entry
                WHERE tenant_account_id = accounting_core.current_tenant_account_id()
                  AND bank_statement_entry_id = %s
                """,
                (entry_id,),
            ).fetchone()
        self.assertIsNotNone(retained)
        self.assertEqual(retained[0], entry_id)

    def _expected_information_hash(self, information: str) -> str:
        """Return the purpose-bound canonical digest for one AddtlNtryInf value."""
        preimage = {
            "evidence_type": _ENTRY_ADDITIONAL_INFORMATION_PURPOSE,
            "additional_entry_information": information,
        }
        canonical = json.dumps(preimage, separators=(",", ":"), sort_keys=True)
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _with_additional_entry_information(self, information: str) -> bytes:
        """Insert one schema-positioned AddtlNtryInf after the first entry's NtryDtls."""
        marker = (
            "            <RmtInf>\n"
            "              <Ustrd>Invoice 1001</Ustrd>\n"
            "            </RmtInf>\n"
            "          </TxDtls>\n"
            "        </NtryDtls>\n"
            "      </Ntry>\n"
        )
        self.assertEqual(self.fixture.count(marker), 1)
        return self.fixture.replace(
            marker,
            "            <RmtInf>\n"
            "              <Ustrd>Invoice 1001</Ustrd>\n"
            "            </RmtInf>\n"
            "          </TxDtls>\n"
            "        </NtryDtls>\n"
            f"        <AddtlNtryInf>{information}</AddtlNtryInf>\n"
            "      </Ntry>\n",
            1,
        ).encode("utf-8")

    def _register_account(self, reference: str, statement: object) -> None:
        """Register one isolated buyer account against the source account evidence."""
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
            "ingestion_idempotency_key": f"entry-additional-information-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }

    def _ingest_and_lookup_first_entry(
        self,
        payload: bytes,
        bank_account_reference: str,
        suffix: str,
        store: MemoryArtifactStore,
    ) -> dict[str, object]:
        """Accept one statement and return the first supported buyer entry projection."""
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
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        return document["bank_statement_entries"][0]


if __name__ == "__main__":
    unittest.main()
