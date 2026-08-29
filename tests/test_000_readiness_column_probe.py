"""Temporary early PostgreSQL 18 column-catalog probe for readiness repair."""

from __future__ import annotations

import hashlib
import json
import unittest
from collections import defaultdict

import psycopg

from tests import test_postgres_posting as posting


class TemporaryEarlyReadinessColumnProbe(unittest.TestCase):
    """Emit canonical migrated column metadata before the full suite runs."""

    @classmethod
    def setUpClass(cls) -> None:
        """Install the checked-in PostgreSQL foundation before catalog inspection."""
        posting.PostgresPostingTests.setUpClass()

    def test_emit_canonical_columns(self) -> None:
        """Fail intentionally with compact exact PostgreSQL 18 column fingerprints."""
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
                       ),
                       attribute.attidentity::text,
                       attribute.attgenerated::text,
                       COALESCE(
                           collation_namespace.nspname || '.' || collation_row.collname,
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
                LEFT JOIN pg_catalog.pg_collation AS collation_row
                  ON collation_row.oid = attribute.attcollation
                 AND attribute.attcollation <> 0
                LEFT JOIN pg_catalog.pg_namespace AS collation_namespace
                  ON collation_namespace.oid = collation_row.collnamespace
                WHERE namespace.nspname IN (
                    'accounting_core', 'accounting_integration', 'accounting_reporting'
                )
                  AND relation.relkind IN ('r', 'p')
                  AND attribute.attnum > 0
                  AND NOT attribute.attisdropped
                ORDER BY namespace.nspname, relation.relname, attribute.attnum
                """
            ).fetchall()
        grouped: dict[tuple[str, str], list[list[object]]] = defaultdict(list)
        for row in rows:
            grouped[(row[0], row[1])].append(list(row[2:]))
        fingerprints = [
            [
                schema_name,
                table_name,
                len(metadata),
                hashlib.sha256(
                    json.dumps(metadata, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
            ]
            for (schema_name, table_name), metadata in sorted(grouped.items())
        ]
        self.fail(
            "CANONICAL_COLUMN_FINGERPRINTS="
            + json.dumps(fingerprints, separators=(",", ":"))
        )


if __name__ == "__main__":
    unittest.main()
