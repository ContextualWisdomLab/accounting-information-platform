"""RED contracts for integrity-pinned camt.053.001.14 schema admission."""

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


class BankStatementXsdAdmissionRedTests(unittest.TestCase):
    """Require schema-backed source admission before normalized evidence exists."""

    def test_adapter_manifest_pins_the_supported_message_xsd(self) -> None:
        """The supported ISO revision retains an integrity-pinned XSD artifact."""
        manifest = load_adapter_manifest()
        artifacts = manifest["artifacts"]
        schema_paths = [
            str(artifact["local_package_path"])
            for artifact in artifacts
            if str(artifact["local_package_path"]).lower().endswith(".xsd")
        ]
        self.assertTrue(
            any("camt.053.001.14" in path for path in schema_paths),
            "camt.053.001.14 admission must retain its vendored XSD in the hash-checked adapter manifest",
        )

    def test_parser_routes_manifest_xsd_to_validator_before_normalization(self) -> None:
        """Parser admission consumes the manifest-selected XSD before normalization."""
        fixture = load_canonical_statement_fixture()
        schema_artifact = {
            "local_package_path": "iso20022/fixtures/camt.053.001.14.xsd",
            "artifact_role": "message_schema",
            "sha256": "a" * 64,
            "byte_length": 123,
        }
        controlled_manifest = {
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "artifacts": [schema_artifact],
        }
        validation_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def reject_from_schema_validator(*args: object, **kwargs: object) -> None:
            rendered_contract = repr((args, kwargs))
            self.assertIn(schema_artifact["local_package_path"], rendered_contract)
            self.assertIn(schema_artifact["sha256"], rendered_contract)
            validation_calls.append((args, kwargs))
            raise AccountingValidationError("schema-admission-sentinel")

        with (
            patch.object(
                bank_statement,
                "load_adapter_manifest",
                return_value=controlled_manifest,
            ),
            patch.object(
                bank_statement,
                "_validate_message_schema",
                side_effect=reject_from_schema_validator,
                create=True,
            ),
            patch.object(
                bank_statement,
                "_normalize_statement",
                side_effect=AssertionError(
                    "normalization ran before manifest-selected XSD admission"
                ),
            ),
        ):
            with self.assertRaisesRegex(
                AccountingValidationError, "schema-admission-sentinel"
            ):
                bank_statement.parse_bank_statement_payload(
                    fixture, CAMT053_MESSAGE_DEFINITION
                )

        self.assertEqual(len(validation_calls), 1)

    def test_unknown_iso_element_is_rejected_by_manifest_selected_schema(self) -> None:
        """The hostile payload itself reaches schema admission before normalization."""
        fixture = load_canonical_statement_fixture()
        marker = b"      <Id>BANK-STMT-2026-08-24</Id>\n"
        self.assertEqual(fixture.count(marker), 1)
        hostile = fixture.replace(
            marker,
            marker + b"      <CwlUnexpectedEvidence>not-in-camt053</CwlUnexpectedEvidence>\n",
            1,
        )
        schema_artifact = {
            "local_package_path": "iso20022/fixtures/camt.053.001.14.xsd",
            "artifact_role": "message_schema",
            "sha256": "b" * 64,
            "byte_length": 456,
        }
        controlled_manifest = {
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "artifacts": [schema_artifact],
        }
        validation_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def reject_hostile_from_schema(*args: object, **kwargs: object) -> None:
            rendered_contract = repr((args, kwargs))
            self.assertIn(schema_artifact["local_package_path"], rendered_contract)
            self.assertIn(schema_artifact["sha256"], rendered_contract)
            supplied_values = (*args, *kwargs.values())
            self.assertTrue(
                any(value == hostile for value in supplied_values),
                "the manifest-selected schema validator must receive the hostile statement payload",
            )
            validation_calls.append((args, kwargs))
            raise AccountingValidationError("hostile-schema-admission-sentinel")

        with (
            patch.object(
                bank_statement,
                "load_adapter_manifest",
                return_value=controlled_manifest,
            ),
            patch.object(
                bank_statement,
                "_validate_message_schema",
                side_effect=reject_hostile_from_schema,
                create=True,
            ),
            patch.object(
                bank_statement,
                "_normalize_statement",
                side_effect=AssertionError(
                    "normalization ran before hostile payload schema admission"
                ),
            ),
        ):
            with self.assertRaisesRegex(
                AccountingValidationError, "hostile-schema-admission-sentinel"
            ):
                bank_statement.parse_bank_statement_payload(
                    hostile, CAMT053_MESSAGE_DEFINITION
                )

        self.assertEqual(len(validation_calls), 1)

    def test_foreign_namespace_lookalike_subtree_is_rejected_before_normalization(self) -> None:
        """A correct Document namespace cannot authorize local-name lookalikes in another namespace."""
        fixture = load_canonical_statement_fixture()
        baseline = bank_statement.parse_bank_statement_payload(
            fixture, CAMT053_MESSAGE_DEFINITION
        )
        self.assertEqual(
            baseline.message_definition_identifier, CAMT053_MESSAGE_DEFINITION
        )

        marker = b"  <BkToCstmrStmt>\n"
        self.assertEqual(fixture.count(marker), 1)
        foreign_namespace = "urn:cwl:test:foreign-camt053-lookalike"
        hostile = fixture.replace(
            marker,
            f'  <BkToCstmrStmt xmlns="{foreign_namespace}">\n'.encode("utf-8"),
            1,
        )
        self.assertNotEqual(hostile, fixture)

        hostile_document = bank_statement._parse_bounded_xml(hostile)
        self.assertEqual(hostile_document.namespace, bank_statement.CAMT053_NAMESPACE)
        self.assertEqual(hostile_document.children[0].local_name, "BkToCstmrStmt")
        self.assertEqual(hostile_document.children[0].namespace, foreign_namespace)

        with patch.object(
            bank_statement,
            "_normalize_statement",
            side_effect=AssertionError(
                "foreign-namespace local-name lookalikes reached normalization before schema admission"
            ),
        ):
            with self.assertRaisesRegex(AccountingValidationError, "schema|namespace"):
                bank_statement.parse_bank_statement_payload(
                    hostile, CAMT053_MESSAGE_DEFINITION
                )


if __name__ == "__main__":
    unittest.main()
