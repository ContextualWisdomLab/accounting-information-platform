"""Temporary generator for exact PostgreSQL-18 readiness definition fingerprints."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg

from accounting_information_platform.persistence import (
    _READINESS_CONSTRAINTS,
    _READINESS_INDEXES,
    apply_foundation_migration,
)


def _render_mapping(name: str, items: list[tuple[object, object]]) -> str:
    lines = [f"{name} = {{"]
    for key, value in items:
        lines.append(f"    {key!r}: {value!r},")
    lines.append("}")
    return "\n".join(lines)


def main() -> None:
    database_url = os.environ["ACCOUNTING_DATABASE_URL"]
    apply_foundation_migration(
        database_url,
        Path("database/migrations/0001_accounting_foundation.sql"),
    )
    constraint_items: list[tuple[object, object]] = []
    index_items: list[tuple[object, object]] = []
    with psycopg.connect(database_url, autocommit=True) as connection:
        for identity in _READINESS_CONSTRAINTS:
            row = connection.execute(
                """
                SELECT constraint_record.contype::text,
                       constraint_record.convalidated,
                       constraint_record.conenforced,
                       md5(pg_get_constraintdef(constraint_record.oid, true))
                FROM pg_catalog.pg_constraint AS constraint_record
                JOIN pg_catalog.pg_class AS relation
                  ON relation.oid = constraint_record.conrelid
                JOIN pg_catalog.pg_namespace AS namespace
                  ON namespace.oid = relation.relnamespace
                WHERE namespace.nspname = %s
                  AND relation.relname = %s
                  AND constraint_record.conname = %s
                """,
                identity,
            ).fetchone()
            if row is None or not row[1] or not row[2]:
                raise RuntimeError(f"canonical constraint is not enforced: {identity!r}")
            constraint_items.append((identity, (row[0], row[3])))
        for index_name in _READINESS_INDEXES:
            row = connection.execute(
                """
                SELECT index_metadata.indisvalid,
                       index_metadata.indisready,
                       index_metadata.indislive,
                       md5(pg_get_indexdef(index_relation.oid))
                FROM pg_catalog.pg_class AS index_relation
                JOIN pg_catalog.pg_index AS index_metadata
                  ON index_metadata.indexrelid = index_relation.oid
                WHERE index_relation.oid = %s::regclass
                """,
                (index_name,),
            ).fetchone()
            if row is None or not all(row[:3]):
                raise RuntimeError(f"canonical index is not valid/ready/live: {index_name}")
            index_items.append((index_name, row[3]))

    source_path = Path("src/accounting_information_platform/persistence.py")
    source = source_path.read_text(encoding="utf-8")
    constant_marker = "_READINESS_BALANCE_TRIGGERS = ("
    if source.count(constant_marker) != 1:
        raise RuntimeError("unexpected balance-trigger constant marker count")
    generated = (
        _render_mapping("_READINESS_CONSTRAINT_FINGERPRINTS", constraint_items)
        + "\n\n"
        + _render_mapping("_READINESS_INDEX_FINGERPRINTS", index_items)
        + "\n\n"
    )
    source = source.replace(constant_marker, generated + constant_marker)

    old_constraint = '''                        NOT EXISTS (
                            SELECT 1
                            FROM unnest(%s::text[], %s::text[], %s::text[])
                                AS required(schema_name, table_name, constraint_name)
                            LEFT JOIN pg_catalog.pg_namespace
                              ON pg_namespace.nspname = required.schema_name
                            LEFT JOIN pg_catalog.pg_class
                              ON pg_class.relnamespace = pg_namespace.oid
                             AND pg_class.relname = required.table_name
                            LEFT JOIN pg_catalog.pg_constraint
                              ON pg_constraint.conrelid = pg_class.oid
                             AND pg_constraint.conname = required.constraint_name
                            WHERE pg_constraint.oid IS NULL
                        )
'''
    new_constraint = '''                        NOT EXISTS (
                            SELECT 1
                            FROM unnest(
                                %s::text[], %s::text[], %s::text[],
                                %s::text[], %s::text[]
                            ) AS required(
                                schema_name, table_name, constraint_name,
                                constraint_type, definition_fingerprint
                            )
                            LEFT JOIN pg_catalog.pg_namespace
                              ON pg_namespace.nspname = required.schema_name
                            LEFT JOIN pg_catalog.pg_class
                              ON pg_class.relnamespace = pg_namespace.oid
                             AND pg_class.relname = required.table_name
                            LEFT JOIN pg_catalog.pg_constraint
                              ON pg_constraint.conrelid = pg_class.oid
                             AND pg_constraint.conname = required.constraint_name
                            WHERE pg_constraint.oid IS NULL
                               OR pg_constraint.contype::text <> required.constraint_type
                               OR NOT pg_constraint.convalidated
                               OR NOT pg_constraint.conenforced
                               OR pg_catalog.md5(
                                    pg_catalog.pg_get_constraintdef(
                                        pg_constraint.oid,
                                        true
                                    )
                                  ) <> required.definition_fingerprint
                        )
'''
    if source.count(old_constraint) != 1:
        raise RuntimeError("unexpected constraint readiness query marker count")
    source = source.replace(old_constraint, new_constraint)

    old_index = '''                        NOT EXISTS (
                            SELECT 1
                            FROM unnest(%s::text[]) AS required(index_name)
                            WHERE to_regclass(required.index_name) IS NULL
                        )
'''
    new_index = '''                        NOT EXISTS (
                            SELECT 1
                            FROM unnest(%s::text[], %s::text[])
                                AS required(index_name, definition_fingerprint)
                            LEFT JOIN pg_catalog.pg_class AS index_relation
                              ON index_relation.oid = to_regclass(required.index_name)
                            LEFT JOIN pg_catalog.pg_index AS index_metadata
                              ON index_metadata.indexrelid = index_relation.oid
                            WHERE index_relation.oid IS NULL
                               OR index_metadata.indexrelid IS NULL
                               OR NOT index_metadata.indisvalid
                               OR NOT index_metadata.indisready
                               OR NOT index_metadata.indislive
                               OR pg_catalog.md5(
                                    pg_catalog.pg_get_indexdef(index_relation.oid)
                                  ) <> required.definition_fingerprint
                        )
'''
    if source.count(old_index) != 1:
        raise RuntimeError("unexpected index readiness query marker count")
    source = source.replace(old_index, new_index)

    old_constraint_params = '''                        [item[0] for item in _READINESS_CONSTRAINTS],
                        [item[1] for item in _READINESS_CONSTRAINTS],
                        [item[2] for item in _READINESS_CONSTRAINTS],
                        [item[0] for item in _READINESS_BALANCE_TRIGGERS],
'''
    new_constraint_params = '''                        [item[0] for item in _READINESS_CONSTRAINTS],
                        [item[1] for item in _READINESS_CONSTRAINTS],
                        [item[2] for item in _READINESS_CONSTRAINTS],
                        [
                            _READINESS_CONSTRAINT_FINGERPRINTS[item][0]
                            for item in _READINESS_CONSTRAINTS
                        ],
                        [
                            _READINESS_CONSTRAINT_FINGERPRINTS[item][1]
                            for item in _READINESS_CONSTRAINTS
                        ],
                        [item[0] for item in _READINESS_BALANCE_TRIGGERS],
'''
    if source.count(old_constraint_params) != 1:
        raise RuntimeError("unexpected constraint readiness params marker count")
    source = source.replace(old_constraint_params, new_constraint_params)

    old_index_params = '''                        list(_READINESS_INDEXES),
                    ),
'''
    new_index_params = '''                        list(_READINESS_INDEXES),
                        [
                            _READINESS_INDEX_FINGERPRINTS[item]
                            for item in _READINESS_INDEXES
                        ],
                    ),
'''
    if source.count(old_index_params) != 1:
        raise RuntimeError("unexpected index readiness params marker count")
    source = source.replace(old_index_params, new_index_params)
    source_path.write_text(source, encoding="utf-8")


if __name__ == "__main__":
    main()
