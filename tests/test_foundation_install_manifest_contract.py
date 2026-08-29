"""Contracts for the complete foundation install and reviewable evidence manifest."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from accounting_information_platform import AccountingValidationError, apply_foundation_migration
from scripts.validate_repository import REQUIRED_FILES


ROOT = Path(__file__).resolve().parents[1]


class FoundationInstallManifestContractTests(unittest.TestCase):
    """Keep required-file and migration-install documentation aligned with the schema."""

    def test_required_files_include_runtime_tenant_binding_and_its_adrs(self) -> None:
        """CI must fail when database-owned runtime tenant binding evidence is absent."""
        required = set(REQUIRED_FILES)
        self.assertTrue(
            {
                "database/migrations/0007_runtime_tenant_binding.sql",
                "docs/adr/0048-reproducible-package-evidence.md",
                "docs/adr/0049-runtime-tenant-database-binding.md",
            }
            <= required
        )

    def test_install_docs_include_runtime_tenant_binding_after_concurrency_migration(self) -> None:
        """Operators must install runtime tenant binding before granting runtime access."""
        migration_six = "database/migrations/0006_concurrency_hot_partition.sql"
        migration_seven = "database/migrations/0007_runtime_tenant_binding.sql"
        for relative_path in ("docs/OPERABILITY.md", "docs/ARCHITECTURE.md"):
            with self.subTest(relative_path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(migration_six, text)
                self.assertIn(migration_seven, text)
                self.assertLess(text.index(migration_six), text.index(migration_seven))

    def test_required_files_and_install_docs_include_soft_close_command_evidence(self) -> None:
        """Soft-close evidence migration follows book-period control in operator docs."""
        migration_nine = "database/migrations/0009_accounting_book_period_control.sql"
        migration_ten = "database/migrations/0010_soft_close_command_evidence.sql"
        self.assertIn(migration_ten, set(REQUIRED_FILES))
        for relative_path in ("docs/OPERABILITY.md", "docs/ARCHITECTURE.md"):
            with self.subTest(relative_path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(migration_nine, text)
                self.assertIn(migration_ten, text)
                self.assertLess(text.index(migration_nine), text.index(migration_ten))

    def test_required_files_and_install_docs_include_bank_statement_evidence(self) -> None:
        """Bank-statement evidence follows soft-close command evidence in operator docs."""
        migration_ten = "database/migrations/0010_soft_close_command_evidence.sql"
        migration_eleven = "database/migrations/0011_bank_statement_evidence.sql"
        self.assertIn(migration_eleven, set(REQUIRED_FILES))
        for relative_path in ("docs/OPERABILITY.md", "docs/ARCHITECTURE.md"):
            with self.subTest(relative_path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(migration_ten, text)
                self.assertIn(migration_eleven, text)
                self.assertLess(text.index(migration_ten), text.index(migration_eleven))

    def test_required_files_and_install_docs_include_assignment_identity(self) -> None:
        """Assignment command identity follows bank-statement evidence in operator docs."""
        migration_eleven = "database/migrations/0011_bank_statement_evidence.sql"
        migration_twelve = "database/migrations/0012_bank_assignment_command_identity.sql"
        self.assertIn(migration_twelve, set(REQUIRED_FILES))
        for relative_path in ("docs/OPERABILITY.md", "docs/ARCHITECTURE.md"):
            with self.subTest(relative_path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(migration_eleven, text)
                self.assertIn(migration_twelve, text)
                self.assertLess(text.index(migration_eleven), text.index(migration_twelve))

    def test_required_files_and_install_docs_include_reconciliation_control(self) -> None:
        """Reconciliation control follows assignment identity in operator/install contracts."""
        migration_twelve = "database/migrations/0012_bank_assignment_command_identity.sql"
        migration_thirteen = "database/migrations/0013_reconciliation_run_exception_evidence.sql"
        self.assertIn(migration_thirteen, set(REQUIRED_FILES))
        for relative_path in ("docs/OPERABILITY.md", "docs/ARCHITECTURE.md"):
            with self.subTest(relative_path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(migration_twelve, text)
                self.assertIn(migration_thirteen, text)
                self.assertLess(text.index(migration_twelve), text.index(migration_thirteen))

    def test_required_files_and_install_docs_include_multi_match_conservation(self) -> None:
        """Migration 0015 must extend the canonical install chain after candidate allocation."""
        migration_fourteen = "database/migrations/0014_reconciliation_candidate_allocation.sql"
        migration_fifteen = "database/migrations/0015_reconciliation_multi_match_conservation.sql"
        self.assertIn(migration_fifteen, set(REQUIRED_FILES))
        for relative_path in ("docs/OPERABILITY.md", "docs/ARCHITECTURE.md"):
            with self.subTest(relative_path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(migration_fourteen, text)
                self.assertIn(migration_fifteen, text)
                self.assertLess(text.index(migration_fourteen), text.index(migration_fifteen))

    def test_required_files_and_install_docs_include_approval_snapshot_binding(self) -> None:
        """Approval snapshot evidence follows multi-match conservation in the install chain."""
        migration_fifteen = "database/migrations/0015_reconciliation_multi_match_conservation.sql"
        migration_sixteen = "database/migrations/0016_reconciliation_approval_evidence.sql"
        self.assertIn(migration_sixteen, set(REQUIRED_FILES))
        for relative_path in ("docs/OPERABILITY.md", "docs/ARCHITECTURE.md"):
            with self.subTest(relative_path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(migration_fifteen, text)
                self.assertIn(migration_sixteen, text)
                self.assertLess(text.index(migration_fifteen), text.index(migration_sixteen))

    def test_required_files_and_install_docs_include_approval_lock_order(self) -> None:
        """Approval lock-order repair follows the approval snapshot migration."""
        migration_sixteen = "database/migrations/0016_reconciliation_approval_evidence.sql"
        migration_seventeen = "database/migrations/0017_reconciliation_approval_lock_order.sql"
        self.assertIn(migration_seventeen, set(REQUIRED_FILES))
        for relative_path in ("docs/OPERABILITY.md", "docs/ARCHITECTURE.md"):
            with self.subTest(relative_path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(migration_sixteen, text)
                self.assertIn(migration_seventeen, text)
                self.assertLess(text.index(migration_sixteen), text.index(migration_seventeen))

    def test_required_files_and_install_docs_include_balance_evidence(self) -> None:
        """Numeric bank-statement balance evidence follows the reconciliation controls."""
        migration_seventeen = "database/migrations/0017_reconciliation_approval_lock_order.sql"
        migration_eighteen = "database/migrations/0018_bank_statement_balance_evidence.sql"
        self.assertIn(migration_eighteen, set(REQUIRED_FILES))
        for relative_path in ("docs/OPERABILITY.md", "docs/ARCHITECTURE.md"):
            with self.subTest(relative_path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(migration_seventeen, text)
                self.assertIn(migration_eighteen, text)
                self.assertLess(text.index(migration_seventeen), text.index(migration_eighteen))

    def test_required_files_and_install_docs_include_run_command_evidence(self) -> None:
        """Run opening evidence follows exact persisted statement balances."""
        migration_eighteen = "database/migrations/0018_bank_statement_balance_evidence.sql"
        migration_nineteen = "database/migrations/0019_reconciliation_run_command_evidence.sql"
        self.assertIn(migration_nineteen, set(REQUIRED_FILES))
        for relative_path in ("docs/OPERABILITY.md", "docs/ARCHITECTURE.md"):
            with self.subTest(relative_path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(migration_eighteen, text)
                self.assertIn(migration_nineteen, text)
                self.assertLess(text.index(migration_eighteen), text.index(migration_nineteen))

    def test_required_files_and_install_docs_include_match_command_evidence(self) -> None:
        """Proposed-match command evidence follows the immutable run command chain."""
        migration_nineteen = "database/migrations/0019_reconciliation_run_command_evidence.sql"
        migration_twenty = "database/migrations/0020_reconciliation_match_command_evidence.sql"
        self.assertIn(migration_twenty, set(REQUIRED_FILES))
        for relative_path in ("docs/OPERABILITY.md", "docs/ARCHITECTURE.md"):
            with self.subTest(relative_path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(migration_nineteen, text)
                self.assertIn(migration_twenty, text)
                self.assertLess(text.index(migration_nineteen), text.index(migration_twenty))

    def test_required_files_and_install_docs_include_run_provenance_repair(self) -> None:
        """Existing installations receive the command provenance trigger repair."""
        migration_twenty = "database/migrations/0020_reconciliation_match_command_evidence.sql"
        migration_twenty_one = (
            "database/migrations/0021_reconciliation_run_command_provenance_repair.sql"
        )
        self.assertIn(migration_twenty_one, set(REQUIRED_FILES))
        for relative_path in ("docs/OPERABILITY.md", "docs/ARCHITECTURE.md"):
            with self.subTest(relative_path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(migration_twenty, text)
                self.assertIn(migration_twenty_one, text)
                self.assertLess(text.index(migration_twenty), text.index(migration_twenty_one))

    def test_required_files_and_install_docs_include_amount_precision_repair(self) -> None:
        """Existing installations receive the reconciliation precision repair."""
        migration_twenty_one = (
            "database/migrations/0021_reconciliation_run_command_provenance_repair.sql"
        )
        migration_twenty_two = "database/migrations/0022_reconciliation_amount_precision.sql"
        self.assertIn(migration_twenty_two, set(REQUIRED_FILES))
        for relative_path in ("docs/OPERABILITY.md", "docs/ARCHITECTURE.md"):
            with self.subTest(relative_path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(migration_twenty_one, text)
                self.assertIn(migration_twenty_two, text)
                self.assertLess(text.index(migration_twenty_one), text.index(migration_twenty_two))

    def test_run_provenance_repair_rejects_existing_mismatches(self) -> None:
        """The upgrade must fail closed on already stored cross-bank commands."""
        migration = (
            ROOT
            / "database/migrations/0021_reconciliation_run_command_provenance_repair.sql"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "reconciliation_run_command_provenance_upgrade_visibility",
            migration,
        )
        for policy in (
            "reconciliation_run_command_provenance_run_upgrade_visibility",
            "reconciliation_run_command_provenance_assignment_upgrade_visibility",
            "reconciliation_run_command_provenance_statement_upgrade_visibility",
        ):
            with self.subTest(policy=policy):
                self.assertIn(policy, migration)
        self.assertIn(
            "statement.bank_account_record_id IS DISTINCT FROM assignment.bank_account_record_id",
            migration,
        )
        self.assertIn(
            "existing reconciliation run command provenance is invalid",
            migration,
        )

    def test_install_fails_closed_when_approval_snapshot_migration_is_missing(self) -> None:
        """The canonical loader may not silently stop before database-owned approval evidence."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == "0016_reconciliation_approval_evidence.sql":
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaises(AccountingValidationError):
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )

    def test_install_fails_closed_when_approval_lock_order_migration_is_missing(self) -> None:
        """The canonical loader must apply the forward lock-order repair."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == "0017_reconciliation_approval_lock_order.sql":
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaises(AccountingValidationError):
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )

    def test_install_fails_closed_when_balance_evidence_migration_is_missing(self) -> None:
        """The canonical loader may not install reconciliation without numeric balance facts."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == "0018_bank_statement_balance_evidence.sql":
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaises(AccountingValidationError):
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )

    def test_install_fails_closed_when_run_command_migration_is_missing(self) -> None:
        """The canonical loader may not open runs without command provenance."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == "0019_reconciliation_run_command_evidence.sql":
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaises(AccountingValidationError):
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )

    def test_install_fails_closed_when_match_command_migration_is_missing(self) -> None:
        """The canonical loader may not persist proposed matches without command evidence."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == "0020_reconciliation_match_command_evidence.sql":
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaises(AccountingValidationError):
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )

    def test_install_fails_closed_when_run_provenance_repair_is_missing(self) -> None:
        """The canonical loader must apply the existing-installation trigger repair."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == "0021_reconciliation_run_command_provenance_repair.sql":
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaisesRegex(AccountingValidationError, "provenance repair"):
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )

    def test_install_fails_closed_when_amount_precision_migration_is_missing(self) -> None:
        """The canonical loader must apply the reconciliation precision repair."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == "0022_reconciliation_amount_precision.sql":
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaisesRegex(AccountingValidationError, "amount-precision"):
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )

    def test_canonical_persistence_loader_fails_closed_when_conservation_is_missing(self) -> None:
        """Real PostgreSQL fixtures may not silently stop the authoritative chain at 0014."""
        from accounting_information_platform.persistence import (
            apply_foundation_migration as apply_persistence_foundation_migration,
        )

        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == "0015_reconciliation_multi_match_conservation.sql":
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaises(AccountingValidationError):
                apply_persistence_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )

    def test_public_loader_fails_closed_when_conservation_is_missing(self) -> None:
        """The exported install boundary must delegate to the same complete canonical chain."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == "0015_reconciliation_multi_match_conservation.sql":
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaises(AccountingValidationError):
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )

    def test_install_fails_closed_when_reconciliation_control_migration_is_missing(self) -> None:
        """The public foundation loader may not silently omit migration 0013."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == "0013_reconciliation_run_exception_evidence.sql":
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaises(AccountingValidationError):
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )

    def test_install_fails_closed_when_candidate_allocation_migration_is_missing(self) -> None:
        """The public foundation loader may not silently omit migration 0014."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == "0014_reconciliation_candidate_allocation.sql":
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaises(AccountingValidationError):
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )

    def test_install_fails_closed_when_reconciliation_control_apply_fails(self) -> None:
        """Applying migration 0013 inside the authoritative chain keeps the PostgreSQL cause."""
        failing_psycopg = type("FailingPsycopg", (), {
            "ClientCursor": object,
            "connect": staticmethod(
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    RuntimeError("postgres connection refused")
                )
            ),
        })
        with patch(
            "accounting_information_platform.persistence._import_psycopg",
            return_value=failing_psycopg,
        ):
            with self.assertRaisesRegex(
                AccountingValidationError, "Foundation migration failed"
            ) as raised:
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )
        self.assertIsInstance(raised.exception.__cause__, RuntimeError)
        self.assertEqual(str(raised.exception.__cause__), "postgres connection refused")

    def test_install_fails_closed_when_assignment_identity_migration_is_missing(self) -> None:
        """The foundation loader may not silently omit migration 0012."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == "0012_bank_assignment_command_identity.sql":
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaises(AccountingValidationError):
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )

    def test_install_fails_closed_when_bank_statement_migration_is_missing(self) -> None:
        """The foundation loader may not silently omit migration 0011."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == "0011_bank_statement_evidence.sql":
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaises(AccountingValidationError):
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )

    def test_install_fails_closed_when_soft_close_evidence_migration_is_missing(self) -> None:
        """The foundation loader may not silently omit migration 0010."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == "0010_soft_close_command_evidence.sql":
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaises(AccountingValidationError):
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )


if __name__ == "__main__":
    unittest.main()
