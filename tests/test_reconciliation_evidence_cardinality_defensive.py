"""Fail-closed cardinality coverage for retained reconciliation evidence."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from accounting_information_platform.reconciliation_close_package import (
    ReconciliationEvidenceReference,
    _validate_and_order_evidence,
)


class ReconciliationEvidenceCardinalityDefensiveTests(unittest.TestCase):
    """Require exactly one retained statement artifact for a close package."""

    def test_duplicate_statement_artifact_authority_is_rejected(self) -> None:
        digest = "sha256:" + "d" * 64
        evidence = (
            ReconciliationEvidenceReference(
                "reconciliation_run",
                "run-1",
                digest,
                "2026-01-01T00:00:00Z",
            ),
            ReconciliationEvidenceReference(
                "statement_artifact",
                "statement-artifact-1",
                digest,
            ),
            ReconciliationEvidenceReference(
                "statement_artifact",
                "statement-artifact-2",
                digest,
            ),
            ReconciliationEvidenceReference(
                "statement_population",
                "statement-population-1",
                digest,
            ),
            ReconciliationEvidenceReference(
                "book_population",
                "book-population-1",
                digest,
            ),
        )
        projection = SimpleNamespace(
            reconciliation_run_reference="run-1",
            statement_population_reference="statement-population-1",
            book_population_reference="book-population-1",
        )

        with self.assertRaisesRegex(
            ValueError,
            "exactly one statement_artifact evidence",
        ):
            _validate_and_order_evidence(evidence, projection=projection)


if __name__ == "__main__":
    unittest.main()
