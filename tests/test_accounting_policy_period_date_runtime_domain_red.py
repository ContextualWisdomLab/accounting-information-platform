"""Regression tests for exact fiscal-period date admission in AccountingPolicy."""

from __future__ import annotations

import unittest
from datetime import date, datetime

from accounting_information_platform import AccountingPolicy, AccountingValidationError


class _ExplodingDate(date):
    """Expose any comparison performed before repository-owned date admission."""

    def __gt__(self, other: object) -> bool:
        raise RuntimeError("caller-defined date comparison executed")


class AccountingPolicyPeriodDateRuntimeDomainTests(unittest.TestCase):
    """Require repository-owned calendar dates before open-period comparison."""

    @staticmethod
    def _policy(*, open_period_start: object, open_period_end: object) -> AccountingPolicy:
        return AccountingPolicy(
            tenant_reference="urn:cwl:tenant_001",
            legal_entity_reference="urn:cwl:legal_entity:entity_001",
            accounting_book_reference="urn:cwl:accounting_book:primary_statutory",
            intended_book_role_code="primary_statutory",
            transaction_currency="KRW",
            functional_currency="KRW",
            open_period_start=open_period_start,  # type: ignore[arg-type]
            open_period_end=open_period_end,  # type: ignore[arg-type]
            chart_account_mapping={"cash": "110100"},
            accounting_policy_version="ifrs-v1",
            posting_rule_version="billing-issued-v1",
        )

    def test_rejects_string_period_dates_even_when_lexically_ordered(self) -> None:
        with self.assertRaisesRegex(AccountingValidationError, "open_period_start must be an exact calendar date"):
            self._policy(open_period_start="2026-08-01", open_period_end="2026-08-31")

    def test_rejects_datetime_before_python_date_comparison(self) -> None:
        with self.assertRaisesRegex(AccountingValidationError, "open_period_start must be an exact calendar date"):
            self._policy(
                open_period_start=datetime(2026, 8, 1, 12, 0),
                open_period_end=date(2026, 8, 31),
            )

    def test_rejects_date_subclass_before_caller_comparison(self) -> None:
        with self.assertRaisesRegex(AccountingValidationError, "open_period_start must be an exact calendar date"):
            self._policy(
                open_period_start=_ExplodingDate(2026, 8, 1),
                open_period_end=date(2026, 8, 31),
            )

    def test_rejects_invalid_end_date_domain_before_period_ordering(self) -> None:
        with self.assertRaisesRegex(AccountingValidationError, "open_period_end must be an exact calendar date"):
            self._policy(
                open_period_start=date(2026, 8, 1),
                open_period_end="2026-08-31",
            )

    def test_accepts_exact_builtin_period_dates(self) -> None:
        policy = self._policy(
            open_period_start=date(2026, 8, 1),
            open_period_end=date(2026, 8, 31),
        )

        self.assertTrue(policy.permits(date(2026, 8, 20)))


if __name__ == "__main__":
    unittest.main()
