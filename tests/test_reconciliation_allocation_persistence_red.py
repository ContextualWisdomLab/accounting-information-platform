"""RED contracts for durable many-to-many reconciliation allocation evidence."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0014_reconciliation_match_allocation.sql"


class ReconciliationAllocationPersistenceRedTests(unittest.TestCase):
    """Require normalized append-only allocation evidence before M2 is complete."""

    def test_migration_defines_match_and_both_allocation_sides(self) -> None:
        """Durable proposals use normalized rows rather than an opaque allocation blob."""
        self.assertTrue(
            MIGRATION.exists(),
            "Add migration 0014 before treating in-memory allocation plans as durable reconciliation evidence.",
        )
        migration = MIGRATION.read_text(encoding="utf-8")
        normalized = re.sub(r"\s+", " ", migration.lower())

        for object_name in (
            "reconciliation_match",
            "statement_match_allocation",
            "journal_match_allocation",
        ):
            self.assertIn(f"create table accounting_core.{object_name}", normalized)

        self.assertNotIn("jsonb", normalized)
        self.assertIn("allocated_amount numeric(38, 6) not null", normalized)
        self.assertIn("check (allocated_amount > 0)", normalized)
        self.assertIn("currency_code text not null", normalized)
        self.assertIn("matching_rule_code text not null", normalized)

    def test_allocation_rows_are_bound_to_one_tenant_and_run(self) -> None:
        """Statement and journal allocations cannot be relabelled into another run."""
        migration = MIGRATION.read_text(encoding="utf-8")
        normalized = re.sub(r"\s+", " ", migration.lower())

        for table_name in ("statement_match_allocation", "journal_match_allocation"):
            self.assertIn(
                f"foreign key (tenant_account_id, reconciliation_run_id, reconciliation_match_id) references accounting_core.reconciliation_match (tenant_account_id, reconciliation_run_id, reconciliation_match_id)",
                normalized,
                f"{table_name} must bind the allocation to the same tenant/run as its match",
            )

        self.assertIn(
            "foreign key (tenant_account_id, bank_statement_entry_id) references accounting_integration.bank_statement_entry (tenant_account_id, bank_statement_entry_id)",
            normalized,
        )
        self.assertIn(
            "foreign key (tenant_account_id, general_journal_id) references accounting_core.general_journal (tenant_account_id, general_journal_id)",
            normalized,
        )

    def test_allocation_evidence_is_forced_rls_and_append_only(self) -> None:
        """Recorded proposal evidence is tenant isolated and corrected by superseding evidence."""
        migration = MIGRATION.read_text(encoding="utf-8")
        normalized = re.sub(r"\s+", " ", migration.lower())

        for object_name in (
            "reconciliation_match",
            "statement_match_allocation",
            "journal_match_allocation",
        ):
            self.assertIn(
                f"alter table accounting_core.{object_name} force row level security",
                normalized,
            )
        self.assertIn("reject_reconciliation_allocation_mutation", normalized)
        self.assertIn("before update or delete on accounting_core.reconciliation_match", normalized)
        self.assertIn("before update or delete on accounting_core.statement_match_allocation", normalized)
        self.assertIn("before update or delete on accounting_core.journal_match_allocation", normalized)


if __name__ == "__main__":
    unittest.main()
