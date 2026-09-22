"""RED contracts for reconciliation-allocation identity runtime admission.

Allocation evidence is durable control evidence. Its tenant, run, statement, journal,
and currency bindings must use repository-owned string semantics before duplicate
source checks or later persistence can execute caller-defined Python behavior.
"""

from __future__ import annotations

from decimal import Decimal
import unittest

from accounting_information_platform.allocation import ReconciliationAllocation


class _ExplodingIdentity(str):
    """Expose caller-controlled whitespace behavior if a string subclass is admitted."""

    def strip(self, chars: str | None = None) -> str:
        """Fail if allocation admission executes caller-owned string behavior."""
        raise RuntimeError("caller-controlled allocation identity strip must not execute")


class _UnhashableIdentity(str):
    """Model a string-shaped identity that cannot participate in source identity sets."""

    __hash__ = None


class ReconciliationAllocationIdentityRuntimeDomainRedTests(unittest.TestCase):
    """Require exact built-in strings for durable allocation identity fields."""

    @staticmethod
    def _allocation(**overrides: object) -> ReconciliationAllocation:
        """Construct otherwise-valid allocation evidence with selected field overrides."""
        values: dict[str, object] = {
            "tenant_account_reference": "tenant-001",
            "reconciliation_run_reference": "run-001",
            "statement_entry_reference": "statement-001",
            "journal_reference": "journal-001",
            "allocated_amount": Decimal("1000.00"),
            "currency_code": "KRW",
        }
        values.update(overrides)
        return ReconciliationAllocation(**values)  # type: ignore[arg-type]

    def test_string_subclasses_fail_before_caller_defined_behavior(self) -> None:
        """Every durable allocation identity rejects subclass-owned string semantics."""
        for field_name in (
            "tenant_account_reference",
            "reconciliation_run_reference",
            "statement_entry_reference",
            "journal_reference",
            "currency_code",
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValueError, field_name):
                    self._allocation(**{field_name: _ExplodingIdentity("valid-looking")})

    def test_unhashable_string_shape_is_not_admitted_as_source_identity(self) -> None:
        """A string-shaped but non-built-in source identity must fail at admission."""
        with self.assertRaisesRegex(ValueError, "statement_entry_reference"):
            self._allocation(statement_entry_reference=_UnhashableIdentity("statement-001"))

    def test_exact_builtin_identity_population_remains_valid(self) -> None:
        """Canonical built-in strings preserve the existing allocation contract."""
        allocation = self._allocation()
        self.assertEqual(allocation.tenant_account_reference, "tenant-001")
        self.assertEqual(allocation.reconciliation_run_reference, "run-001")
        self.assertEqual(allocation.statement_entry_reference, "statement-001")
        self.assertEqual(allocation.journal_reference, "journal-001")
        self.assertEqual(allocation.currency_code, "KRW")
        self.assertEqual(allocation.allocated_amount, Decimal("1000.00"))


if __name__ == "__main__":
    unittest.main()
