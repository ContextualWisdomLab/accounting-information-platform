"""RED contracts for strict JSON exception-resolution command identity."""

from __future__ import annotations

import unittest

from accounting_information_platform import AccountingValidationError
from accounting_information_platform import reconciliation_exception_resolution as resolution


class ReconciliationExceptionResolutionStrictJsonRedTests(unittest.TestCase):
    """Require one RFC-8259-shaped value domain before command hashing."""

    def test_python_only_containers_cannot_be_hashed_as_command_identity(self) -> None:
        """Tuple and set values must fail before canonical serialization."""
        for value in (("tuple",), {"set"}):
            with self.subTest(value=repr(value)):
                with self.assertRaises(AccountingValidationError):
                    resolution._source_payload_hash({"request_context": value})

    def test_non_string_mapping_keys_cannot_be_hashed_as_command_identity(self) -> None:
        """JSON object identity cannot silently stringify a Python mapping key."""
        with self.assertRaises(AccountingValidationError):
            resolution._source_payload_hash({"request_context": {1: "not-json-key"}})

    def test_complete_json_value_domain_remains_deterministic(self) -> None:
        """Valid JSON scalars, arrays and objects keep one stable source identity."""
        command = {
            "null_value": None,
            "boolean_value": True,
            "string_value": "reviewed",
            "integer_value": 7,
            "float_value": 1.25,
            "array_value": [None, False, "x", 2, 3.5, {"nested": "value"}],
            "object_value": {"key": [1, 2, 3]},
        }
        first = resolution._source_payload_hash(command)
        second = resolution._source_payload_hash(command)
        self.assertEqual(first, second)
        self.assertRegex(first, r"^sha256:[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
