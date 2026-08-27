"""Public installation boundary for the accounting foundation migration chain."""

from __future__ import annotations

from pathlib import Path

from .persistence import apply_foundation_migration as _apply_foundation_migration


def apply_foundation_migration(database_url: str, migration_path: Path) -> None:
    """Apply the complete checked-in foundation chain through the canonical loader."""
    _apply_foundation_migration(database_url, migration_path)


__all__ = ["apply_foundation_migration"]
