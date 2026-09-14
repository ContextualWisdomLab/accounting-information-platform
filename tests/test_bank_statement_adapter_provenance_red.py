"""RED contracts for current ISO 20022 camt.053 adapter provenance metadata."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
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
        with mock.patch.object(
            bank_statement,
            "_MANIFEST_PATH",
            self._manifest_path_for(baseline),
        ):
            accepted = load_adapter_manifest()
        self.assertEqual(
            accepted["message_definition_identifier"],
            CAMT053_MESSAGE_DEFINITION,
        )
        self.assertEqual(accepted["submitting_organization"], "ISTH")
        self.assertEqual(accepted["source_message_set_last_updated"], "2026-03-19")

        adapter_root = bank_statement._ADAPTER_ROOT.resolve()
        outside_path = (adapter_root.parent / "bank_statement.py").resolve()
        outside_payload = outside_path.read_bytes()
        outside_digest = hashlib.sha256(outside_payload).hexdigest()
        hostile_paths = {
            "relative_traversal": "iso20022/../bank_statement.py",
            "absolute_bypass": str(outside_path),
        }

        for attack, local_package_path in hostile_paths.items():
            with self.subTest(attack=attack):
                raw_path = Path(local_package_path)
                resolved_path = (
                    raw_path
                    if raw_path.is_absolute()
                    else adapter_root.parent / raw_path
                ).resolve()
                with self.assertRaises(ValueError):
                    resolved_path.relative_to(adapter_root)

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

    def test_loader_requires_complete_role_bound_artifact_inventory(self) -> None:
        """A hash-valid subset or role-relabelled set cannot redefine adapter evidence."""
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
        expected_inventory = {
            "provenance_notice": "iso20022/NOTICE",
            "canonical_valid_fixture": (
                "iso20022/fixtures/camt.053.001.14.valid.xml"
            ),
            "cwl_derived_structural_profile": (
                "iso20022/fixtures/camt.053.001.14.structural-profile.json"
            ),
        }
        observed_inventory = {
            str(artifact["artifact_role"]): str(artifact["local_package_path"])
            for artifact in baseline["artifacts"]
        }
        self.assertEqual(observed_inventory, expected_inventory)

        with mock.patch.object(
            bank_statement,
            "_MANIFEST_PATH",
            self._manifest_path_for(baseline),
        ):
            accepted = load_adapter_manifest()
        self.assertEqual(len(accepted["artifacts"]), len(expected_inventory))

        hostile_manifests: dict[str, dict[str, object]] = {}

        missing_profile = json.loads(json.dumps(baseline))
        missing_profile["artifacts"] = [
            artifact
            for artifact in missing_profile["artifacts"]
            if artifact["artifact_role"] != "cwl_derived_structural_profile"
        ]
        hostile_manifests["missing_required_profile"] = missing_profile

        duplicate_fixture = json.loads(json.dumps(baseline))
        duplicate_fixture["artifacts"].append(
            dict(
                next(
                    artifact
                    for artifact in duplicate_fixture["artifacts"]
                    if artifact["artifact_role"] == "canonical_valid_fixture"
                )
            )
        )
        hostile_manifests["duplicate_role_and_path"] = duplicate_fixture

        relabelled_notice = json.loads(json.dumps(baseline))
        relabelled_notice["artifacts"][0]["artifact_role"] = "canonical_valid_fixture"
        hostile_manifests["role_path_relabel"] = relabelled_notice

        for attack, hostile in hostile_manifests.items():
            with self.subTest(attack=attack):
                with mock.patch.object(
                    bank_statement,
                    "_MANIFEST_PATH",
                    self._manifest_path_for(hostile),
                ):
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        "adapter artifact inventory",
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
