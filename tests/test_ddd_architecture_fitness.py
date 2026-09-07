"""Architectural fitness tests for accounting bounded-context ownership."""

from __future__ import annotations

import ast
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "accounting_information_platform"
CONTEXT_MAP = ROOT / "docs" / "CONTEXT_MAP.md"
UBIQUITOUS_LANGUAGE = ROOT / "docs" / "UBIQUITOUS_LANGUAGE.md"
CONTEXT_MAP_ADR = ROOT / "docs" / "adr" / "0059-accounting-bounded-context-map.md"

BOUND_CONTEXTS = {
    "proposal_intake",
    "policy_resolution",
    "journal_posting",
    "journal_reversal",
    "close_control",
    "trial_balance",
    "reporting_projection",
    "integration_outbox",
    "tax_interface",
    "bank_statement_registry",
    "reconciliation_run_control",
    "reconciliation_review",
}
TECHNICAL_PRIMARY_OWNER_EXCEPTIONS = {
    "src/accounting_information_platform/migration_install.py": "deployment_infrastructure",
}
GENERIC_BUCKET_NAMES = {
    "utils",
    "helpers",
    "common",
    "services",
    "lib",
    "shared",
    "core",
    "models",
    "misc",
    "legacy",
}
TRANSITIONAL_GENERIC_PATHS = {PACKAGE / "core.py"}
LOCAL_APPLICATION_IMPORT_ROOT = "accounting_information_platform"
APPROVED_THIRD_PARTY_IMPORT_ROOTS: frozenset[str] = frozenset()
REQUIRED_UBIQUITOUS_TERMS = {
    "Journal proposal",
    "General journal",
    "Posting receipt",
    "Reversal",
    "Fiscal period",
    "Soft close",
    "Hard close",
    "Reconciliation run",
    "Approval evidence",
    "Reconciliation exception",
    "Book-to-bank bridge",
    "Transactional outbox evidence",
    "Anti-Corruption Layer (ACL)",
    "Effective time",
    "System time",
}


def _import_roots(path: Path) -> set[str]:
    """Return top-level imported package names from one production module."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _forbidden_external_import_roots(roots: set[str]) -> set[str]:
    """Reject every undeclared absolute import instead of naming sibling applications one by one."""
    return roots - set(sys.stdlib_module_names) - {
        "__future__",
        LOCAL_APPLICATION_IMPORT_ROOT,
        *APPROVED_THIRD_PARTY_IMPORT_ROOTS,
    }


def _physical_ownership_rows(text: str) -> list[tuple[str, str, str]]:
    """Parse physical path, primary owner and transitional responsibility cells."""
    header = (
        "| Physical path | Primary owner | Transitional responsibilities | "
        "DDD status | Next correction |"
    )
    lines = text.splitlines()
    if header not in lines:
        return []
    start = lines.index(header) + 2
    rows: list[tuple[str, str, str]] = []
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 5:
            continue
        path_cell, primary_owner, transitional_responsibilities, _, _ = cells
        if not path_cell.startswith("`src/accounting_information_platform/"):
            continue
        rows.append(
            (
                path_cell.strip("`"),
                primary_owner,
                transitional_responsibilities,
            )
        )
    return rows


def _primary_ownership_rows_for_path(
    relative_path: str,
    rows: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    """Return the most-specific exact or package-directory ownership rows for one module."""
    candidates = [
        row
        for row in rows
        if row[0] == relative_path
        or (row[0].endswith("/") and relative_path.startswith(row[0]))
    ]
    if not candidates:
        return []
    most_specific_length = max(len(row[0]) for row in candidates)
    return [row for row in candidates if len(row[0]) == most_specific_length]


class DddArchitectureFitnessTests(unittest.TestCase):
    """Prevent new ownership ambiguity while legacy flat modules are split safely."""

    def test_context_map_declares_every_bounded_context_and_relationship_guard(self) -> None:
        """Keep authority contexts explicit rather than inferring them from folder names."""
        text = CONTEXT_MAP.read_text(encoding="utf-8")
        for context in BOUND_CONTEXTS:
            self.assertIn(f"`{context}`", text)
        self.assertIn("ContextualWisdomLab/context-graph-contracts", text)
        self.assertIn("minimal cross-repository Shared Kernel", text)
        self.assertIn("enterprise-architecture-core", text)
        self.assertIn("Anti-Corruption Layer", text)
        self.assertIn("Published Language", text)
        self.assertIn("transitional-debt", text)
        self.assertIn("does not authorize another service to write accounting tables", text)
        self.assertIn("0059-accounting-bounded-context-map.md", text)

    def test_context_map_does_not_outrun_the_proposed_adr(self) -> None:
        """Keep the context-map claim Proposed until ADR 0059 is integrated with evidence."""
        context_map = CONTEXT_MAP.read_text(encoding="utf-8")
        adr = CONTEXT_MAP_ADR.read_text(encoding="utf-8")
        self.assertIn("Status: Proposed", adr)
        self.assertIn("Status: proposed architecture", context_map)
        self.assertNotIn("Status: accepted architecture", context_map)

    def test_ubiquitous_language_does_not_outrun_the_proposed_adr(self) -> None:
        """Keep architecture vocabulary Proposed while ADR 0059 remains Proposed."""
        ubiquitous_language = UBIQUITOUS_LANGUAGE.read_text(encoding="utf-8")
        adr = CONTEXT_MAP_ADR.read_text(encoding="utf-8")
        self.assertIn("Status: Proposed", adr)
        self.assertIn("Status: proposed architecture vocabulary", ubiquitous_language)
        self.assertNotIn("Status: accepted architecture vocabulary", ubiquitous_language)

    def test_proposed_adr_owns_the_context_map_decision_until_integration(self) -> None:
        """Keep an unintegrated architecture decision Proposed until evidence is complete."""
        text = CONTEXT_MAP_ADR.read_text(encoding="utf-8")
        self.assertIn("Status: Proposed", text)
        self.assertNotIn("Status: Accepted", text)
        self.assertIn("ContextualWisdomLab/context-graph-contracts", text)
        self.assertIn("minimal cross-repository Shared Kernel", text)
        self.assertIn("enterprise-architecture-core", text)
        self.assertIn("Anti-Corruption Layer", text)
        self.assertIn("published proposal/API/event contracts", text)
        for context in BOUND_CONTEXTS:
            self.assertIn(f"`{context}`", text)

    def test_context_fabric_shared_kernel_preserves_accounting_authority(self) -> None:
        """Allow only released contract grammar to cross the Context Fabric boundary."""
        context_map = CONTEXT_MAP.read_text(encoding="utf-8")
        adr = CONTEXT_MAP_ADR.read_text(encoding="utf-8")
        for text in (context_map, adr):
            self.assertIn("released `cwl-context-contracts`", text)
            self.assertIn("Context Assertion", text)
            self.assertIn("CloudEvents", text)
            self.assertIn("truth status", text)
            self.assertIn("valid/system time", text)
            self.assertIn("provenance", text)
            self.assertIn("journal/ledger balances", text)
            self.assertIn("cross-service SQL", text)
        self.assertIn("EA Decision Plane", context_map)
        self.assertIn("architecture/change evidence only", context_map)

    def test_every_production_module_has_exactly_one_primary_owner(self) -> None:
        """Require one accountable owner for root and nested production modules."""
        text = CONTEXT_MAP.read_text(encoding="utf-8")
        rows = _physical_ownership_rows(text)
        self.assertTrue(
            rows,
            msg=(
                "docs/CONTEXT_MAP.md must expose a parseable physical-ownership table "
                "with one Primary owner column"
            ),
        )
        production_paths = sorted(
            path.relative_to(ROOT).as_posix()
            for path in PACKAGE.rglob("*.py")
            if path.name != "__init__.py"
        )
        for relative in production_paths:
            matches = _primary_ownership_rows_for_path(relative, rows)
            self.assertEqual(
                1,
                len(matches),
                msg=f"{relative} needs exactly one most-specific physical-ownership row",
            )
            primary_owner_codes = re.findall(r"`([^`]+)`", matches[0][1])
            self.assertEqual(
                1,
                len(primary_owner_codes),
                msg=f"{relative} needs exactly one primary owner token",
            )
            expected_technical_owner = TECHNICAL_PRIMARY_OWNER_EXCEPTIONS.get(relative)
            if expected_technical_owner is not None:
                self.assertEqual([expected_technical_owner], primary_owner_codes)
            else:
                self.assertIn(
                    primary_owner_codes[0],
                    BOUND_CONTEXTS,
                    msg=f"{relative} primary owner must be one declared bounded context",
                )

    def test_directory_owner_covers_nested_modules_but_not_undeclared_packages(self) -> None:
        """Treat a documented package row as ownership for descendants, never unrelated packages."""
        rows = [
            (
                "src/accounting_information_platform/iso20022/",
                "`bank_statement_registry`",
                "None",
            )
        ]
        covered = _primary_ownership_rows_for_path(
            "src/accounting_information_platform/iso20022/parser.py",
            rows,
        )
        undeclared = _primary_ownership_rows_for_path(
            "src/accounting_information_platform/new_context/parser.py",
            rows,
        )
        self.assertEqual(rows, covered)
        self.assertEqual([], undeclared)

    def test_no_new_generic_domain_bucket_is_created(self) -> None:
        """Keep existing core.py debt from becoming precedent for more generic buckets."""
        violations: list[str] = []
        for path in PACKAGE.rglob("*"):
            if path in TRANSITIONAL_GENERIC_PATHS:
                continue
            candidate = path.stem if path.is_file() else path.name
            if candidate in GENERIC_BUCKET_NAMES:
                violations.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(
            [],
            violations,
            msg=(
                "new generic accounting buckets hide bounded-context ownership; "
                "name the owning context/responsibility instead"
            ),
        )

    def test_existing_core_bucket_is_explicitly_debt_not_shared_kernel(self) -> None:
        """Ensure the legacy core.py exception cannot silently become a Shared Kernel."""
        text = CONTEXT_MAP.read_text(encoding="utf-8")
        self.assertTrue((PACKAGE / "core.py").is_file())
        self.assertIn("`src/accounting_information_platform/core.py`", text)
        self.assertIn("Existing `core.py` is explicit debt, not precedent", text)
        self.assertIn("Python package root is a deployment container, not a DDD Shared Kernel", text)

    def test_domain_source_does_not_import_foreign_application_repositories(self) -> None:
        """Fail closed on every undeclared absolute import, including newly named sibling apps."""
        violations: dict[str, list[str]] = {}
        for path in sorted(PACKAGE.rglob("*.py")):
            forbidden = sorted(_forbidden_external_import_roots(_import_roots(path)))
            if forbidden:
                violations[path.relative_to(ROOT).as_posix()] = forbidden
        self.assertEqual(
            {},
            violations,
            msg=(
                "accounting domain/application code may import only stdlib, its own package, "
                "or an explicitly reviewed third-party library root; foreign applications "
                "must cross released contracts and ACLs"
            ),
        )

    def test_foreign_import_gate_does_not_depend_on_known_sibling_names(self) -> None:
        """Reject a newly named foreign application without updating a sibling-name denylist."""
        forbidden = _forbidden_external_import_roots(
            {"json", LOCAL_APPLICATION_IMPORT_ROOT, "future_commercial_service"}
        )
        self.assertEqual({"future_commercial_service"}, forbidden)

    def test_ubiquitous_language_covers_authority_sensitive_terms(self) -> None:
        """Keep proposal, posting, reconciliation, time and evidence terms unambiguous."""
        text = UBIQUITOUS_LANGUAGE.read_text(encoding="utf-8")
        for term in REQUIRED_UBIQUITOUS_TERMS:
            self.assertIn(f"**{term}**", text)
        self.assertIn("`journal proposal` is not `general journal`", text)
        self.assertIn("`approved reconciliation` is not `approved journal posting`", text)
        self.assertIn("`statement entry` is not `journal line`", text)


if __name__ == "__main__":
    unittest.main()
