"""RED contract for chart-account Entity identity in retained snapshot provenance."""

from __future__ import annotations

import unittest
from decimal import Decimal
from uuid import UUID

from accounting_information_platform.persistence import _canonical_snapshot_hash


class TrialBalanceSnapshotHashChartAccountIdentityTests(unittest.TestCase):
    """Bind retained trial-balance provenance to the exact chart-account Entity."""

    def test_snapshot_hash_changes_when_only_chart_account_id_changes(self) -> None:
        """Code equality must not make two different account Entities hash-identical."""
        first_account_id = UUID("00000000-0000-7000-8000-000000000101")
        successor_account_id = UUID("00000000-0000-7000-8000-000000000102")
        common = {
            "tenant_reference": "urn:cwl:tenant_snapshot_hash_identity",
            "legal_entity_reference": "urn:cwl:legal_entity:snapshot_hash_identity",
            "accounting_book_reference": "urn:cwl:accounting_book:snapshot_hash_identity",
            "period_code": "2026-08",
            "snapshot_currency_code": "KRW",
            "source_journal_count": 1,
        }

        original_hash = _canonical_snapshot_hash(
            **common,
            lines=((first_account_id, "410100", Decimal("0"), Decimal("25000")),),
        )
        successor_hash = _canonical_snapshot_hash(
            **common,
            lines=((successor_account_id, "410100", Decimal("0"), Decimal("25000")),),
        )
        replay_hash = _canonical_snapshot_hash(
            **common,
            lines=((first_account_id, "410100", Decimal("0"), Decimal("25000")),),
        )

        self.assertEqual(original_hash, replay_hash)
        self.assertNotEqual(original_hash, successor_hash)


if __name__ == "__main__":
    unittest.main()
