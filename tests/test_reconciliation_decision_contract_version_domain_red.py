"""RED contract for the reconciliation decision contract-version runtime domain."""

from __future__ import annotations

import unittest
from decimal import Decimal

from accounting_information_platform.reconciliation import ReconciliationDecision


class _UnhashableStr(str):
    __hash__ = None


class ReconciliationDecisionContractVersionDomainRedTests(unittest.TestCase):
    """Require malformed version evidence to fail through the reconciliation boundary."""

    @staticmethod
    def _decision(*, contract_version: object) -> ReconciliationDecision:
        return ReconciliationDecision(
            statement_entry_reference="statement-contract-version-1",
            decision_code="match",
            rule_code="provider_reference",
            matched_journal_references=("journal-contract-version-1",),
            allocated_amount=Decimal("1000.00"),
            exception_code=None,
            next_action="Review the deterministic proposal; do not post a journal.",
            contract_version=contract_version,  # type: ignore[arg-type]
        )

    def test_unhashable_contract_version_values_fail_closed_with_domain_error(self) -> None:
        """Lists and mappings must not leak Python set-membership TypeError."""
        for contract_version in ([], {}):
            with self.subTest(contract_version=contract_version):
                with self.assertRaisesRegex(ValueError, "contract_version"):
                    self._decision(contract_version=contract_version)

    def test_unhashable_str_subclass_cannot_reach_version_membership(self) -> None:
        """Only exact built-in strings participate in the repository-owned version set."""
        with self.assertRaisesRegex(ValueError, "contract_version"):
            self._decision(contract_version=_UnhashableStr("reconciliation-decision/v1"))

    def test_supported_exact_string_versions_remain_valid(self) -> None:
        """The runtime guard preserves both repository-supported decision contracts."""
        for contract_version in ("reconciliation-decision/v1", "reconciliation-decision/v2"):
            with self.subTest(contract_version=contract_version):
                decision = self._decision(contract_version=contract_version)
                self.assertEqual(decision.contract_version, contract_version)

    def test_unknown_exact_string_version_still_fails_closed(self) -> None:
        """An exact string outside the closed version set remains unsupported."""
        with self.assertRaisesRegex(ValueError, "contract_version"):
            self._decision(contract_version="reconciliation-decision/v99")


if __name__ == "__main__":
    unittest.main()
