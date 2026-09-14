"""RED contracts for current ISO 20022 camt.053 adapter provenance metadata."""

from __future__ import annotations

import hashlib
import json
import unittest
from unittest import mock

import accounting_information_platform.bank_statement as bank_statement
from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    load_adapter_manifest,
)


class BankStatementAdapterProvenanceRedTests(unittest.TestCase):
    """Keep retained adapter provenance aligned with the ISO catalogue entry."""

    def test_manifest_records_current_v14_catalogue_provenance(self) -> None:
        """Do not misattribute the pinned V14 definition or omit its catalogue edition."""
        manifest = load_adapter_manifest()

        self.assertEqual(
            manifest["message_definition_identifier"],
            CAMT053_MESSAGE_DEFINITION,
        )
        self.assertEqual(
            manifest.get("message_definition_name"),
            "BankToCustomerStatementV14",
        )
        self.assertEqual(
            manifest.get("submitting_organization"),
            "ISTH",
            "ISO 20022 currently attributes camt.053.001.14 to submitting organisation ISTH",
        )
        self.assertEqual(
            manifest.get("source_message_set_last_updated"),
            "2026-03-19",
            "retain the ISO catalogue edition separately from local retrieval_time",
        )

    def test_loader_rejects_catalogue_provenance_drift(self) -> None:
        """Hash-valid adapter bytes do not make contradictory catalogue metadata trustworthy."""
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

        with mock.patch.object(
            bank_statement,
            "_MANIFEST_PATH",
            self._manifest_path_for(baseline),
        ):
            accepted = load_adapter_manifest()
        self.assertEqual(accepted["submitting_organization"], "ISTH")
        self.assertEqual(accepted["source_message_set_last_updated"], "2026-03-19")

        hostile_values = {
            "message_definition_name": "BankToCustomerStatementV13",
            "submitting_organization": "SWIFT",
            "source_message_set_last_updated": "2026-03-18",
            "official_source_url": "https://example.invalid/camt.053",
        }
        for field, hostile_value in hostile_values.items():
            with self.subTest(field=field):
                hostile = dict(baseline)
                hostile[field] = hostile_value
                with mock.patch.object(
                    bank_statement,
                    "_MANIFEST_PATH",
                    self._manifest_path_for(hostile),
                ):
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        "adapter catalogue provenance",
                    ):
                        load_adapter_manifest()

    def test_loader_rejects_hash_valid_artifact_path_escape(self) -> None:
        """A valid digest cannot authorize manifest reads outside the ISO adapter root."""
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
        outside_path = (bank_statement._ADAPTER_ROOT.parent / "bank_statement.py").resolve()
        outside_payload = outside_path.read_bytes()
        outside_digest = hashlib.sha256(outside_payload).hexdigest()
        hostile_paths = {
            "relative_traversal": "iso20022/../bank_statement.py",
            "absolute_bypass": str(outside_path),
        }

        for attack, local_package_path in hostile_paths.items():
            with self.subTest(attack=attack):
                hostile = json.loads(json.dumps(baseline))
                artifact = dict(hostile["artifacts"][0])
                artifact.update(
                    {
                        "local_package_path": local_package_path,
                        "sha256": outside_digest,
                        "byte_length": len(outside_payload),
                    }
                )
                hostile["artifacts"][0] = artifact
                with mock.patch.object(
                    bank_statement,
                    "_MANIFEST_PATH",
                    self._manifest_path_for(hostile),
                ):
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        "adapter artifact path",
                    ):
                        load_adapter_manifest()

    @staticmethod
    def _manifest_path_for(manifest: dict[str, object]) -> mock.Mock:
        """Supply controlled manifest bytes without intercepting other file reads."""
        manifest_path = mock.Mock()
        manifest_path.read_text.return_value = json.dumps(manifest)
        return manifest_path


if __name__ == "__main__":
    unittest.main()
