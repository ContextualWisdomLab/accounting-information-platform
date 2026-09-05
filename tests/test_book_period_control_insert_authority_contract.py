"""Static contract for canonical book-period authority materialization."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database" / "migrations" / "0034_book_period_control_seed.sql"


def test_direct_book_period_control_insert_is_not_an_authority_writer() -> None:
    """Require direct control inserts to fail explicitly instead of reporting silent success."""
    migration = MIGRATION.read_text(encoding="utf-8")
    guard_start = migration.index(
        "CREATE OR REPLACE FUNCTION accounting_core.guard_book_period_control_insert_authority"
    )
    guard_end = migration.index(
        "REVOKE ALL ON FUNCTION accounting_core.guard_book_period_control_insert_authority",
        guard_start,
    )
    guard = migration[guard_start:guard_end]

    assert "pg_trigger_depth() < 2" in guard
    assert "NEW.period_status_code IS DISTINCT FROM 'open'" in guard
    assert "NEW.period_closed_at IS NOT NULL" in guard
    assert "RAISE EXCEPTION" in guard
    assert "book_period_control_insert_authority_required" in guard
    assert "USING ERRCODE = 'check_violation';" in guard
    assert "RETURN NULL;" not in guard
    assert "CREATE TRIGGER book_period_control_insert_authority_guard" in migration
    assert "BEFORE INSERT" in migration


def test_authority_guard_is_installed_after_migration_repair() -> None:
    """Keep the one-time owner repair outside the post-install runtime insert guard."""
    migration = MIGRATION.read_text(encoding="utf-8")
    repair_position = migration.index(
        "INSERT INTO accounting_core.accounting_book_period_control (",
        migration.index("-- Repair databases"),
    )
    guard_position = migration.index(
        "CREATE OR REPLACE FUNCTION accounting_core.guard_book_period_control_insert_authority"
    )

    assert repair_position < guard_position
