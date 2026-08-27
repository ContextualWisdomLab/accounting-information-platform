"""Public installation boundary for the accounting foundation migration chain."""

from __future__ import annotations

from .persistence import apply_foundation_migration

__all__ = ["apply_foundation_migration"]