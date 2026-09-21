"""Forced-RLS regression for rejected repeated line-adjustment reordering."""

from __future__ import annotations

import unittest

import psycopg

from accounting_information_platform import (
    AccountingValidationError,
    accept_bank_statement_evidence,
)
from tests import test_postgres_posting as posting
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_adjustment_order_evidence_red
    as adjustment_order_contract,
)

_PARENT_TEST = (
    adjustment_order_contract.BankStatementStructuredLineAdjustmentOrderEvidenceRedTests
)
_CORRECTION_ERROR = adjustment_order_contract._CORRECTION_ERROR


class BankStatementStructuredLineAdjustmentOrderRlsNonMutationRedTests(
    unittest.TestCase
):
    """Prove conflict atomicity through the same forced-RLS tenant boundary."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL structured-remittance fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Prepare the adjustment-order fixture and fresh tenant evidence state."""
        self.parent = _PARENT_TEST(
            "test_rejected_adjustment_order_preserves_complete_tenant_statement_state"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)

    def test_rejected_adjustment_order_preserves_rls_scoped_statement_state(self) -> None:
        """Compare all tenant statement rows under installed RLS context."""
        owner = self.parent.parent.credit_debit_contract
        accepted = accept_bank_statement_evidence(
            owner._command(
                self.parent.base_payload,
                "adjustment-order-rls-nonmutation-base",
            ),
            posting.DATABASE_URL,
            owner.case.policy.tenant_reference,
            artifact_store=owner.store,
        )
        self.assertFalse(accepted["replayed"])
        record_id = str(accepted["bank_statement_record_id"])

        before_rows = self._tenant_statement_rows()
        expected_artifacts = {
            self.parent.base_statement.source_artifact_hash: self.parent.base_payload,
        }
        self.assertEqual(owner.store._artifacts, expected_artifacts)
        self.assertTrue(before_rows["bank_statement_artifact"])
        self.assertTrue(before_rows["bank_statement_entry"])
        self.assertTrue(before_rows["bank_statement_entry_detail"])
        self.assertTrue(
            any(
                str(row[0]) == record_id
                for row in before_rows["bank_statement_record"]
            )
        )

        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                owner._command(
                    self.parent.changed_payload,
                    "adjustment-order-rls-nonmutation-changed",
                ),
                posting.DATABASE_URL,
                owner.case.policy.tenant_reference,
                artifact_store=owner.store,
            )

        self.assertEqual(self._tenant_statement_rows(), before_rows)
        self.assertEqual(owner.store._artifacts, expected_artifacts)

    def _tenant_statement_rows(self) -> dict[str, tuple[tuple[object, ...], ...]]:
        """Snapshot every evidence column after installing the tenant RLS context."""
        owner = self.parent.parent.credit_debit_contract
        tenant_reference = owner.case.policy.tenant_reference
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_row = connection.execute(
                """
                SELECT tenant_account_id
                FROM accounting_core.tenant_account
                WHERE tenant_account_code = %s
                """,
                (tenant_reference,),
            ).fetchone()
            if tenant_row is None:
                raise AssertionError("test tenant must exist before evidence snapshot")
            tenant_id = tenant_row[0]
            connection.execute(
                "SELECT set_config('app.tenant_account_id', %s, false)",
                (str(tenant_id),),
            )

            artifact_rows = connection.execute(
                """
                SELECT *
                FROM accounting_integration.bank_statement_artifact
                WHERE tenant_account_id = %s
                ORDER BY bank_statement_artifact_id
                """,
                (tenant_id,),
            ).fetchall()
            statement_rows = connection.execute(
                """
                SELECT *
                FROM accounting_integration.bank_statement_record
                WHERE tenant_account_id = %s
                ORDER BY bank_statement_record_id
                """,
                (tenant_id,),
            ).fetchall()
            entry_rows = connection.execute(
                """
                SELECT *
                FROM accounting_integration.bank_statement_entry
                WHERE tenant_account_id = %s
                ORDER BY bank_statement_entry_id
                """,
                (tenant_id,),
            ).fetchall()
            detail_rows = connection.execute(
                """
                SELECT *
                FROM accounting_integration.bank_statement_entry_detail
                WHERE tenant_account_id = %s
                ORDER BY bank_statement_entry_detail_id
                """,
                (tenant_id,),
            ).fetchall()

        return {
            "bank_statement_artifact": tuple(tuple(row) for row in artifact_rows),
            "bank_statement_record": tuple(tuple(row) for row in statement_rows),
            "bank_statement_entry": tuple(tuple(row) for row in entry_rows),
            "bank_statement_entry_detail": tuple(tuple(row) for row in detail_rows),
        }


if __name__ == "__main__":
    unittest.main()
