"""Apply clearing-system choice evidence controls to the standard instructing agent."""

from __future__ import annotations

from tests import (
    test_postgres_bank_statement_detail_payment_agent_clearing_system_choice_evidence_red as clearing,
)


class BankStatementDetailInstructingAgentClearingSystemChoiceEvidenceRedTests(
    clearing.BankStatementDetailPaymentAgentClearingSystemChoiceEvidenceRedTests
):
    """Run the complete ClrSysMmbId contract on TransactionAgents6/InstgAgt."""

    def setUp(self) -> None:
        """Reuse the reviewed contract with the instructing-agent role only."""
        super().setUp()
        self.case.ROLES = {
            "InstgAgt": ("instructing_agent_evidence_hash", "DEUTDEFF"),
        }
