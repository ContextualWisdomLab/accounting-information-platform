"""Repair only the current PR 2 failures after database invariants landed."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load(name: str) -> ModuleType:
    """Load one sibling repair module from this script's directory."""
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"unable to load repair module {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    """Repair HomeTax idempotency and fixtures invalidated by DB-side balance checks."""
    command = _load("repair_pr2_command_idempotency")
    command.main()

    followup = _load("repair_pr2_followup_adjustments")
    followup.harden_home_tax_projection()
    followup.normalize_trigger_and_test_cleanup()

    aggregate = _load("repair_pr2_all")
    aggregate.normalize_postgres_regression_setup()


if __name__ == "__main__":
    main()
