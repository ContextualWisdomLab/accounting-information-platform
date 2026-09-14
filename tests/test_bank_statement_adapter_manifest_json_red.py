"""RED contracts for unambiguous ISO 20022 adapter-manifest JSON evidence."""

from __future__ import annotations

import json
import unittest
from unittest import mock

import accounting_information_platform.bank_statement as bank_statement
from accounting_information_platform import AccountingValidationError, load_adapter_manifest


class BankStatementAdapterManifestJsonRedTests(unittest.TestCase):
    """Reject duplicate JSON members before manifest authority is interpreted."""

    def test_loader_rejects_duplicate_json_members_at_any_object_depth(self) -> None:
        """Duplicate names make evidence interpretation parser-dependent even when values agree."""
        baseline = json.loads(
            bank_statement._MANIFEST_PATH.read_text(encoding="utf-8")
        )
        baseline.update(
            {
                "message_definition_name": "BankToCustomerStatementV14",
                "submitting_organization": "ISTH",
                "source_message_set_last_updated": "2026-03-19",
                "official_source_url": (
                    "https://www.iso20022.org/iso-20022-message-definitions?search=camt.053"
                ),
            }
        )
        baseline_text = json.dumps(baseline, separators=(",", ":"))

        with mock.patch.object(
            bank_statement,
            "_MANIFEST_PATH",
            self._manifest_path_for_raw_json(baseline_text),
        ):
            accepted = load_adapter_manifest()
        self.assertEqual(accepted["adapter_version"], "1")

        duplicate_version = baseline_text.replace(
            '"adapter_version":"1"',
            '"adapter_version":"1","adapter_version":"1"',
            1,
        )
        self.assertNotEqual(duplicate_version, baseline_text)

        first_role = str(baseline["artifacts"][0]["artifact_role"])
        role_member = json.dumps("artifact_role") + ":" + json.dumps(first_role)
        duplicate_role = baseline_text.replace(
            role_member,
            f"{role_member},{role_member}",
            1,
        )
        self.assertNotEqual(duplicate_role, baseline_text)

        for location, hostile_text in {
            "top_level": duplicate_version,
            "artifact_object": duplicate_role,
        }.items():
            with self.subTest(location=location):
                with mock.patch.object(
                    bank_statement,
                    "_MANIFEST_PATH",
                    self._manifest_path_for_raw_json(hostile_text),
                ):
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        "adapter manifest duplicate JSON member",
                    ):
                        load_adapter_manifest()

    @staticmethod
    def _manifest_path_for_raw_json(raw_json: str) -> mock.Mock:
        """Supply exact manifest bytes so duplicate members survive until the loader parses them."""
        manifest_path = mock.Mock()
        manifest_path.read_text.return_value = raw_json
        return manifest_path


if __name__ == "__main__":
    unittest.main()
