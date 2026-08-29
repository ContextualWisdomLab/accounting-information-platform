"""RED PostgreSQL contract for reconciliation command candidate/match lineage."""

from __future__ import annotations

import unittest
import uuid

import psycopg

from tests.test_reconciliation_match_api import ReconciliationMatchApiTests
from tests import test_postgres_posting as posting


class ReconciliationMatchCommandChainTests(unittest.TestCase):
    """Prove immutable command evidence names one real candidate-to-match chain."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        self.helper = ReconciliationMatchApiTests(
            "test_proposed_match_is_persisted_and_replayed"
        )
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.addCleanup(self.helper.tearDown)

    def test_command_rejects_candidate_from_a_different_match(self) -> None:
        """Cross-pair candidate/match provenance fails at the database boundary."""
        run_id, _ = self.helper._open_run()
        tenant_id = self.helper.case.tenant_id

        with psycopg.connect(posting.DATABASE_URL) as connection:
            candidate_ids: list[uuid.UUID] = []
            match_ids: list[uuid.UUID] = []
            for suffix in ("a", "b"):
                candidate_id = connection.execute(
                    """
                    INSERT INTO accounting_core.reconciliation_candidate (
                        tenant_account_id,
                        reconciliation_run_id,
                        statement_entry_reference,
                        journal_reference,
                        statement_amount,
                        journal_amount,
                        rule_code
                    )
                    VALUES (%s, %s, %s, %s, '1.000000', '1.000000', 'chain-red')
                    RETURNING reconciliation_candidate_id
                    """,
                    (
                        tenant_id,
                        run_id,
                        f"urn:cwl:statement:chain:{suffix}:{uuid.uuid4()}",
                        f"urn:cwl:journal:chain:{suffix}:{uuid.uuid4()}",
                    ),
                ).fetchone()[0]
                match_id = connection.execute(
                    """
                    INSERT INTO accounting_core.reconciliation_match (
                        tenant_account_id,
                        reconciliation_run_id,
                        reconciliation_candidate_id,
                        match_status_code
                    )
                    VALUES (%s, %s, %s, 'proposed')
                    RETURNING reconciliation_match_id
                    """,
                    (tenant_id, run_id, candidate_id),
                ).fetchone()[0]
                candidate_ids.append(candidate_id)
                match_ids.append(match_id)

            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                connection.execute(
                    """
                    INSERT INTO accounting_core.reconciliation_match_command (
                        tenant_account_id,
                        reconciliation_run_id,
                        reconciliation_candidate_id,
                        reconciliation_match_id,
                        candidate_idempotency_key,
                        candidate_command_hash,
                        source_payload_hash,
                        source_payload_reference
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        tenant_id,
                        run_id,
                        candidate_ids[0],
                        match_ids[1],
                        f"chain-cross-pair-{uuid.uuid4().hex}",
                        "sha256:" + "a" * 64,
                        "sha256:" + "b" * 64,
                        "urn:cwl:object:cross-pair-provenance",
                    ),
                )
