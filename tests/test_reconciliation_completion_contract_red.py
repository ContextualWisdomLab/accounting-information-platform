"""RED contracts for the owner-controlled reconciliation completion command."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0020_reconciliation_completion_command.sql"
SOURCE = ROOT / "src/accounting_information_platform/reconciliation_completion.py"
HTTP_SOURCE = ROOT / "src/accounting_information_platform/http_api.py"
PUBLIC_SOURCE = ROOT / "src/accounting_information_platform/__init__.py"


class ReconciliationCompletionContractTests(unittest.TestCase):
    """Require a lawful evidence-backed path from evaluation to reconciliation."""

    def test_database_requires_immutable_completion_evidence_for_reconciled_transition(self) -> None:
        """The DB must reject a first transition to reconciled without its command evidence."""
        self.assertTrue(MIGRATION.exists(), "migration 0020 must define completion authority")
        migration = MIGRATION.read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE accounting_core.reconciliation_completion_command", migration)
        self.assertIn("reconciliation_completion_key", migration)
        self.assertIn("completion_command_hash", migration)
        self.assertIn("actor_reference", migration)
        self.assertIn("completion_purpose_code", migration)
        self.assertIn("statement_population_hash", migration)
        self.assertIn("book_population_hash", migration)
        self.assertIn("bridge_evidence_hash", migration)
        self.assertIn("ENABLE ROW LEVEL SECURITY", migration)
        self.assertIn("FORCE ROW LEVEL SECURITY", migration)
        self.assertIn("reconciliation_completion_command_immutability_guard", migration)
        self.assertIn("reconciliation_run_reconciled_guard", migration)
        self.assertIn("OLD.run_status_code NOT IN ('evaluating', 'review_required')", migration)
        self.assertIn("resolution_status_code = 'open'", migration)
        self.assertIn("match_status_code = 'proposed'", migration)
        self.assertIn("reconciliation_completion_command", migration)

    def test_application_command_uses_one_consistent_snapshot_and_database_owned_bridge(self) -> None:
        """Completion must bind exact populations and bridge facts from one DB snapshot."""
        self.assertTrue(SOURCE.exists(), "reconciliation completion source must exist")
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("def accept_reconciliation_completion(", source)
        self.assertIn("ledger._consistent_read_session()", source)
        self.assertIn("_database_owned_close_projection_evidence(", source)
        self.assertIn("FOR UPDATE OF run_record", source)
        self.assertIn("reconciliation_completion_key", source)
        self.assertIn("actor_reference", source)
        self.assertIn("completion_purpose_code", source)
        self.assertIn("reconciliation_completion_command", source)
        self.assertIn("accounting_integration.outbox_event", source)
        self.assertIn("'reconciliation_run.reconciled'", source)
        self.assertIn("IdempotencyConflictError", source)

    def test_http_and_public_api_expose_completion_without_general_status_mutation(self) -> None:
        """The buyer API exposes one completion command, not arbitrary run-status editing."""
        http_source = HTTP_SOURCE.read_text(encoding="utf-8")
        public_source = PUBLIC_SOURCE.read_text(encoding="utf-8")
        self.assertIn('RECONCILIATION_COMPLETION_PATH = "/reconciliation-completions"', http_source)
        self.assertIn("accept_reconciliation_completion", http_source)
        self.assertIn("_post_reconciliation_completion", http_source)
        self.assertNotIn('RECONCILIATION_STATUS_PATH =', http_source)
        self.assertIn("accept_reconciliation_completion", public_source)


if __name__ == "__main__":  # pragma: no cover - direct invocation convenience
    unittest.main()
