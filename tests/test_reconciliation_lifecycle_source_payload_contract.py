"""Coverage and install contracts for lifecycle source-payload identity."""

from __future__ import annotations

import math
import unittest
from pathlib import Path
from unittest.mock import patch

from accounting_information_platform import AccountingValidationError
from accounting_information_platform import migration_install
from accounting_information_platform import reconciliation_lifecycle as lifecycle
from tests.test_reconciliation_lifecycle import _command


class ReconciliationLifecycleSourcePayloadContractTests(unittest.TestCase):
    """Keep strict JSON identity and migration 0026 on the supported boundary."""

    def test_strict_nested_json_values_have_one_deterministic_identity(self) -> None:
        """Lists and JSON scalar values remain valid and independent of object key order."""
        first = _command(
            request_context={
                "flags": [None, True, 1, 1.25],
                "review": {"batch": "close-09", "sequence": 2},
            }
        )
        second = _command(
            request_context={
                "review": {"sequence": 2, "batch": "close-09"},
                "flags": [None, True, 1, 1.25],
            }
        )

        first_hash = lifecycle._source_payload_hash(first)
        second_hash = lifecycle._source_payload_hash(second)

        self.assertRegex(first_hash, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(first_hash, second_hash)

    def test_nonfinite_json_number_fails_before_identity_is_accepted(self) -> None:
        """NaN/Infinity cannot receive a non-standard JSON command identity."""
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaisesRegex(AccountingValidationError, "JSON-compatible"):
                    lifecycle._source_payload_hash(
                        _command(request_context={"nonfinite": value})
                    )

    def test_install_fails_closed_when_source_identity_migration_is_missing(self) -> None:
        """The supported installer may not stop before lifecycle source identity."""
        root = Path(__file__).resolve().parents[1]
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == "0026_reconciliation_lifecycle_source_payload_identity.sql":
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaisesRegex(
                AccountingValidationError,
                "0026_reconciliation_lifecycle_source_payload_identity",
            ):
                migration_install.apply_foundation_migration(
                    "postgresql://unused",
                    root / "database/migrations/0001_accounting_foundation.sql",
                )


if __name__ == "__main__":
    unittest.main()
