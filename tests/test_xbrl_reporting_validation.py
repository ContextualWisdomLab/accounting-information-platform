"""Validation tests for XBRL export integrity and reporting primitives."""

from __future__ import annotations

import copy
import unittest
import xml.etree.ElementTree as element_tree
from decimal import Decimal

from accounting_information_platform import financial_reporting as reporting
from accounting_information_platform.financial_reporting import primitives as primitive_module
from accounting_information_platform.financial_reporting import xbrl as xbrl_module
from accounting_information_platform.core import AccountingValidationError
from financial_reporting_fixtures import (
    _context_without_comparison,
    _package_without_comparison,
    _rehash,
    _report_context,
    _statement_package,
    _taxonomy_profile,
    _valid_mapping,
    _valid_profile,
)


class ExportValidationTests(unittest.TestCase):
    """Exercise XBRL export integrity and invalid artifact handling."""

    def setUp(self) -> None:
        """Build one canonical report artifact for export validation."""
        self.artifact = reporting.build_financial_report_artifact(
            _statement_package(),
            _report_context(),
        )

    def test_rejects_public_input_types(self) -> None:
        """Reject non-mapping artifacts and non-profile taxonomy values."""
        with self.assertRaises(AccountingValidationError):
            reporting.export_xbrl_instance([], _taxonomy_profile())
        with self.assertRaises(AccountingValidationError):
            reporting.export_xbrl_instance(self.artifact, object())

    def test_rejects_artifact_envelope_tampering(self) -> None:
        """Reject source, digest, derived-field, and JSON-shape tampering."""
        invalid_artifacts: list[dict[str, object]] = []

        invalid_source = copy.deepcopy(self.artifact)
        invalid_source["source_statement_package"] = []
        invalid_artifacts.append(invalid_source)

        changed_source = copy.deepcopy(self.artifact)
        changed_source["source_statement_package"]["tenant_reference"] = "x"
        invalid_artifacts.append(changed_source)

        invalid_hash = copy.deepcopy(self.artifact)
        invalid_hash["report_artifact_hash"] = "bad"
        invalid_artifacts.append(invalid_hash)

        changed_derived_value = copy.deepcopy(self.artifact)
        changed_derived_value["profit_and_loss_summary"][
            "net_income_amount"
        ] = "x"
        invalid_artifacts.append(changed_derived_value)

        non_json_artifact = copy.deepcopy(self.artifact)
        non_json_artifact["not_json"] = {1}
        invalid_artifacts.append(non_json_artifact)

        for invalid_artifact in invalid_artifacts:
            with self.subTest(invalid_artifact=invalid_artifact):
                with self.assertRaises(AccountingValidationError):
                    reporting.export_xbrl_instance(
                        invalid_artifact,
                        _taxonomy_profile(),
                    )

    def test_rejects_invalid_context_shapes_even_after_rehash(self) -> None:
        """Rehydrate and validate context rather than trusting its artifact hash."""
        invalid_artifacts: list[dict[str, object]] = []

        invalid_context_type = copy.deepcopy(self.artifact)
        invalid_context_type["report_context"] = []
        invalid_artifacts.append(_rehash(invalid_context_type))

        missing_precision = copy.deepcopy(self.artifact)
        missing_precision["report_context"].pop("decimal_precision")
        invalid_artifacts.append(_rehash(missing_precision))

        invalid_date = copy.deepcopy(self.artifact)
        invalid_date["report_context"]["current_period_start_date"] = "bad"
        invalid_artifacts.append(_rehash(invalid_date))

        for invalid_artifact in invalid_artifacts:
            with self.subTest(invalid_artifact=invalid_artifact):
                with self.assertRaises(AccountingValidationError):
                    reporting.export_xbrl_instance(
                        invalid_artifact,
                        _taxonomy_profile(),
                    )

    def test_rejects_invalid_fact_shapes_even_after_rehash(self) -> None:
        """Rebuild canonical facts from source evidence before serializing them."""
        invalid_artifacts: list[dict[str, object]] = []
        for fact_value in ("bad", [1]):
            invalid_fact_container = copy.deepcopy(self.artifact)
            invalid_fact_container["fact_records"] = fact_value
            invalid_artifacts.append(_rehash(invalid_fact_container))

        invalid_fact_code = copy.deepcopy(self.artifact)
        invalid_fact_code["fact_records"][0]["fact_code"] = "bad"
        invalid_artifacts.append(_rehash(invalid_fact_code))

        invalid_period_context = copy.deepcopy(self.artifact)
        invalid_period_context["fact_records"][0][
            "period_context_code"
        ] = "bad"
        invalid_artifacts.append(_rehash(invalid_period_context))

        duplicate_fact = copy.deepcopy(self.artifact)
        duplicate_fact["fact_records"].append(
            copy.deepcopy(duplicate_fact["fact_records"][0])
        )
        invalid_artifacts.append(_rehash(duplicate_fact))

        non_finite_fact = copy.deepcopy(self.artifact)
        non_finite_fact["fact_records"][0]["fact_amount"] = "NaN"
        invalid_artifacts.append(_rehash(non_finite_fact))

        for invalid_artifact in invalid_artifacts:
            with self.subTest(invalid_artifact=invalid_artifact):
                with self.assertRaises(AccountingValidationError):
                    reporting.export_xbrl_instance(
                        invalid_artifact,
                        _taxonomy_profile(),
                    )

    def test_rejects_missing_mapping_and_wrong_period_type(self) -> None:
        """Map only facts present in the artifact with the canonical period type."""
        missing_fact_profile = _valid_profile(
            concept_mappings=(
                _valid_mapping(fact_code="profit_loss.missing_amount"),
            )
        )
        with self.assertRaises(AccountingValidationError):
            reporting.export_xbrl_instance(
                self.artifact,
                missing_fact_profile,
            )

        wrong_period_profile = _valid_profile(
            concept_mappings=(
                _valid_mapping(period_type_code="instant"),
            )
        )
        with self.assertRaises(AccountingValidationError):
            reporting.export_xbrl_instance(
                self.artifact,
                wrong_period_profile,
            )

    def test_noncomparative_export_omits_comparison_contexts(self) -> None:
        """Create only current contexts for a report without comparative data."""
        report_artifact = reporting.build_financial_report_artifact(
            _package_without_comparison(),
            _context_without_comparison(),
        )
        xbrl_export = reporting.export_xbrl_instance(
            report_artifact,
            _valid_profile(),
        )
        self.assertNotIn("comparison_duration", xbrl_export["xbrl_instance"])

    def test_context_element_requires_comparison_dates(self) -> None:
        """Reject direct construction of a comparison context without dates."""
        xml_root = element_tree.Element("root")
        with self.assertRaises(AccountingValidationError):
            xbrl_module._context_element(
                xml_root,
                "bad",
                _context_without_comparison(),
                True,
                False,
            )


class HelperValidationTests(unittest.TestCase):
    """Exercise validation primitives that protect public report contracts."""

    def test_required_and_optional_text(self) -> None:
        """Reject noncanonical text and normalize an absent optional value."""
        for raw_value in (None, "", " bad", 1):
            with self.subTest(raw_value=raw_value):
                with self.assertRaises(AccountingValidationError):
                    primitive_module._required_text(raw_value, "field")
        self.assertEqual(
            primitive_module._optional_text(None, "field"),
            "",
        )
        for raw_value in (1, " bad"):
            with self.subTest(raw_value=raw_value):
                with self.assertRaises(AccountingValidationError):
                    primitive_module._optional_text(raw_value, "field")
        self.assertEqual(
            primitive_module._optional_text("", "field"),
            "",
        )

    def test_absolute_uri_variants(self) -> None:
        """Accept a complete URN and reject relative or incomplete identifiers."""
        self.assertEqual(
            primitive_module._absolute_uri("urn:test:value", "field"),
            "urn:test:value",
        )
        for raw_value in ("relative", "http:path", "urn:"):
            with self.subTest(raw_value=raw_value):
                with self.assertRaises(AccountingValidationError):
                    primitive_module._absolute_uri(raw_value, "field")

    def test_amount_and_json_failures(self) -> None:
        """Reject conversion failures, non-finite decimals, and invalid JSON."""

        class BadString:
            """Raise while being converted to text for decimal validation."""

            def __str__(self) -> str:
                """Reject string conversion."""
                raise ValueError("bad")

        for raw_value in (object(), BadString(), "NaN", "Infinity"):
            with self.subTest(raw_value=raw_value):
                with self.assertRaises(AccountingValidationError):
                    primitive_module._amount(raw_value, "amount")
        self.assertEqual(
            primitive_module._amount("-0", "amount"),
            Decimal("0"),
        )
        with self.assertRaises(AccountingValidationError):
            primitive_module._json_bytes({1}, "bad json")
        with self.assertRaises(AccountingValidationError):
            primitive_module._json_bytes(float("nan"), "bad json")


if __name__ == "__main__":
    unittest.main()
