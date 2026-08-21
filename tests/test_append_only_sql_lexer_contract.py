"""Regression tests for lexical append-only journal migration validation."""

from __future__ import annotations

import unittest

from scripts.validate_repository import (
    APPEND_ONLY_JOURNAL_MUTATION_ERROR,
    validate_append_only_journal_sql,
)


class AppendOnlySqlLexerContractTests(unittest.TestCase):
    """Distinguish executable destructive SQL from comments and string literals."""

    def test_comment_interposition_cannot_hide_destructive_journal_sql(self) -> None:
        """SQL comments between keywords must behave as token separators, not bypasses."""
        statements = (
            "DELETE/**/FROM accounting_core.general_journal;",
            "DROP/**/TABLE accounting_core.journal_entry_line;",
            'UPDATE/* audit */ accounting_core."general_journal" SET journal_status_code = \'posted\';',
            "TRUNCATE/* cleanup */TABLE accounting_core.journal_entry_line;",
        )
        for statement in statements:
            with self.subTest(statement=statement):
                self.assertEqual(
                    validate_append_only_journal_sql(statement),
                    (APPEND_ONLY_JOURNAL_MUTATION_ERROR,),
                )

    def test_multi_target_destructive_ddl_cannot_hide_journal_tables(self) -> None:
        """Every TRUNCATE/DROP target is checked, not only the first table name."""
        statements = (
            "TRUNCATE staging_table, accounting_core.general_journal;",
            "TRUNCATE TABLE ONLY staging_table, ONLY accounting_core.journal_entry_line RESTART IDENTITY;",
            "DROP TABLE staging_table, accounting_core.journal_entry_line;",
            'DROP TABLE IF EXISTS staging_table, accounting_core."general_journal" CASCADE;',
        )
        for statement in statements:
            with self.subTest(statement=statement):
                self.assertEqual(
                    validate_append_only_journal_sql(statement),
                    (APPEND_ONLY_JOURNAL_MUTATION_ERROR,),
                )

    def test_multi_target_unrelated_ddl_remains_valid(self) -> None:
        """Unrelated multi-table cleanup is not rejected by the append-only journal gate."""
        statements = (
            "TRUNCATE staging_one, staging_two;",
            "DROP TABLE IF EXISTS staging_one, staging_two CASCADE;",
        )
        for statement in statements:
            with self.subTest(statement=statement):
                self.assertEqual(validate_append_only_journal_sql(statement), ())

    def test_postgresql_quoted_boundaries_preserve_executable_sql_only(self) -> None:
        """PL/pgSQL bodies stay inspectable while data literals cannot create or hide mutations."""
        cases = (
            (
                "DO $$ BEGIN DELETE FROM accounting_core.general_journal; END $$;",
                (APPEND_ONLY_JOURNAL_MUTATION_ERROR,),
            ),
            (
                "SELECT $tag$DELETE FROM accounting_core.general_journal;$tag$;",
                (),
            ),
            (
                "SELECT E'it\\'s text'; DELETE FROM accounting_core.general_journal;",
                (APPEND_ONLY_JOURNAL_MUTATION_ERROR,),
            ),
        )
        for statement, expected in cases:
            with self.subTest(statement=statement):
                self.assertEqual(validate_append_only_journal_sql(statement), expected)

    def test_comments_and_string_literals_do_not_create_false_positive(self) -> None:
        """Non-executable journal-mutation text must remain valid migration prose/data."""
        statements = (
            "/* DELETE FROM accounting_core.general_journal; */ SELECT 1;",
            "/* outer\n/* nested */\n*/ SELECT 1;",
            "-- DROP TABLE accounting_core.journal_entry_line;\nSELECT 1;",
            "SELECT 'DELETE FROM accounting_core.general_journal;';",
            "SELECT 'it''s\nstill text';",
            "SELECT 'unterminated",
            "SELECT 'DROP/**/TABLE accounting_core.journal_entry_line;';",
        )
        for statement in statements:
            with self.subTest(statement=statement):
                self.assertEqual(validate_append_only_journal_sql(statement), ())


if __name__ == "__main__":
    unittest.main()
