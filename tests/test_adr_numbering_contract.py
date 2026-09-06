"""Repository contract for unique accounting architecture-decision identifiers."""

from __future__ import annotations

import unittest
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADR_DIRECTORY = ROOT / "docs" / "adr"


class AdrNumberingContractTests(unittest.TestCase):
    """Keep each four-digit ADR identifier owned by exactly one decision record."""

    def test_adr_numeric_prefixes_are_unique(self) -> None:
        """Two decision records must never claim the same ADR number."""
        owners: dict[str, list[str]] = defaultdict(list)
        for path in sorted(ADR_DIRECTORY.glob("[0-9][0-9][0-9][0-9]-*.md")):
            owners[path.name[:4]].append(path.name)

        duplicates = {
            number: filenames
            for number, filenames in owners.items()
            if len(filenames) > 1
        }
        self.assertEqual(
            duplicates,
            {},
            "ADR numeric prefixes must be unique; renumber the newer conflicting "
            "decision and update its title/references before integration.",
        )


if __name__ == "__main__":
    unittest.main()
