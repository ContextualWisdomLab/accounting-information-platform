"""Architectural fitness tests for accounting bounded-context ownership."""

from __future__ import annotations

import ast
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
FORBIDDEN_APPLICATION_IMPORT_ROOTS = {
    "metering_billing_platform",
    "contextual_orchestrator",
    "naruon",
    "keyverse",
    "context_graph_contracts",
    "enterprise_architecture_core",
}
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

    def test_accepted_adr_owns_the_context_map_decision(self) -> None:
        """Keep the Context Map tied to a reviewable accepted architecture decision."""
        text = CONTEXT_MAP_ADR.read_text(encoding="utf-8")
        self.assertIn("Status: Accepted", text)
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

    def test_every_top_level_production_module_has_explicit_physical_owner(self) -> None:
        """Require new modules to be assigned to a context in the code-current map."""
        text = CONTEXT_MAP.read_text(encoding="utf-8")
        production_modules = sorted(
            path
            for path in PACKAGE.glob("*.py")
            if path.name != "__init__.py"
        )
        for path in production_modules:
            relative = path.relative_to(ROOT).as_posix()
            self.assertIn(
                f"`{relative}`",
                text,
                msg=f"{relative} needs an explicit primary/current owner in docs/CONTEXT_MAP.md",
            )
        self.assertIn("`src/accounting_information_platform/iso20022/`", text)

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
        """Force cross-repository coupling through published contracts and ACLs."""
        violations: dict[str, list[str]] = {}
        for path in sorted(PACKAGE.rglob("*.py")):
            roots = _import_roots(path)
            forbidden = sorted(roots & FORBIDDEN_APPLICATION_IMPORT_ROOTS)
            if forbidden:
                violations[path.relative_to(ROOT).as_posix()] = forbidden
        self.assertEqual(
            {},
            violations,
            msg="accounting domain/application code must not import foreign application repositories",
        )

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
