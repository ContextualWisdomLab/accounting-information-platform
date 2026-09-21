"""Regression for complete relational non-mutation on rejected discount reordering."""

from __future__ import annotations

import unittest

import psycopg

from accounting_information_platform import (
    AccountingValidationError,
    accept_bank_statement_evidence,
)
from tests import test_postgres_posting as posting
from tests import (
    test_postgres_bank_statement_structured_referred_document_line_discount_order_evidence_red
    as discount_order_contract,
)

_PARENT_TEST = (
    discount_order_contract.BankStatementStructuredLineDiscountOrderEvidenceRedTests
)
_CORRECTION_ERROR = discount_order_contract._CORRECTION_ERROR


class BankStatementStructuredLineDiscountOrderNonMutationRedTests(unittest.TestCase):
    """Reject changed source order without leaving any tenant-scoped evidence delta."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL bank-statement fixture."""
        _PARENT_TEST.setUpClass()

    def setUp(self) -> None:
        """Prepare the repaired repeated-discount order fixture and object store."""
        self.parent = _PARENT_TEST(
            "test_changed_discount_order_fails_closed_without_mutating_accepted_evidence"
        )
        self.parent.setUp()
        self.addCleanup(self.parent.doCleanups)

    def test_rejected_discount_order_preserves_complete_tenant_statement_state(
        self,
    ) -> None:
        """Compare every persisted statement-evidence column before and after rejection."""
        accepted = accept_bank_statement_evidence(
            self.parent.parent._command(
                self.parent.base_payload,
                "repeated-line-discount-order-relational-base",
            ),
            posting.DATABASE_URL,
            self.parent.parent.case.policy.tenant_reference,
            artifact_store=self.parent.parent.store,
        )
        self.assertFalse(accepted["replayed"])
        record_id = str(accepted["bank_statement_record_id"])

        before_rows = self._tenant_statement_rows()
        before_artifacts = dict(self.parent.parent.store._artifacts)
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
                self.parent.parent._command(
                    self.parent.changed_payload,
                    "repeated-line-discount-order-relational-changed",
                ),
                posting.DATABASE_URL,
                self.parent.parent.case.policy.tenant_reference,
                artifact_store=self.parent.parent.store,
            )

        self.assertEqual(self._tenant_statement_rows(), before_rows)
        self.assertEqual(self.parent.parent.store._artifacts, before_artifacts)

    def _tenant_statement_rows(self) -> dict[str, tuple[tuple[object, ...], ...]]:
        """Snapshot all columns under the tenant's forced-RLS evidence view."""
        tenant_reference = self.parent.parent.case.policy.tenant_reference
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
