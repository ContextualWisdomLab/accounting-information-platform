"""Migration-contract regressions for reconciliation authority outbox retention."""

from __future__ import annotations

import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION = _ROOT / "database/migrations/0023_reconciliation_authority_outbox_retention.sql"


class ReconciliationOutboxRetentionMigrationContractTests(unittest.TestCase):
    """Keep the damage preflight visible under forced row-level security."""

    def test_damage_preflight_has_temporary_all_tenant_visibility(self) -> None:
        """A migration owner cannot falsely pass the preflight through tenant-filtered RLS."""
        migration = _MIGRATION.read_text(encoding="utf-8")
        preflight = migration.index("DO $$")
        durable_guard = migration.index(
            "CREATE OR REPLACE FUNCTION accounting_core.assert_reconciliation_authority_outbox_identity"
        )
        policies = (
            (
                "reconciliation_authority_retention_upgrade_resolution_visibility",
                "accounting_core.reconciliation_exception_resolution_command",
            ),
            (
                "reconciliation_authority_retention_upgrade_transition_visibility",
                "accounting_core.reconciliation_run_transition_command",
            ),
            (
                "reconciliation_authority_retention_upgrade_outbox_visibility",
                "accounting_integration.outbox_event",
            ),
        )

        for policy_name, table_name in policies:
            create_policy = (
                f"CREATE POLICY {policy_name}\n"
                f"    ON {table_name}\n"
                "    FOR SELECT\n"
                "    TO current_user\n"
                "    USING (true);"
            )
            drop_policy = f"DROP POLICY {policy_name}\n    ON {table_name};"
            self.assertIn(create_policy, migration)
            self.assertIn(drop_policy, migration)
            self.assertLess(migration.index(create_policy), preflight)
            self.assertLess(preflight, migration.index(drop_policy))
            self.assertLess(migration.index(drop_policy), durable_guard)


if __name__ == "__main__":
    unittest.main()
