"""Repository contracts for exact readiness definition-fingerprint inventories."""

from __future__ import annotations

import unittest
import re
from pathlib import Path

from accounting_information_platform import persistence as persistence_module

_READINESS_INDEXES = tuple(
    f"{item[0]}.{item[1]}" for item in persistence_module._READINESS_INDEX_DEFINITIONS
)
_MIGRATION_ROOT = Path(__file__).resolve().parents[1] / "database" / "migrations"
_TABLE_DECLARATION_PATTERN = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?P<schema>[a-z0-9_]+)\.(?P<table>[a-z0-9_]+)",
    re.IGNORECASE,
)
_RLS_DECLARATION_PATTERN = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?"
    r"(?P<schema>[a-z0-9_]+)\.(?P<table>[a-z0-9_]+)\s+"
    r"(?:ENABLE|FORCE)\s+ROW\s+LEVEL\s+SECURITY",
    re.IGNORECASE,
)
_POLICY_DECLARATION_PATTERN = re.compile(
    r"CREATE\s+POLICY\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?P<policy>[a-z0-9_]+)\s+ON\s+"
    r"(?P<schema>[a-z0-9_]+)\.(?P<table>[a-z0-9_]+)",
    re.IGNORECASE,
)


def _migration_text() -> str:
    """Return the complete checked-in migration source in execution order."""
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(_MIGRATION_ROOT.glob("*.sql"))
    )


class ReadinessFingerprintInventoryContractTests(unittest.TestCase):
    """Keep every protected migration object paired with canonical semantics."""

    def test_migration_parsers_accept_declaration_modifiers(self) -> None:
        """Inventory parsing cannot skip valid conditional migration declarations."""
        self.assertEqual(
            ("accounting_core", "readiness_parser_table"),
            tuple(
                _TABLE_DECLARATION_PATTERN.search(
                    "CREATE TABLE IF NOT EXISTS "
                    "accounting_core.readiness_parser_table"
                ).group(name)
                for name in ("schema", "table")
            ),
        )
        self.assertEqual(
            ("accounting_core", "readiness_parser_table"),
            tuple(
                _RLS_DECLARATION_PATTERN.search(
                    "ALTER TABLE IF EXISTS accounting_core.readiness_parser_table "
                    "ENABLE ROW LEVEL SECURITY"
                ).group(name)
                for name in ("schema", "table")
            ),
        )
        policy = _POLICY_DECLARATION_PATTERN.search(
            "CREATE POLICY IF NOT EXISTS readiness_parser_isolation ON "
            "accounting_core.readiness_parser_table"
        )
        self.assertEqual(
            ("accounting_core", "readiness_parser_table", "readiness_parser_isolation"),
            tuple(policy.group(name) for name in ("schema", "table", "policy")),
        )

    def test_readiness_table_inventory_covers_all_migration_tables(self) -> None:
        """A new migration table cannot silently fall outside readiness."""
        migration_tables = {
            f"{match.group('schema')}.{match.group('table')}"
            for match in _TABLE_DECLARATION_PATTERN.finditer(_migration_text())
        }
        self.assertEqual(migration_tables, set(persistence_module._READINESS_TABLES))

    def test_readiness_rls_inventory_covers_all_migration_declarations(self) -> None:
        """RLS declarations remain paired with the readiness table contract."""
        migration_rls_tables = {
            (match.group("schema"), match.group("table"))
            for match in _RLS_DECLARATION_PATTERN.finditer(_migration_text())
        }
        self.assertEqual(
            migration_rls_tables,
            set(persistence_module._READINESS_RLS_TABLES),
        )

    def test_readiness_policy_inventory_covers_all_migration_policies(self) -> None:
        """A new or renamed tenant policy must update the runtime contract."""
        migration_policies = {
            (match.group("schema"), match.group("table"), match.group("policy"))
            for match in _POLICY_DECLARATION_PATTERN.finditer(_migration_text())
        }
        self.assertEqual(
            migration_policies,
            set(persistence_module._READINESS_RLS_POLICIES),
        )

    def test_constraint_fingerprints_cover_exact_readiness_inventory(self) -> None:
        """Every required constraint has one canonical type/definition fingerprint."""
        self.assertEqual(
                len(persistence_module._READINESS_CONSTRAINTS),
                len({item[:3] for item in persistence_module._READINESS_CONSTRAINTS}),
        )
        self.assertTrue(
            all(
                len(item) == 5
                and item[3] in {"c", "f", "p", "u"}
                and len(item[4]) == 32
                for item in persistence_module._READINESS_CONSTRAINTS
            )
        )

    def test_index_fingerprints_cover_exact_readiness_inventory(self) -> None:
        """Every required explicit index has one canonical definition fingerprint."""
        self.assertEqual(
            set(_READINESS_INDEXES),
            {
                f"{item[0]}.{item[1]}"
                for item in persistence_module._READINESS_INDEX_DEFINITIONS
            },
        )
        self.assertEqual(
            len(persistence_module._READINESS_INDEX_DEFINITIONS),
            len(
                {
                    (item[0], item[1])
                    for item in persistence_module._READINESS_INDEX_DEFINITIONS
                }
            ),
        )
        self.assertTrue(
            all(
                len(item) == 7
                and isinstance(item[4], bool)
                and len(item[6]) == 32
                for item in persistence_module._READINESS_INDEX_DEFINITIONS
            )
        )

    def test_control_function_fingerprints_cover_every_control_trigger(self) -> None:
        """Every control trigger resolves one canonical function fingerprint."""
        required_functions = {
            (item[3], item[4])
            for item in persistence_module._READINESS_CONTROL_TRIGGERS
        }
        self.assertEqual(
            required_functions,
            set(persistence_module._READINESS_CONTROL_FUNCTION_FINGERPRINTS),
        )
        self.assertTrue(
            all(
                len(fingerprint) == 32
                for fingerprint in (
                    persistence_module._READINESS_CONTROL_FUNCTION_FINGERPRINTS.values()
                )
            )
        )


if __name__ == "__main__":
    unittest.main()
