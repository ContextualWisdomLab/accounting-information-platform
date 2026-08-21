"""One-shot normalization for exact-head reproducibility and reversal trigger precedence."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    """Return one repository UTF-8 file."""
    return (ROOT / path).read_text(encoding="utf-8")


def _write(path: str, content: str) -> None:
    """Replace one repository UTF-8 file."""
    (ROOT / path).write_text(content, encoding="utf-8")


def _replace(path: str, old: str, new: str, label: str) -> None:
    """Replace one exact block and fail closed if source drifted."""
    text = _read(path)
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        raise SystemExit(f"{path}: {label} anchor drifted")
    _write(path, text)


def update_ci_timestamp() -> None:
    """Make SOURCE_DATE_EPOCH fail closed and derive it from the verified exact head."""
    _replace(
        ".github/workflows/ci.yml",
        '''      - name: Pin reproducible build timestamp
        env:
          EXPECTED_SHA: ${{ github.event.pull_request.head.sha || github.sha }}
        shell: bash
        run: echo "SOURCE_DATE_EPOCH=$(git show -s --format=%ct \\\"$EXPECTED_SHA\\\")" >> "$GITHUB_ENV"
''',
        '''      - name: Pin reproducible build timestamp
        env:
          EXPECTED_SHA: ${{ github.event.pull_request.head.sha || github.sha }}
        shell: bash
        run: |
          set -euo pipefail
          source_date_epoch="$(git show -s --format=%ct "$EXPECTED_SHA")"
          test -n "$source_date_epoch"
          printf 'SOURCE_DATE_EPOCH=%s\\n' "$source_date_epoch" >> "$GITHUB_ENV"
''',
        "SOURCE_DATE_EPOCH",
    )


def update_ci_contract() -> None:
    """Pin a regression for the exact-head timestamp command and non-empty result."""
    path = "tests/test_ci_contract.py"
    text = _read(path)
    marker = '''    def test_ci_requires_reproducible_wheel_sbom_and_signed_attestations(self) -> None:
'''
    addition = '''    def test_ci_reproducible_timestamp_uses_exact_head_and_fails_closed(self) -> None:
        """The verified exact head supplies a mandatory non-empty SOURCE_DATE_EPOCH."""
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'source_date_epoch="$(git show -s --format=%ct "$EXPECTED_SHA")"',
            workflow,
        )
        self.assertIn('test -n "$source_date_epoch"', workflow)
        self.assertIn("printf 'SOURCE_DATE_EPOCH=%s\\\\n'", workflow)
        self.assertNotIn('\\\\\"$EXPECTED_SHA\\\\\"', workflow)

'''
    if addition not in text:
        if marker not in text:
            raise SystemExit(f"{path}: CI contract insertion anchor drifted")
        text = text.replace(marker, addition + marker, 1)
    _write(path, text)


def update_reversal_trigger_order() -> None:
    """Run temporal-order validation before finalization validation on INSERT."""
    _replace(
        "database/migrations/0005_closed_period_guard.sql",
        '''DROP TRIGGER IF EXISTS journal_reversal_temporal_guard
    ON accounting_core.journal_reversal;
CREATE TRIGGER journal_reversal_temporal_guard
    BEFORE INSERT ON accounting_core.journal_reversal
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_reversal_temporal_order();
''',
        '''DROP TRIGGER IF EXISTS journal_reversal_temporal_guard
    ON accounting_core.journal_reversal;
DROP TRIGGER IF EXISTS journal_reversal_first_temporal_guard
    ON accounting_core.journal_reversal;
CREATE TRIGGER journal_reversal_first_temporal_guard
    BEFORE INSERT ON accounting_core.journal_reversal
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_reversal_temporal_order();
''',
        "temporal trigger",
    )
    _replace(
        "database/migrations/0005_closed_period_guard.sql",
        '''DROP TRIGGER IF EXISTS journal_reversal_finalization_guard
    ON accounting_core.journal_reversal;
CREATE TRIGGER journal_reversal_finalization_guard
    BEFORE INSERT ON accounting_core.journal_reversal
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_reversal_lineage_insert();
''',
        '''DROP TRIGGER IF EXISTS journal_reversal_finalization_guard
    ON accounting_core.journal_reversal;
DROP TRIGGER IF EXISTS journal_reversal_second_finalization_guard
    ON accounting_core.journal_reversal;
CREATE TRIGGER journal_reversal_second_finalization_guard
    BEFORE INSERT ON accounting_core.journal_reversal
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_reversal_lineage_insert();
''',
        "finalization trigger",
    )


def update_changelog() -> None:
    """Record the exact-head reproducibility and deterministic guard fixes."""
    path = "CHANGELOG.md"
    text = _read(path)
    marker = "### Fixed\n\n"
    bullet = (
        "- Exact-head CI now derives `SOURCE_DATE_EPOCH` from the verified commit without literal escaped quotes and fails closed if the timestamp is empty. Reversal lineage INSERT guards are named so PostgreSQL evaluates temporal ordering before finalization state, preserving deterministic causal diagnostics while both database invariants remain enforced.\n"
    )
    if bullet not in text:
        if marker not in text:
            raise SystemExit(f"{path}: Fixed section anchor drifted")
        text = text.replace(marker, marker + bullet, 1)
    _write(path, text)


def main() -> None:
    """Apply all bounded normalization changes for the observed exact-head failures."""
    update_ci_timestamp()
    update_ci_contract()
    update_reversal_trigger_order()
    update_changelog()


if __name__ == "__main__":
    main()
