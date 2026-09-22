"""RED contract for exact built-in optional reconciliation identities."""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from typing import Callable

from accounting_information_platform.reconciliation import BookJournalEvidence
from accounting_information_platform.reconciliation import StatementEntryEvidence


class _ExplodingStripStr(str):
    def strip(self, *args: object, **kwargs: object) -> str:
        """Prove admission rejects the subclass before caller whitespace logic runs."""
        raise RuntimeError("caller-controlled strip must not execute")


class _ExplodingEqualityStr(str):
    def __eq__(self, other: object) -> bool:
        """Prove admission rejects the subclass before caller equality can match."""
        raise RuntimeError("caller-controlled equality must not enter reconciliation")

    __hash__ = str.__hash__


class ReconciliationOptionalIdentityRuntimeDomainRedTests(unittest.TestCase):
    """Keep optional strong references inside the repository-owned string domain."""

    @staticmethod
    def _statement(**overrides: object) -> StatementEntryEvidence:
        """Build valid statement evidence while varying one optional identity."""
        values: dict[str, object] = {
            "statement_entry_reference": "statement-optional-runtime-1",
            "provider_reference": None,
            "end_to_end_reference": None,
            "account_servicer_reference": None,
            "amount": Decimal("1000.00"),
            "currency_code": "KRW",
            "credit_debit_code": "CRDT",
            "booking_date": date(2026, 9, 22),
            "value_date": date(2026, 9, 22),
        }
        values.update(overrides)
        return StatementEntryEvidence(**values)  # type: ignore[arg-type]

    @staticmethod
    def _journal(**overrides: object) -> BookJournalEvidence:
        """Build valid journal evidence while varying one optional identity."""
        values: dict[str, object] = {
            "journal_reference": "journal-optional-runtime-1",
            "provider_reference": None,
            "end_to_end_reference": None,
            "account_servicer_reference": None,
            "amount": Decimal("1000.00"),
            "currency_code": "KRW",
            "credit_debit_code": "CRDT",
            "accounting_date": date(2026, 9, 22),
        }
        values.update(overrides)
        return BookJournalEvidence(**values)  # type: ignore[arg-type]

    @staticmethod
    def _cases() -> tuple[tuple[Callable[..., object], str], ...]:
        """Return every statement/book optional strong-reference admission point."""
        fields = (
            "provider_reference",
            "end_to_end_reference",
            "account_servicer_reference",
        )
        return tuple(
            (factory, field_name)
            for factory in (
                ReconciliationOptionalIdentityRuntimeDomainRedTests._statement,
                ReconciliationOptionalIdentityRuntimeDomainRedTests._journal,
            )
            for field_name in fields
        )

    def test_optional_identity_rejects_subclass_before_custom_strip_executes(self) -> None:
        """Durable evidence admission must not call a subclass-defined strip method."""
        for factory, field_name in self._cases():
            with self.subTest(factory=factory.__name__, field_name=field_name):
                with self.assertRaisesRegex(ValueError, field_name):
                    factory(**{field_name: _ExplodingStripStr("source-runtime-1")})

    def test_optional_identity_rejects_subclass_before_custom_equality_can_enter_matching(self) -> None:
        """A string subclass must fail before its equality semantics can become match evidence."""
        for factory, field_name in self._cases():
            with self.subTest(factory=factory.__name__, field_name=field_name):
                with self.assertRaisesRegex(ValueError, field_name):
                    factory(**{field_name: _ExplodingEqualityStr("source-runtime-2")})

    def test_none_and_exact_builtin_optional_identities_remain_valid(self) -> None:
        """Absence and exact built-in non-blank strings preserve the supported contract."""
        for factory, field_name in self._cases():
            with self.subTest(factory=factory.__name__, field_name=field_name, value=None):
                evidence = factory(**{field_name: None})
                self.assertIsNone(getattr(evidence, field_name))
            with self.subTest(
                factory=factory.__name__, field_name=field_name, value="builtin"
            ):
                evidence = factory(**{field_name: "source-runtime-3"})
                self.assertEqual(getattr(evidence, field_name), "source-runtime-3")


if __name__ == "__main__":
    unittest.main()
