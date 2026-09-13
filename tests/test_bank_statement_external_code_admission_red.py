"""RED contracts for versioned ISO 20022 external-code admission."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import accounting_information_platform.bank_statement as bank_statement
from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    load_adapter_manifest,
    load_canonical_statement_fixture,
)


class BankStatementExternalCodeAdmissionRedTests(unittest.TestCase):
    """Require pinned external-code semantics before normalized evidence exists."""

    def test_adapter_manifest_pins_external_code_and_bank_transaction_code_evidence(
        self,
    ) -> None:
        """The adapter retains versioned ISO external-code and BTC source evidence."""
        manifest = load_adapter_manifest()
        artifacts = manifest["artifacts"]
        by_role = {
            str(artifact.get("artifact_role")): artifact
            for artifact in artifacts
            if isinstance(artifact, dict)
        }
        for role in (
            "iso20022_external_code_sets",
            "bank_transaction_code_combinations",
        ):
            with self.subTest(role=role):
                self.assertIn(
                    role,
                    by_role,
                    f"camt.053 admission must pin {role} in the hash-checked adapter manifest",
                )
                artifact = by_role[role]
                self.assertTrue(str(artifact.get("local_package_path") or "").strip())
                self.assertRegex(str(artifact.get("sha256") or ""), r"^[0-9a-f]{64}$")
                self.assertTrue(
                    str(artifact.get("source_version") or "").strip(),
                    f"{role} must retain the upstream release/version identifier",
                )

    def test_parser_routes_manifest_external_code_evidence_before_normalization(
        self,
    ) -> None:
        """Parser admission consumes pinned external-code evidence before normalization."""
        fixture = load_canonical_statement_fixture()
        schema_artifact = self._artifact(
            "message_schema",
            "iso20022/fixtures/camt.053.001.14.xsd",
            "a",
            "camt.053.001.14",
        )
        external_code_artifact = self._artifact(
            "iso20022_external_code_sets",
            "iso20022/fixtures/iso20022-external-code-sets.json",
            "b",
            "August 2026 (v3)",
        )
        transaction_code_artifact = self._artifact(
            "bank_transaction_code_combinations",
            "iso20022/fixtures/bank-transaction-code-combinations.xlsx",
            "c",
            "30 November 2025 (v1)",
        )
        controlled_manifest = {
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "artifacts": [
                schema_artifact,
                external_code_artifact,
                transaction_code_artifact,
            ],
        }
        schema_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        semantic_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def accept_schema(*args: object, **kwargs: object) -> None:
            rendered_contract = repr((args, kwargs))
            self.assertIn(schema_artifact["local_package_path"], rendered_contract)
            self.assertIn(schema_artifact["sha256"], rendered_contract)
            self.assertTrue(any(value == fixture for value in (*args, *kwargs.values())))
            schema_calls.append((args, kwargs))

        def reject_from_external_codes(*args: object, **kwargs: object) -> None:
            rendered_contract = repr((args, kwargs))
            for artifact in (external_code_artifact, transaction_code_artifact):
                self.assertIn(artifact["local_package_path"], rendered_contract)
                self.assertIn(artifact["sha256"], rendered_contract)
                self.assertIn(artifact["source_version"], rendered_contract)
            self.assertTrue(any(value == fixture for value in (*args, *kwargs.values())))
            self.assertEqual(len(schema_calls), 1)
            semantic_calls.append((args, kwargs))
            raise AccountingValidationError("external-code-admission-sentinel")

        with (
            patch.object(
                bank_statement,
                "load_adapter_manifest",
                return_value=controlled_manifest,
            ),
            patch.object(
                bank_statement,
                "_validate_message_schema",
                side_effect=accept_schema,
                create=True,
            ),
            patch.object(
                bank_statement,
                "_validate_external_code_evidence",
                side_effect=reject_from_external_codes,
                create=True,
            ),
            patch.object(
                bank_statement,
                "_normalize_statement",
                side_effect=AssertionError(
                    "normalization ran before versioned external-code admission"
                ),
            ),
        ):
            with self.assertRaisesRegex(
                AccountingValidationError, "external-code-admission-sentinel"
            ):
                bank_statement.parse_bank_statement_payload(
                    fixture, CAMT053_MESSAGE_DEFINITION
                )

        self.assertEqual(len(schema_calls), 1)
        self.assertEqual(len(semantic_calls), 1)

    def test_unknown_bank_transaction_domain_reaches_external_code_validator(
        self,
    ) -> None:
        """A schema-shaped but unknown BkTxCd domain is rejected by code semantics."""
        fixture = load_canonical_statement_fixture()
        marker = b"            <Cd>PMNT</Cd>\n"
        self.assertEqual(fixture.count(marker), 2)
        hostile = fixture.replace(marker, b"            <Cd>ZZZZ</Cd>\n", 1)
        self.assertEqual(hostile.count(b"<Cd>ZZZZ</Cd>"), 1)

        schema_artifact = self._artifact(
            "message_schema",
            "iso20022/fixtures/camt.053.001.14.xsd",
            "d",
            "camt.053.001.14",
        )
        external_code_artifact = self._artifact(
            "iso20022_external_code_sets",
            "iso20022/fixtures/iso20022-external-code-sets.json",
            "e",
            "August 2026 (v3)",
        )
        transaction_code_artifact = self._artifact(
            "bank_transaction_code_combinations",
            "iso20022/fixtures/bank-transaction-code-combinations.xlsx",
            "f",
            "30 November 2025 (v1)",
        )
        controlled_manifest = {
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "artifacts": [
                schema_artifact,
                external_code_artifact,
                transaction_code_artifact,
            ],
        }
        schema_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        semantic_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def accept_hostile_schema(*args: object, **kwargs: object) -> None:
            self.assertTrue(any(value == hostile for value in (*args, *kwargs.values())))
            schema_calls.append((args, kwargs))

        def reject_hostile_code(*args: object, **kwargs: object) -> None:
            rendered_contract = repr((args, kwargs))
            for artifact in (external_code_artifact, transaction_code_artifact):
                self.assertIn(artifact["local_package_path"], rendered_contract)
                self.assertIn(artifact["sha256"], rendered_contract)
            self.assertTrue(any(value == hostile for value in (*args, *kwargs.values())))
            self.assertEqual(len(schema_calls), 1)
            semantic_calls.append((args, kwargs))
            raise AccountingValidationError("unknown-bank-transaction-code-sentinel")

        with (
            patch.object(
                bank_statement,
                "load_adapter_manifest",
                return_value=controlled_manifest,
            ),
            patch.object(
                bank_statement,
                "_validate_message_schema",
                side_effect=accept_hostile_schema,
                create=True,
            ),
            patch.object(
                bank_statement,
                "_validate_external_code_evidence",
                side_effect=reject_hostile_code,
                create=True,
            ),
            patch.object(
                bank_statement,
                "_normalize_statement",
                side_effect=AssertionError(
                    "normalization ran before hostile external-code admission"
                ),
            ),
        ):
            with self.assertRaisesRegex(
                AccountingValidationError, "unknown-bank-transaction-code-sentinel"
            ):
                bank_statement.parse_bank_statement_payload(
                    hostile, CAMT053_MESSAGE_DEFINITION
                )

        self.assertEqual(len(schema_calls), 1)
        self.assertEqual(len(semantic_calls), 1)

    @staticmethod
    def _artifact(
        role: str,
        path: str,
        digest_character: str,
        source_version: str,
    ) -> dict[str, object]:
        """Build one controlled manifest artifact for admission-order assertions."""
        return {
            "local_package_path": path,
            "artifact_role": role,
            "sha256": digest_character * 64,
            "byte_length": 123,
            "source_version": source_version,
        }


if __name__ == "__main__":
    unittest.main()
