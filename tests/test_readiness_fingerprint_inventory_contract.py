"""Repository contracts for exact readiness definition-fingerprint inventories."""

from __future__ import annotations

import unittest

from accounting_information_platform import persistence as persistence_module


class ReadinessFingerprintInventoryContractTests(unittest.TestCase):
    """Keep every protected constraint and index paired with canonical semantics."""

    def test_constraint_fingerprints_cover_exact_readiness_inventory(self) -> None:
        """Every required constraint has one canonical type/definition fingerprint."""
        self.assertEqual(
            set(persistence_module._READINESS_CONSTRAINTS),
            set(persistence_module._READINESS_CONSTRAINT_FINGERPRINTS),
        )

    def test_index_fingerprints_cover_exact_readiness_inventory(self) -> None:
        """Every required explicit index has one canonical definition fingerprint."""
        self.assertEqual(
            set(persistence_module._READINESS_INDEXES),
            set(persistence_module._READINESS_INDEX_FINGERPRINTS),
        )


if __name__ == "__main__":
    unittest.main()
