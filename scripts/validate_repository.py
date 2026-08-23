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
    "src/accounting_information_platform/accept.py",
    "src/accounting_information_platform/billing_pull.py",
    "src/accounting_information_platform/core.py",
    "src/accounting_information_platform/http_api.py",
    "src/accounting_information_platform/ingest.py",
    "src/accounting_information_platform/persistence.py",
    "src/accounting_information_platform/py.typed",
    "schemas/accounting-journal-proposal.schema.json",
    "schemas/accounting-posting-receipt.schema.json",
    "schemas/accounting-policy-manifest.schema.json",
    "database/migrations/0001_accounting_foundation.sql",
    "database/migrations/0002_chart_account_class.sql",
    "database/migrations/0003_home_tax_submission.sql",
    "database/migrations/0004_close_idempotency_key.sql",
    "database/migrations/0005_closed_period_guard.sql",
    "database/migrations/0006_concurrency_hot_partition.sql",
    "database/migrations/0007_runtime_tenant_binding.sql",
    "database/migrations/0008_fiscal_period_open_command.sql",
    "database/migrations/0009_accounting_book_period_control.sql",
    "docs/PRD.md",
    "docs/TRD.md",
    "docs/ARCHITECTURE.md",
    "docs/DATA_MODEL.md",
    "docs/ACCOUNTING_BOUNDARY.md",
    "docs/SECURITY.md",
    "docs/TEST_STRATEGY.md",
    "docs/OPERABILITY.md",
    "docs/product-technical-gap-baseline.md",
    "docs/adr/0001-accounting-authority.md",
    "docs/adr/0002-proposal-receipt-boundary.md",
    "docs/adr/0003-append-only-journals.md",
    "docs/adr/0004-exact-decimal-arithmetic.md",
    "docs/adr/0005-policy-driven-account-mapping.md",
    "docs/adr/0006-fiscal-period-close-snapshot.md",
    "docs/adr/0007-catalog-policy-resolution.md",
    "docs/adr/0008-http-journal-proposal-accept.md",
    "docs/adr/0009-http-posting-receipt-lookup.md",
    "docs/adr/0010-http-period-close-and-trial-balance.md",
    "docs/adr/0011-ais-pulls-billing-get.md",
    "docs/adr/0012-http-append-only-reversal.md",
    "docs/adr/0013-http-account-role-mapping-read.md",
    "docs/adr/0014-http-posted-journal-inquiry.md",
    "docs/adr/0015-http-fiscal-period-open.md",
    "docs/adr/0016-http-period-journal-list.md",
    "docs/adr/0017-http-outbox-read-and-publish.md",
    "docs/adr/0018-http-fiscal-period-list.md",
    "docs/adr/0019-http-account-ledger-inquiry.md",
    "docs/adr/0020-http-chart-account-catalog-read.md",
    "docs/adr/0021-http-financial-statement-read.md",
    "docs/adr/0022-http-accounting-book-list.md",
    "docs/adr/0023-http-two-step-period-close.md",
    "docs/adr/0024-hard-close-retained-earnings.md",
    "docs/adr/0025-http-financial-statement-comparison.md",
    "docs/adr/0026-http-legal-entity-list.md",
    "docs/adr/0027-http-audit-event-history.md",
    "docs/adr/0028-http-financial-statement-year-to-date.md",
    "docs/adr/0029-http-journal-reversal-list.md",
    "docs/adr/0030-http-period-close-list.md",
    "docs/adr/0031-http-adjusting-journal.md",
    "docs/adr/0032-http-changes-in-equity.md",
    "docs/adr/0033-http-cash-flow.md",
    "docs/adr/0034-http-account-balances.md",
    "docs/adr/0035-http-account-rollforward.md",
    "docs/adr/0036-http-trial-balance-basis.md",
    "docs/adr/0037-http-financial-statement-package.md",
    "docs/adr/0038-http-journal-source-list.md",
    "docs/adr/0039-http-receivable-aging.md",
    "docs/adr/0040-http-period-close-package.md",
    "docs/adr/0041-http-payable-aging.md",
    "docs/adr/0042-http-collection-write-off.md",
    "docs/adr/0043-http-unapplied-cash.md",
    "docs/adr/0044-http-unapplied-cash-rollforward.md",
    "docs/adr/0045-http-vat-period-register.md",
    "docs/adr/0046-http-home-tax-submission.md",
    "docs/adr/0047-wage-income-withholding-reservation.md",
    "docs/adr/0048-reproducible-package-evidence.md",
    "docs/adr/0049-runtime-tenant-database-binding.md",
    "docs/adr/0050-postgresql-concurrency-hot-partition.md",
    "docs/adr/0051-accounting-book-period-control.md",
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
HASH_TOKEN_PATTERN = re.compile(r"--hash=sha256:([0-9a-f]{64})")
PINNED_REQUIREMENT_PATTERN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==")
COVERAGE_UNIVERSAL_WHEEL_HASH = (
    "964730a1e9de9c0cf11be6a1a3c79ce419c34882842abd256086ba4698705e84"
)
COVERAGE_CP313_MANYLINUX_X86_64_WHEEL_HASH = (
    "12b59c90084e3234fb11184886bf4a40f4f16a8c8f867be2e087b81f8e8868d4"
)
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
POLICY_NAME_PATTERN = re.compile(
    r"\bCREATE\s+POLICY(?:\s+IF\s+NOT\s+EXISTS)?\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
FUNCTION_NAME_PATTERN = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+"
    r"(?:(?:[A-Za-z_][A-Za-z0-9_]*)\.)?([A-Za-z_][A-Za-z0-9_]*)\s*\(",
    re.IGNORECASE,
)
APPEND_ONLY_JOURNAL_MUTATION_ERROR = (
    "accounting migrations must not UPDATE, DELETE, TRUNCATE, or DROP TABLE "
    "general_journal or journal_entry_line"
)
_SQL_IDENTIFIER = r'(?:"(?:[^"]|"")*"|[A-Za-z_][A-Za-z0-9_]*)'
APPEND_ONLY_JOURNAL_MUTATION_PATTERN = re.compile(
    rf"\b(?:"
    rf"UPDATE(?:\s+ONLY)?|"
    rf"DELETE\s+FROM(?:\s+ONLY)?|"
    rf"TRUNCATE(?:\s+TABLE)?(?:\s+ONLY)?|"
    rf"DROP\s+TABLE(?:\s+IF\s+EXISTS)?"
    rf")\s+"
    rf"(?:{_SQL_IDENTIFIER}\s*\.\s*)?"
    rf'(?:general_journal|journal_entry_line|"general_journal"|"journal_entry_line")'
    rf"(?=\s|;|$)",
    re.IGNORECASE,
)
_DOLLAR_QUOTE_PATTERN = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


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


def _dollar_body_is_executable(prefix: str) -> bool:
    """Return whether a dollar-quoted body is executable migration code rather than data."""
    stripped = prefix.rstrip()
    return bool(
        re.search(r"\bAS\s*$", stripped, re.IGNORECASE)
        or re.search(
            r"\bDO(?:\s+LANGUAGE\s+[A-Za-z_][A-Za-z0-9_]*)?\s*$",
            stripped,
            re.IGNORECASE,
        )
    )


def _lex_executable_sql(sql_text: str) -> str:
    """Mask SQL comments and data literals while preserving executable migration code."""
    output: list[str] = []
    index = 0
    length = len(sql_text)
    while index < length:
        if sql_text.startswith("--", index):
            output.append(" ")
            index += 2
            while index < length and sql_text[index] not in "\r\n":
                index += 1
            continue
        if sql_text.startswith("/*", index):
            output.append(" ")
            index += 2
            depth = 1
            while index < length and depth:
                if sql_text.startswith("/*", index):
                    depth += 1
                    index += 2
                    continue
                if sql_text.startswith("*/", index):
                    depth -= 1
                    index += 2
                    continue
                if sql_text[index] in "\r\n":
                    output.append(sql_text[index])
                index += 1
            continue
        if sql_text[index] == "$":
            delimiter_match = _DOLLAR_QUOTE_PATTERN.match(sql_text, index)
            if delimiter_match is not None:
                delimiter = delimiter_match.group(0)
                body_start = delimiter_match.end()
                body_end = sql_text.find(delimiter, body_start)
                if body_end < 0:
                    output.append(sql_text[index])
                    index += 1
                    continue
                body = sql_text[body_start:body_end]
                if _dollar_body_is_executable("".join(output)):
                    output.append(" ")
                    output.append(_lex_executable_sql(body))
                    output.append(" ")
                else:
                    output.append(" ")
                    output.extend(character for character in body if character in "\r\n")
                index = body_end + len(delimiter)
                continue
        if sql_text[index] == "'":
            escape_backslash = bool(
                index > 0
                and sql_text[index - 1] in "Ee"
                and (
                    index < 2
                    or not (
                        sql_text[index - 2].isalnum() or sql_text[index - 2] == "_"
                    )
                )
            )
            output.append(" ")
            index += 1
            while index < length:
                if escape_backslash and sql_text[index] == "\\" and index + 1 < length:
                    index += 2
                    continue
                if sql_text[index] == "'":
                    if index + 1 < length and sql_text[index + 1] == "'":
                        index += 2
                        continue
                    index += 1
                    break
                if sql_text[index] in "\r\n":
                    output.append(sql_text[index])
                index += 1
            continue
        output.append(sql_text[index])
        index += 1
    return "".join(output)


def validate_append_only_journal_sql(sql_text: str) -> tuple[str, ...]:
    """Reject executable destructive mutation of posted journal tables in migrations."""
    executable_sql = _lex_executable_sql(sql_text)
    if APPEND_ONLY_JOURNAL_MUTATION_PATTERN.search(executable_sql) is not None:
        return (APPEND_ONLY_JOURNAL_MUTATION_ERROR,)

    table_list_pattern = re.compile(
        r"\b(?:TRUNCATE(?:\s+TABLE)?|DROP\s+TABLE(?:\s+IF\s+EXISTS)?)\s+([^;]+)",
        re.IGNORECASE,
    )
    protected_tables = {"general_journal", "journal_entry_line"}
    for command_match in table_list_pattern.finditer(executable_sql):
        targets_text = re.split(
            r"\b(?:RESTART\s+IDENTITY|CONTINUE\s+IDENTITY|CASCADE|RESTRICT)\b",
            command_match.group(1),
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        for raw_target in targets_text.split(","):
            target = re.sub(r"^\s*ONLY\s+", "", raw_target, flags=re.IGNORECASE)
            target = target.strip().removesuffix("*").strip()
            identifier = target.rsplit(".", 1)[-1].strip()
            if identifier.startswith('"') and identifier.endswith('"'):
                identifier = identifier[1:-1].replace('""', '"')
            if identifier.lower() in protected_tables:
                return (APPEND_ONLY_JOURNAL_MUTATION_ERROR,)
    return ()


def validate_sql_object_names(sql_text: str) -> tuple[str, ...]:
    """Require created schemas, tables, columns, policies, and functions to use two-word snake case."""
    errors: list[str] = []
    named_objects = (
        ("schema", SCHEMA_NAME_PATTERN.findall(sql_text)),
        ("table", TABLE_NAME_PATTERN.findall(sql_text)),
        ("column", COLUMN_NAME_PATTERN.findall(sql_text)),
        ("policy", POLICY_NAME_PATTERN.findall(sql_text)),
        ("function", FUNCTION_NAME_PATTERN.findall(sql_text)),
    )
    for object_type, object_names in named_objects:
        for object_name in object_names:
            if TWO_WORD_SNAKE_PATTERN.fullmatch(object_name) is None:
                errors.append(
                    f"{object_type} name must contain at least two snake_case words: "
                    f"{object_name}"
                )
    return tuple(errors)


def validate_quality_requirements(requirements_text: str) -> tuple[str, ...]:
    """Require hash-locked coverage wheels and the no-build-isolation packaging backend."""
    errors: list[str] = []
    package_hashes: dict[str, set[str]] = {}
    current_package: str | None = None

    for raw_line in requirements_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        hashes = set(HASH_TOKEN_PATTERN.findall(line))
        requirement_line = HASH_TOKEN_PATTERN.sub("", line).replace("\\", "").strip()
        if requirement_line:
            name_match = PINNED_REQUIREMENT_PATTERN.match(requirement_line)
            if name_match is None:
                errors.append(f"unrecognized quality dependency line: {requirement_line}")
                current_package = None
            else:
                current_package = name_match.group(1).lower()
                package_hashes.setdefault(current_package, set())
        if hashes:
            if current_package is None:
                errors.append("hash lock is not attached to a quality dependency")
            else:
                package_hashes.setdefault(current_package, set()).update(hashes)

    if not any(package_hashes.values()):
        errors.append("quality dependencies must be hash locked")

    if "coverage" not in package_hashes:
        errors.append("quality dependencies must pin coverage")
    else:
        coverage_hashes = package_hashes["coverage"]
        if COVERAGE_UNIVERSAL_WHEEL_HASH not in coverage_hashes:
            errors.append("coverage must pin the universal py3-none-any wheel hash")
        if COVERAGE_CP313_MANYLINUX_X86_64_WHEEL_HASH not in coverage_hashes:
            errors.append(
                "coverage must pin the CPython 3.13 manylinux x86_64 wheel hash"
            )

    if "setuptools" not in package_hashes:
        errors.append(
            "quality dependencies must pin setuptools for no-build-isolation packaging"
        )
    elif not package_hashes["setuptools"]:
        errors.append("setuptools must be hash locked")

    if "wheel" not in package_hashes:
        errors.append(
            "quality dependencies must pin wheel for no-build-isolation packaging"
        )
    elif not package_hashes["wheel"]:
        errors.append("wheel must be hash locked")

    if "packaging" not in package_hashes:
        errors.append(
            "quality dependencies must pin packaging for setuptools license metadata"
        )
    elif not package_hashes["packaging"]:
        errors.append("packaging must be hash locked")

    if "psycopg" not in package_hashes:
        errors.append(
            "quality dependencies must pin psycopg for PostgreSQL persistence tests"
        )
    elif not package_hashes["psycopg"]:
        errors.append("psycopg must be hash locked")

    if "psycopg-binary" not in package_hashes:
        errors.append(
            "quality dependencies must pin psycopg-binary for PostgreSQL persistence tests"
        )
    elif not package_hashes["psycopg-binary"]:
        errors.append("psycopg-binary must be hash locked")

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

    migrations_directory = root / "database/migrations"
    if migrations_directory.is_dir():
        for migration_path in sorted(migrations_directory.glob("*.sql")):
            sql_text = migration_path.read_text(encoding="utf-8")
            errors.extend(validate_sql_object_names(sql_text))
            errors.extend(validate_append_only_journal_sql(sql_text))

    requirements_path = root / "requirements-quality.txt"
    if requirements_path.is_file():
        errors.extend(
            validate_quality_requirements(requirements_path.read_text(encoding="utf-8"))
        )

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
    """Return checked-in text contracts while excluding tests and generated state."""
    included_suffixes = {".json", ".md", ".py", ".sql", ".toml", ".yaml", ".yml"}
    excluded_parts = {
        ".git",
        ".codegraph",
        ".venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        "dist",
        "tests",
    }
    files: list[Path] = []
    for file_path in root.rglob("*"):
        relative_parts = file_path.relative_to(root).parts
        if not file_path.is_file() or file_path.suffix not in included_suffixes:
            continue
        if any(part in excluded_parts for part in relative_parts):
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
