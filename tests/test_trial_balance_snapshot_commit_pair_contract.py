"""Static contract for retained-snapshot and hard-close commit pairing."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_TO_CLOSE_MIGRATION = (
    ROOT / "database/migrations/0035_trial_balance_snapshot_hard_close_pair.sql"
)
CLOSE_TO_SNAPSHOT_MIGRATION = (
    ROOT / "database/migrations/0036_hard_close_trial_balance_snapshot_pair.sql"
)
INSTALLER_PATH = ROOT / "src/accounting_information_platform/migration_install.py"


def test_snapshot_pair_guard_is_deferred_and_fail_closed() -> None:
    """Snapshot admission may be temporary soft-close state, but commit may not be."""
    sql = SNAPSHOT_TO_CLOSE_MIGRATION.read_text(encoding="utf-8")

    assert "CREATE CONSTRAINT TRIGGER trial_balance_snapshot_hard_close_pair_guard" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert "AFTER INSERT" in sql
    assert "period_status_value IS DISTINCT FROM 'hard_closed'" in sql
    assert "trial_balance_snapshot_hard_close_pair_required" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, pg_temp" in sql
    assert (
        "REVOKE ALL ON FUNCTION accounting_reporting.require_trial_balance_snapshot_hard_close_pair()"
        in sql
    )


def test_hard_close_pair_guard_is_deferred_and_fail_closed() -> None:
    """Hard-close authority may not commit unless its retained snapshot exists."""
    sql = CLOSE_TO_SNAPSHOT_MIGRATION.read_text(encoding="utf-8")

    assert "CREATE CONSTRAINT TRIGGER hard_close_trial_balance_snapshot_pair_guard" in sql
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert "AFTER UPDATE OF period_status_code" in sql
    assert "NEW.period_status_code = 'hard_closed'" in sql
    assert "accounting_reporting.trial_balance_snapshot" in sql
    assert "hard_close_snapshot_pair_required" in sql
    assert "SECURITY DEFINER" in sql
    assert "SET search_path = pg_catalog, pg_temp" in sql
    assert (
        "REVOKE ALL ON FUNCTION accounting_reporting.require_hard_close_trial_balance_snapshot_pair()"
        in sql
    )


def test_canonical_installer_cannot_stop_before_bidirectional_pair_guards() -> None:
    """Every supported foundation install reaches both commit-pair migrations."""
    installer = INSTALLER_PATH.read_text(encoding="utf-8")

    assert '"0035_trial_balance_snapshot_hard_close_pair.sql"' in installer
    assert '"0036_hard_close_trial_balance_snapshot_pair.sql"' in installer
