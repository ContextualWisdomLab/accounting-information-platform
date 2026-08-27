"""One-shot normalizer for PR #29's authoritative migration-install chain.

This helper is temporary machinery. The normalization workflow removes this file
and itself before publishing the canonical repair commit.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    """Replace exactly one guarded text block or fail closed."""
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one repair anchor in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def repair_persistence() -> None:
    path = ROOT / "src/accounting_information_platform/persistence.py"
    validation_anchor = '''    allocation_control_migration_path = (
        migration_path.parent / "0014_reconciliation_candidate_allocation.sql"
    )
    if not allocation_control_migration_path.is_file():
        raise AccountingValidationError(
            "Reconciliation candidate/allocation migration is missing at "
            f"{allocation_control_migration_path}. Restore "
            "database/migrations/0014_reconciliation_candidate_allocation.sql, then retry."
        )
    psycopg = _import_psycopg()
'''
    validation_replacement = '''    allocation_control_migration_path = (
        migration_path.parent / "0014_reconciliation_candidate_allocation.sql"
    )
    if not allocation_control_migration_path.is_file():
        raise AccountingValidationError(
            "Reconciliation candidate/allocation migration is missing at "
            f"{allocation_control_migration_path}. Restore "
            "database/migrations/0014_reconciliation_candidate_allocation.sql, then retry."
        )
    conservation_migration_path = (
        migration_path.parent / "0015_reconciliation_multi_match_conservation.sql"
    )
    if not conservation_migration_path.is_file():
        raise AccountingValidationError(
            "Reconciliation multi-match conservation migration is missing at "
            f"{conservation_migration_path}. Restore "
            "database/migrations/0015_reconciliation_multi_match_conservation.sql, then retry."
        )
    psycopg = _import_psycopg()
'''
    replace_once(path, validation_anchor, validation_replacement)

    execution_anchor = '''            connection.execute(
                allocation_control_migration_path.read_text(encoding="utf-8")
            )
    except Exception as error:
'''
    execution_replacement = '''            connection.execute(
                allocation_control_migration_path.read_text(encoding="utf-8")
            )
            connection.execute(
                conservation_migration_path.read_text(encoding="utf-8")
            )
    except Exception as error:
'''
    replace_once(path, execution_anchor, execution_replacement)


def repair_public_loader() -> None:
    path = ROOT / "src/accounting_information_platform/migration_install.py"
    path.write_text(
        '''"""Public installation boundary for the accounting foundation migration chain."""\n\nfrom __future__ import annotations\n\nfrom pathlib import Path\n\nfrom .persistence import apply_foundation_migration as _apply_foundation_migration\n\n\ndef apply_foundation_migration(database_url: str, migration_path: Path) -> None:\n    """Apply the complete checked-in foundation chain through the canonical loader."""\n    _apply_foundation_migration(database_url, migration_path)\n\n\n__all__ = ["apply_foundation_migration"]\n''',
        encoding="utf-8",
    )


def repair_install_contracts() -> None:
    path = ROOT / "tests/test_foundation_install_manifest_contract.py"
    doc_anchor = '''    def test_install_fails_closed_when_reconciliation_control_migration_is_missing(self) -> None:
'''
    doc_tests = '''    def test_required_files_and_install_docs_include_multi_match_conservation(self) -> None:
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

'''
    replace_once(path, doc_anchor, doc_tests + doc_anchor)


def repair_repository_manifest() -> None:
    path = ROOT / "scripts/validate_repository.py"
    anchor = '''    "database/migrations/0014_reconciliation_candidate_allocation.sql",
'''
    replacement = anchor + '''    "database/migrations/0015_reconciliation_multi_match_conservation.sql",
'''
    replace_once(path, anchor, replacement)


def repair_operability() -> None:
    path = ROOT / "docs/OPERABILITY.md"
    replace_once(
        path,
        "Apply migrations in numeric order through `0014_reconciliation_candidate_allocation.sql` before starting the service.",
        "Apply migrations in numeric order through `0015_reconciliation_multi_match_conservation.sql` before starting the service.",
    )
    anchor = '''database/migrations/0014_reconciliation_candidate_allocation.sql
```
'''
    replacement = '''database/migrations/0014_reconciliation_candidate_allocation.sql
database/migrations/0015_reconciliation_multi_match_conservation.sql
```
'''
    replace_once(path, anchor, replacement)
    explanation_anchor = '''Migration `0007_runtime_tenant_binding.sql` replaces caller-selected tenant authority with owner-controlled runtime-login binding.'''
    explanation = '''Migration `0015_reconciliation_multi_match_conservation.sql` replaces the run-wide single-approved-match shortcut from `0014` with tenant/run-scoped match identity plus exact statement/journal allocation conservation. It permits multiple independently approved matches only when no authoritative source amount is over-consumed and grants no journal-posting authority.\n\n'''
    replace_once(path, explanation_anchor, explanation + explanation_anchor)


def repair_architecture() -> None:
    path = ROOT / "docs/ARCHITECTURE.md"
    anchor = '''14. `database/migrations/0014_reconciliation_candidate_allocation.sql` — durable reconciliation candidate, single-approved match, and exact statement/journal allocation rows with forced tenant RLS.
'''
    replacement = anchor + '''15. `database/migrations/0015_reconciliation_multi_match_conservation.sql` — replaces the run-wide single-approved-match shortcut with tenant/run-scoped match identity and exact statement/journal source-allocation conservation so independent matches may be approved without double-consuming source evidence.
'''
    replace_once(path, anchor, replacement)


def repair_changelog() -> None:
    path = ROOT / "CHANGELOG.md"
    old = '''- Added durable reconciliation candidate, match, and allocation rows (`reconciliation_candidate`, `reconciliation_match`, `statement_match_allocation`, `journal_match_allocation`) with forced tenant row-level security, exact positive amounts, and a partial unique index allowing at most one `approved` match per run so the same source amount cannot be double-consumed. Allocations remain reviewed evidence with no accounting-adjustment authority. ADR 0054 records the boundary.
'''
    new = '''- Added durable reconciliation candidate, match, and allocation rows (`reconciliation_candidate`, `reconciliation_match`, `statement_match_allocation`, `journal_match_allocation`) with forced tenant row-level security and exact positive amounts. Append-only migration `0015_reconciliation_multi_match_conservation.sql` replaces the earlier run-wide single-approved-match shortcut with tenant/run-scoped identity and exact statement/journal allocation conservation, allowing multiple independent approved matches without double-consuming authoritative source amounts. Allocations remain reviewed evidence with no posting, reversal, close, or accounting-policy authority. ADR 0054 records the boundary.
'''
    replace_once(path, old, new)


def main() -> None:
    repair_persistence()
    repair_public_loader()
    repair_install_contracts()
    repair_repository_manifest()
    repair_operability()
    repair_architecture()
    repair_changelog()


if __name__ == "__main__":
    main()
