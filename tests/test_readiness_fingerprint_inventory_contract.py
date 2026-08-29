"""Repository contracts for exact readiness definition-fingerprint inventories."""

from __future__ import annotations

import unittest

from accounting_information_platform import persistence as persistence_module

_READINESS_INDEXES = tuple(
    f"{item[0]}.{item[1]}" for item in persistence_module._READINESS_INDEX_DEFINITIONS
)


class ReadinessFingerprintInventoryContractTests(unittest.TestCase):
    """Keep every protected constraint and index paired with canonical semantics."""

    def test_constraint_fingerprints_cover_exact_readiness_inventory(self) -> None:
        """Every required constraint has one canonical type/definition fingerprint."""
        self.assertEqual(
                len(persistence_module._READINESS_CONSTRAINTS),
                len({item[:3] for item in persistence_module._READINESS_CONSTRAINTS}),
        )
        self.assertTrue(
            all(
                len(item) == 5
                and item[3] in {"c", "f", "p", "u"}
                and len(item[4]) == 32
                for item in persistence_module._READINESS_CONSTRAINTS
            )
        )

    def test_index_fingerprints_cover_exact_readiness_inventory(self) -> None:
        """Every required explicit index has one canonical definition fingerprint."""
        self.assertEqual(
            set(_READINESS_INDEXES),
            {
                f"{item[0]}.{item[1]}"
                for item in persistence_module._READINESS_INDEX_DEFINITIONS
            },
        )
        self.assertEqual(
            len(persistence_module._READINESS_INDEX_DEFINITIONS),
            len(
                {
                    (item[0], item[1])
                    for item in persistence_module._READINESS_INDEX_DEFINITIONS
                }
            ),
        )
        self.assertTrue(
            all(
                len(item) == 7
                and isinstance(item[4], bool)
                and len(item[6]) == 32
                for item in persistence_module._READINESS_INDEX_DEFINITIONS
            )
        )


if __name__ == "__main__":
    unittest.main()
