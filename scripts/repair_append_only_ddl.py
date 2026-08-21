"""One-shot normalization for multi-target append-only journal DDL validation.

This helper exists only to apply and validate the current PR repair. The
normalization workflow removes it before publishing the canonical repaired head.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts/validate_repository.py"


def main() -> None:
    """Make TRUNCATE and DROP TABLE inspect every comma-separated target."""
    text = VALIDATOR.read_text(encoding="utf-8")
    old = '''def validate_append_only_journal_sql(sql_text: str) -> tuple[str, ...]:
    """Reject executable destructive mutation of posted journal tables in migrations."""
    executable_sql = _lex_executable_sql(sql_text)
    if APPEND_ONLY_JOURNAL_MUTATION_PATTERN.search(executable_sql) is None:
        return ()
    return (APPEND_ONLY_JOURNAL_MUTATION_ERROR,)
'''
    new = '''def validate_append_only_journal_sql(sql_text: str) -> tuple[str, ...]:
    """Reject executable destructive mutation of posted journal tables in migrations."""
    executable_sql = _lex_executable_sql(sql_text)
    if APPEND_ONLY_JOURNAL_MUTATION_PATTERN.search(executable_sql) is not None:
        return (APPEND_ONLY_JOURNAL_MUTATION_ERROR,)

    table_list_pattern = re.compile(
        r"\\b(?:TRUNCATE(?:\\s+TABLE)?|DROP\\s+TABLE(?:\\s+IF\\s+EXISTS)?)\\s+([^;]+)",
        re.IGNORECASE,
    )
    protected_tables = {"general_journal", "journal_entry_line"}
    for command_match in table_list_pattern.finditer(executable_sql):
        targets_text = re.split(
            r"\\b(?:RESTART\\s+IDENTITY|CONTINUE\\s+IDENTITY|CASCADE|RESTRICT)\\b",
            command_match.group(1),
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        for raw_target in targets_text.split(","):
            target = re.sub(r"^\\s*ONLY\\s+", "", raw_target, flags=re.IGNORECASE)
            target = target.strip().removesuffix("*").strip()
            identifier = target.rsplit(".", 1)[-1].strip()
            if identifier.startswith('"') and identifier.endswith('"'):
                identifier = identifier[1:-1].replace('""', '"')
            if identifier.lower() in protected_tables:
                return (APPEND_ONLY_JOURNAL_MUTATION_ERROR,)
    return ()
'''
    if text.count(old) != 1:
        raise SystemExit("append-only validator anchor drifted")
    VALIDATOR.write_text(text.replace(old, new, 1), encoding="utf-8")


if __name__ == "__main__":
    main()
