"""Normalize the hard-close imbalance regression after database balance enforcement."""

from __future__ import annotations

from pathlib import Path
import re


def main() -> None:
    """Exercise binder imbalance without first committing an invalid journal."""
    path = Path("tests/test_postgres_posting.py")
    text = path.read_text(encoding="utf-8")
    if "Close rejects a corrupted in-memory binder without persisting an invalid journal." in text:
        return
    pattern = re.compile(
        r"(?ms)^    def test_hard_close_rejects_unbalanced_trial_balance\(self\) -> None:\n"
        r".*?(?=^    def test_post_proposal_resolves_catalog_policy_from_billing_ingest\()"
    )
    replacement = '''    def test_hard_close_rejects_unbalanced_trial_balance(self) -> None:
        """Close rejects a corrupted in-memory binder without persisting an invalid journal."""
        self.ledger.post(self._two_line_proposal(), self.policy)
        package = self.ledger._assemble_period_close_package(
            self.policy.legal_entity_reference,
            self.policy.accounting_book_reference,
            "2026-08",
        )
        trial_balance = dict(package["trial_balance"])
        lines = [dict(line) for line in trial_balance["lines"]]
        self.assertTrue(lines)
        lines[0]["debit_amount"] = str(
            Decimal(str(lines[0]["debit_amount"])) + Decimal("1")
        )
        trial_balance["lines"] = lines
        unbalanced_package = dict(package)
        unbalanced_package["trial_balance"] = trial_balance

        with mock.patch.object(
            self.ledger,
            "_assemble_period_close_package",
            return_value=unbalanced_package,
        ):
            with self.assertRaisesRegex(AccountingValidationError, "does not balance"):
                self._close_period(idempotency_key="period-close-unbalanced")

        self.assertEqual(self._period_status("2026-08"), "open")
        self.assertEqual(self._count_closing_journals(), 0)
        self.assertEqual(
            self._count_table("accounting_reporting.trial_balance_snapshot"), 0
        )

'''
    updated, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise SystemExit("hard-close imbalance regression anchor drifted")
    path.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
