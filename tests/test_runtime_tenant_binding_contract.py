"""Contracts for database-controlled runtime tenant identity."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from accounting_information_platform import AccountingValidationError
from accounting_information_platform.persistence import apply_foundation_migration


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "database/migrations"
RUNTIME_BINDING_MIGRATION = MIGRATIONS / "0007_runtime_tenant_binding.sql"


class RuntimeTenantBindingContractTests(unittest.TestCase):
    """Keep tenant RLS authority anchored to the authenticated database login."""

    def test_runtime_binding_uses_session_user_and_not_caller_guc(self) -> None:
        """The RLS identity function resolves an admin-owned active binding for session_user."""
        migration = RUNTIME_BINDING_MIGRATION.read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE accounting_core.runtime_tenant_binding", migration)
        self.assertIn("runtime_role_oid oid NOT NULL", migration)
        self.assertIn("runtime_role_name name NOT NULL", migration)
        self.assertIn("tenant_account_id uuid NOT NULL", migration)
        self.assertIn("valid_from timestamptz NOT NULL", migration)
        self.assertIn("valid_to timestamptz", migration)
        self.assertIn("SECURITY DEFINER", migration)
        self.assertIn("SET search_path = pg_catalog, accounting_core", migration)
        self.assertIn("runtime_tenant_binding.runtime_role_name = session_user", migration)
        self.assertIn("pg_roles.oid = runtime_tenant_binding.runtime_role_oid", migration)
        self.assertNotIn("app.tenant_account_id", migration)

    def test_foundation_loader_requires_runtime_binding_migration(self) -> None:
        """A partial migration set fails before connecting instead of silently omitting tenant binding."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_root = Path(temporary_directory)
            for migration_number in range(1, 7):
                source = next(MIGRATIONS.glob(f"{migration_number:04d}_*.sql"))
                (temporary_root / source.name).write_text(
                    source.read_text(encoding="utf-8"), encoding="utf-8"
                )
            with self.assertRaisesRegex(AccountingValidationError, "0007_runtime_tenant_binding"):
                apply_foundation_migration(
                    "postgresql://unused:unused@127.0.0.1:1/unused",
                    temporary_root / "0001_accounting_foundation.sql",
                )


if __name__ == "__main__":
    unittest.main()
