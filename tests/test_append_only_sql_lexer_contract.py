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

    def test_comments_and_string_literals_do_not_create_false_positive(self) -> None:
        """Non-executable journal-mutation text must remain valid migration prose/data."""
        statements = (
            "/* DELETE FROM accounting_core.general_journal; */ SELECT 1;",
            "-- DROP TABLE accounting_core.journal_entry_line;\nSELECT 1;",
            "SELECT 'DELETE FROM accounting_core.general_journal;';",
            "SELECT 'DROP/**/TABLE accounting_core.journal_entry_line;';",
        )
        for statement in statements:
            with self.subTest(statement=statement):
                self.assertEqual(validate_append_only_journal_sql(statement), ())


if __name__ == "__main__":
    unittest.main()
