"""Review RED for the exact related-account currency correction contract."""

from __future__ import annotations

import unittest

from accounting_information_platform import (
    AccountingValidationError,
    MemoryArtifactStore,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
)
from tests import test_postgres_posting as posting
from tests.test_postgres_bank_statement_debtor_creditor_account_currency_evidence_red import (
    BankStatementDebtorCreditorAccountCurrencyEvidenceRedTests as AccountCurrencyHelpers,
)


class BankStatementDebtorCreditorAccountCurrencyCorrectionContractReviewRedTests(unittest.TestCase):
    """Reject related-account currency replay through the complete correction contract."""

    _with_role_account = AccountCurrencyHelpers._with_role_account
    _register_statement_account = AccountCurrencyHelpers._register_statement_account
    _command = AccountCurrencyHelpers._command
    _currency_account = AccountCurrencyHelpers._currency_account

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one nested PostgreSQL fixture and the pinned CAMT.053 statement."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.same_scalar_identifier = "DE89370400440532013000"

    def test_material_account_currency_change_requires_complete_correction_contract(self) -> None:
        """Pin the complete fail-closed remediation message for both related-account roles."""
        expected = (
            r"^statement identity already exists with different entry evidence\. "
            r"Use an explicit correction contract, then retry ingest\.$"
        )
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                baseline = self._with_role_account(role, self._currency_account("BHD"))
                changed = self._with_role_account(role, self._currency_account("KWD"))
                reference = self._register_statement_account(baseline)
                store = MemoryArtifactStore()
                accept_bank_statement_evidence(
                    self._command(baseline, reference, f"{role}-account-currency-contract-baseline"),
                    posting.DATABASE_URL,
                    self.case.policy.tenant_reference,
                    artifact_store=store,
                )
                with self.assertRaisesRegex(AccountingValidationError, expected):
                    accept_bank_statement_evidence(
                        self._command(changed, reference, f"{role}-account-currency-contract-changed"),
                        posting.DATABASE_URL,
                        self.case.policy.tenant_reference,
                        artifact_store=store,
                    )


if __name__ == "__main__":
    unittest.main()
