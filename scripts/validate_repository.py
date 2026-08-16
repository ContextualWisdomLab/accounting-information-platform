"""Validate portable repository contracts without network access."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Sequence


DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
REQUIRED_FILES = (
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "CHANGELOG.md",
    "LICENSE",
    "pyproject.toml",
    "requirements-quality.txt",
    ".coveragerc",
    ".github/workflows/ci.yml",
    "src/accounting_information_platform/__init__.py",
    "src/accounting_information_platform/core.py",
    "src/accounting_information_platform/py.typed",
    "schemas/accounting-journal-proposal.schema.json",
    "schemas/accounting-posting-receipt.schema.json",
    "schemas/accounting-policy-manifest.schema.json",
    "database/migrations/0001_accounting_foundation.sql",
    "docs/PRD.md",
    "docs/TRD.md",
    "docs/ARCHITECTURE.md",
    "docs/DATA_MODEL.md",
    "docs/ACCOUNTING_BOUNDARY.md",
    "docs/SECURITY.md",
    "docs/TEST_STRATEGY.md",
    "docs/OPERABILITY.md",
    "docs/adr/0001-accounting-authority.md",
    "docs/adr/0002-proposal-receipt-boundary.md",
    "docs/adr/0003-append-only-journals.md",
    "docs/adr/0004-exact-decimal-arithmetic.md",
    "docs/adr/0005-policy-driven-account-mapping.md",
    "docs/doctoring/REFERENCES.md",
    "docs/doctoring/STANDARD_TRACEABILITY.md",
    "docs/superpowers/specs/2026-08-16-accounting-information-platform-design.md",
    "docs/superpowers/plans/2026-08-16-initial-accounting-foundation.md",
)
ACTION_REFERENCE_PATTERN = re.compile(
    r"\buses:\s*([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([^\s#]+)"
)
FULL_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
PLACEHOLDER_PATTERN = re.compile(
    r"\b(" + "|".join(("TO" + "DO", "T" + "BD", "FIX" + "ME")) + r")\b"
)
TWO_WORD_SNAKE_PATTERN = re.compile(r"^[a-z][a-z0-9]*_[a-z0-9_]+$")
SCHEMA_NAME_PATTERN = re.compile(
    r"\bCREATE\s+SCHEMA(?:\s+IF\s+NOT\s+EXISTS)?\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
TABLE_NAME_PATTERN = re.compile(
    r"\bCREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+"
    r"(?:(?:[A-Za-z_][A-Za-z0-9_]*)\.)?([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
COLUMN_NAME_PATTERN = re.compile(
    r"^\s+([A-Za-z_][A-Za-z0-9_]*)\s+"
    r"(?:uuid|text|timestamptz|timestamp|date|integer|bigint|numeric|boolean|jsonb)\b",
    re.IGNORECASE | re.MULTILINE,
)


def find_mutable_action_references(text: str) -> tuple[str, ...]:
    """Return GitHub Action references not pinned to a full commit SHA."""
    references = {
        f"{match.group(1)}@{match.group(2)}"
        for match in ACTION_REFERENCE_PATTERN.finditer(text)
        if FULL_COMMIT_PATTERN.fullmatch(match.group(2)) is None
    }
    return tuple(sorted(references))


def find_placeholder_tokens(text: str) -> tuple[str, ...]:
    """Return unresolved implementation placeholder tokens in *text*."""
    return tuple(sorted({match.group(1) for match in PLACEHOLDER_PATTERN.finditer(text)}))


def validate_sql_object_names(sql_text: str) -> tuple[str, ...]:
    """Require created schemas, tables, and columns to use two-word snake case."""
    errors: list[str] = []
    for schema_name in SCHEMA_NAME_PATTERN.findall(sql_text):
        if TWO_WORD_SNAKE_PATTERN.fullmatch(schema_name) is None:
            errors.append(
                f"schema name must contain at least two snake_case words: {schema_name}"
            )
    for table_name in TABLE_NAME_PATTERN.findall(sql_text):
        if TWO_WORD_SNAKE_PATTERN.fullmatch(table_name) is None:
            errors.append(
                f"table name must contain at least two snake_case words: {table_name}"
            )
    for column_name in COLUMN_NAME_PATTERN.findall(sql_text):
        if TWO_WORD_SNAKE_PATTERN.fullmatch(column_name) is None:
            errors.append(
                f"column name must contain at least two snake_case words: {column_name}"
            )
    return tuple(errors)



def validate_public_docstrings(source_root: Path) -> tuple[str, ...]:
    """Require docstrings on every shipped public Python symbol below *source_root*."""
    errors: list[str] = []
    if not source_root.is_dir():
        return ()
    for source_path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        relative_path = source_path.relative_to(source_root).as_posix()
        if ast.get_docstring(tree) is None:
            errors.append(f"missing module docstring: {relative_path}")
        for node in ast.walk(tree):
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue
            if ast.get_docstring(node) is None:
                errors.append(f"missing public docstring: {relative_path}:{node.name}")
    return tuple(errors)

def validate_repository(root: Path) -> tuple[str, ...]:
    """Return all deterministic repository-contract violations below *root*."""
    errors: list[str] = []
    for relative_path in REQUIRED_FILES:
        if not (root / relative_path).is_file():
            errors.append(f"missing required file: {relative_path}")

    errors.extend(validate_public_docstrings(root / "src"))

    schema_ids: set[str] = set()
    schemas_directory = root / "schemas"
    if schemas_directory.is_dir():
        for schema_path in sorted(schemas_directory.glob("*.schema.json")):
            errors.extend(_validate_schema_file(schema_path, schema_ids))

    migration_path = root / "database/migrations/0001_accounting_foundation.sql"
    if migration_path.is_file():
        sql_text = migration_path.read_text(encoding="utf-8")
        errors.extend(validate_sql_object_names(sql_text))
        if re.search(r"\bDELETE\s+FROM\b", sql_text, re.IGNORECASE):
            errors.append("accounting migrations must not define destructive journal deletion")

    requirements_path = root / "requirements-quality.txt"
    if requirements_path.is_file():
        requirements_text = requirements_path.read_text(encoding="utf-8")
        if "--hash=sha256:" not in requirements_text:
            errors.append("quality dependencies must be hash locked")

    for file_path in _iter_contract_files(root):
        text = file_path.read_text(encoding="utf-8")
        relative_path = file_path.relative_to(root).as_posix()
        for token in find_placeholder_tokens(text):
            errors.append(f"unresolved placeholder in {relative_path}: {token}")
        if file_path.suffix in {".yml", ".yaml"}:
            for reference in find_mutable_action_references(text):
                errors.append(
                    f"mutable GitHub Action reference in {relative_path}: {reference}"
                )
    return tuple(errors)


def main(arguments: Sequence[str] | None = None) -> int:
    """Validate the supplied repository root and print actionable diagnostics."""
    supplied_arguments = tuple(sys.argv[1:] if arguments is None else arguments)
    root = Path(supplied_arguments[0]).resolve() if supplied_arguments else Path.cwd()
    errors = validate_repository(root)
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"repository contracts valid: {root}")
    return 0


def _iter_contract_files(root: Path) -> tuple[Path, ...]:
    """Return checked-in text contracts while excluding tests and cache state."""
    included_suffixes = {".json", ".md", ".py", ".sql", ".toml", ".yaml", ".yml"}
    files: list[Path] = []
    for file_path in root.rglob("*"):
        relative_parts = file_path.relative_to(root).parts
        if not file_path.is_file() or file_path.suffix not in included_suffixes:
            continue
        if any(part in {".git", "__pycache__", "tests"} for part in relative_parts):
            continue
        files.append(file_path)
    return tuple(sorted(files))


def _validate_schema_file(schema_path: Path, schema_ids: set[str]) -> tuple[str, ...]:
    """Validate one JSON Schema root and its globally unique HTTPS identifier."""
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return (f"invalid JSON in {schema_path.name}: {error.msg}",)
    errors: list[str] = []
    if schema.get("$schema") != DRAFT_2020_12:
        errors.append(f"schema must declare Draft 2020-12: {schema_path.name}")
    schema_id = schema.get("$id")
    if not isinstance(schema_id, str) or not schema_id.startswith("https://"):
        errors.append(f"schema must have an HTTPS $id: {schema_path.name}")
    elif schema_id in schema_ids:
        errors.append(f"duplicate schema $id: {schema_id}")
    else:
        schema_ids.add(schema_id)
    if schema.get("type") != "object":
        errors.append(f"schema root must be an object: {schema_path.name}")
    if schema.get("additionalProperties") is not False:
        errors.append(f"schema root must reject additional properties: {schema_path.name}")
    return tuple(errors)


if __name__ == "__main__":  # pragma: no cover - main() is tested directly.
    raise SystemExit(main())
