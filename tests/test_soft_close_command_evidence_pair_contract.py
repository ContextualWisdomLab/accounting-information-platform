"""Static contracts for the soft-close authority/evidence commit pair."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0037_soft_close_command_evidence_pair.sql"
INSTALLER = ROOT / "src/accounting_information_platform/migration_install.py"


def test_soft_close_pair_migration_is_deferred_fail_closed_and_upgrade_safe() -> None:
    """Soft-close state must commit with complete evidence without fabricating legacy facts."""
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "soft_close_command_evidence_pair_legacy_preflight" in sql
    assert "soft_close_command_evidence_pair_required" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert "AFTER UPDATE OF period_status_code" in sql
    assert "period_control.period_status_code = 'soft_closed'" in sql
    assert "period_control.soft_close_idempotency_key IS NOT NULL" in sql
    assert "period_control.soft_close_source_payload_hash IS NOT NULL" in sql
    assert "period_control.soft_close_source_journal_count IS NOT NULL" in sql
    assert "FOR SELECT\n    TO current_user\n    USING (true)" in sql
    assert "DROP POLICY soft_close_evidence_pair_upgrade_visibility" in sql
    assert (
        "REVOKE ALL ON FUNCTION accounting_core.require_soft_close_command_evidence_pair()\n"
        "    FROM PUBLIC;"
    ) in sql


def test_canonical_installer_places_soft_close_pair_after_hard_close_pair() -> None:
    """Supported installs may not stop before the soft-close authority/evidence guard."""
    module = ast.parse(INSTALLER.read_text(encoding="utf-8"))
    migration_names: tuple[str, ...] | None = None

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
            assert isinstance(element, ast.BinOp) and isinstance(element.op, ast.Div)
            assert isinstance(element.right, ast.Constant)
            assert isinstance(element.right.value, str)
            names.append(element.right.value)
        migration_names = tuple(names)
        break

    assert migration_names is not None
    hard_close_index = migration_names.index("0036_hard_close_trial_balance_snapshot_pair.sql")
    soft_close_index = migration_names.index("0037_soft_close_command_evidence_pair.sql")
    assert soft_close_index == hard_close_index + 1
