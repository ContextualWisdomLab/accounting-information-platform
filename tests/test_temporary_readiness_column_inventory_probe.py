"""Temporary repair probe for canonical PostgreSQL 18 column metadata."""

from __future__ import annotations

import json
import unittest

import psycopg

from tests import test_postgres_posting as posting


class TemporaryReadinessColumnInventoryProbe(unittest.TestCase):
    """Emit canonical migrated column metadata for readiness inventory repair."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the checked-in PostgreSQL foundation before catalog inspection."""
        posting.PostgresPostingTests.setUpClass()

    def test_emit_canonical_columns(self) -> None:
        """Fail intentionally with exact PostgreSQL 18 column metadata."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            rows = connection.execute(
                """
                SELECT namespace.nspname,
                       relation.relname,
                       attribute.attname,
                       pg_catalog.format_type(attribute.atttypid, attribute.atttypmod),
                       attribute.attnotnull,
                       COALESCE(
                           pg_catalog.pg_get_expr(default_value.adbin, default_value.adrelid),
                           ''
                       )
                FROM pg_catalog.pg_attribute AS attribute
                JOIN pg_catalog.pg_class AS relation
                  ON relation.oid = attribute.attrelid
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                LEFT JOIN pg_catalog.pg_attrdef AS default_value
                  ON default_value.adrelid = relation.oid
                 AND default_value.adnum = attribute.attnum
                WHERE namespace.nspname IN (
                    'accounting_core', 'accounting_integration', 'accounting_reporting'
                )
                  AND relation.relkind IN ('r', 'p')
                  AND attribute.attnum > 0
                  AND NOT attribute.attisdropped
                ORDER BY namespace.nspname, relation.relname, attribute.attnum
                """
            ).fetchall()
        self.fail("CANONICAL_COLUMNS=" + json.dumps(rows, separators=(",", ":")))


if __name__ == "__main__":
    unittest.main()
