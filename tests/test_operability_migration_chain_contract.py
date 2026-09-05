"""Contract tying operator migration instructions to the canonical installer."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "src/accounting_information_platform/migration_install.py"
OPERABILITY = ROOT / "docs/OPERABILITY.md"


def _forward_migration_names() -> tuple[str, ...]:
    """Read the literal forward-migration tuple without importing database code."""
    module = ast.parse(INSTALLER.read_text(encoding="utf-8"))
    for node in ast.walk(module):
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "forward_migration_paths"
            for target in node.targets
        ):
            continue
        if not isinstance(node.value, ast.Tuple):
            continue

        names: list[str] = []
        for element in node.value.elts:
            if not isinstance(element, ast.BinOp) or not isinstance(element.op, ast.Div):
                raise AssertionError("forward_migration_paths must remain literal Path divisions")
            if not isinstance(element.right, ast.Constant) or not isinstance(
                element.right.value, str
            ):
                raise AssertionError("forward migration filename must remain a string literal")
            names.append(element.right.value)
        return tuple(names)

    raise AssertionError("canonical forward_migration_paths tuple is missing")


def test_operability_lists_every_canonical_forward_migration_in_order() -> None:
    """Operator instructions may not stop before the installer authority chain."""
    text = OPERABILITY.read_text(encoding="utf-8")
    cursor = -1

    for migration_name in _forward_migration_names():
        next_cursor = text.find(f"database/migrations/{migration_name}", cursor + 1)
        assert next_cursor > cursor, (
            f"docs/OPERABILITY.md must list {migration_name} after the previous "
            "canonical forward migration"
        )
        cursor = next_cursor

    assert "hard_close_snapshot_pair_legacy_preflight" in text
    assert "trial_balance_snapshot_hard_close_pair_legacy_preflight" in text
