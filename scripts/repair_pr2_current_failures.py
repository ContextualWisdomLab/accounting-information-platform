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


def _document_current_reversal_contract() -> None:
    """Keep current exact-replay prose and add the temporal ordering invariant."""
    adr_path = Path("docs/adr/0003-append-only-journals.md")
    adr = adr_path.read_text(encoding="utf-8")
    temporal_sentence = (
        " A reversal accounting date must also be on or after the original journal "
        "accounting date, so a reversal cannot appear in a trial balance before the "
        "fact it corrects."
    )
    if temporal_sentence.strip() not in adr:
        anchor = (
            "A reversal replay is valid only when the tenant, reversal command "
            "idempotency key, original journal reference, and immutable "
            "reversal-command payload hash all match the stored reversal command; "
            "any mismatch fails closed."
        )
        if anchor not in adr:
            raise SystemExit("current reversal ADR anchor drifted")
        adr = adr.replace(anchor, anchor + temporal_sentence, 1)
        adr_path.write_text(adr, encoding="utf-8")

    changelog_path = Path("CHANGELOG.md")
    changelog = changelog_path.read_text(encoding="utf-8")
    entry = (
        "- Made HomeTax rejection commands and journal reversals exactly idempotent: "
        "tenant-scoped command keys replay only identical command evidence, while "
        "changed evidence fails closed; reversals also cannot predate the original "
        "accounting date.\n"
    )
    if entry not in changelog:
        marker = "### Changed\n"
        if marker not in changelog:
            raise SystemExit("CHANGELOG changed-section anchor drifted")
        changelog_path.write_text(
            changelog.replace(marker, marker + "\n" + entry, 1),
            encoding="utf-8",
        )


def main() -> None:
    """Repair current HomeTax, reversal, and database-regression failures."""
    command = _load("repair_pr2_command_idempotency")
    command.update_home_tax_contract()
    command.update_reversal_contract()
    command.update_tests()

    aggregate = _load("repair_pr2_all")
    aggregate.fix_reversal_key_after_reference_resolution()

    temporal = _load("repair_pr2_reversal_temporal_order")
    temporal.harden_reference_oracle()
    temporal.harden_postgres_reversal()
    temporal.add_regressions()

    followup = _load("repair_pr2_followup_adjustments")
    followup.harden_home_tax_projection()
    followup.normalize_trigger_and_test_cleanup()

    _load("repair_pr2_home_tax_replay_outcome").main()
    aggregate.normalize_postgres_regression_setup()
    _document_current_reversal_contract()


if __name__ == "__main__":
    main()
