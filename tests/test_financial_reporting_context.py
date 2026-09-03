"""Validation tests for report context and taxonomy-profile trust boundaries."""

from __future__ import annotations

import unittest
from datetime import date

from accounting_information_platform import financial_reporting as reporting
from accounting_information_platform.core import AccountingValidationError
from financial_reporting_fixtures import (
    _context_without_comparison,
    _valid_mapping,
    _valid_profile,
)


class ContextAndProfileValidationTests(unittest.TestCase):
    """Exercise report-context and taxonomy-profile trust boundaries."""

    def test_context_without_comparison_omits_comparison_dates(self) -> None:
        """Keep non-comparative context documents free of absent date keys."""
        context_document = _context_without_comparison()._document()
        self.assertNotIn("comparison_period_start_date", context_document)
        self.assertNotIn("comparison_period_end_date", context_document)

    def test_context_rejects_each_invalid_shape(self) -> None:
        """Reject invalid XML text, currency, date types/order, pairs, and precision."""
        base_values = {
            "entity_identifier_scheme": "https://example.com/entity",
            "entity_identifier_value": "ENTITY-1",
            "reporting_currency_code": "KRW",
            "current_period_start_date": date(2026, 1, 1),
            "current_period_end_date": date(2026, 12, 31),
            "comparison_period_start_date": date(2025, 1, 1),
            "comparison_period_end_date": date(2025, 12, 31),
            "decimal_precision": 2,
        }
        invalid_overrides = (
            {"entity_identifier_scheme": "https://example.com/\u0000"},
            {"entity_identifier_value": "ENTITY\u0000"},
            {"reporting_currency_code": "krw"},
            {"current_period_start_date": "2026-01-01"},
            {"current_period_end_date": "2026-12-31"},
            {"comparison_period_start_date": "2025-01-01"},
            {"comparison_period_end_date": "2025-12-31"},
            {"current_period_start_date": date(2027, 1, 1)},
            {"comparison_period_start_date": None},
            {"comparison_period_end_date": None},
            {
                "comparison_period_start_date": date(2025, 12, 31),
                "comparison_period_end_date": date(2025, 1, 1),
            },
            {"decimal_precision": True},
            {"decimal_precision": "2"},
            {"decimal_precision": 19},
        )
        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides):
                with self.assertRaises(AccountingValidationError):
                    reporting.FinancialReportContext(**(base_values | overrides))

    def test_mapping_rejects_invalid_fact_concept_and_period_fields(self) -> None:
        """Reject noncanonical fact codes, XML names, and period types."""
        invalid_overrides = (
            {"fact_code": "bad"},
            {"concept_local_name": "bad:name"},
            {"period_type_code": "quarter"},
        )
        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides):
                with self.assertRaises(AccountingValidationError):
                    _valid_mapping(**overrides)

    def test_profile_rejects_invalid_fields_and_ambiguous_mappings(self) -> None:
        """Reject profiles that cannot identify one immutable safe mapping set."""
        duplicate_fact = _valid_mapping(concept_local_name="OtherProfit")
        duplicate_concept = _valid_mapping(
            fact_code="profit_loss.revenue_amount"
        )
        invalid_overrides = (
            {"profile_identifier": ""},
            {"profile_identifier": "profile\u0000"},
            {"profile_version": True},
            {"profile_version": "1"},
            {"profile_version": 0},
            {"reporting_standard_code": "Bad-Code"},
            {"taxonomy_release_code": " "},
            {"taxonomy_prefix": "1bad"},
            {"taxonomy_prefix": "xml"},
            {"taxonomy_prefix": "xmlFuture"},
            {"taxonomy_namespace_uri": "relative"},
            {"taxonomy_namespace_uri": "https://example.com/\u0000"},
            {"schema_reference_uri": "relative"},
            {"schema_reference_uri": "https://example.com/\u0000"},
            {"taxonomy_package_hash": "bad"},
            {"concept_mappings": ()},
            {"concept_mappings": ("bad",)},
            {"concept_mappings": (_valid_mapping(), duplicate_fact)},
            {"concept_mappings": (_valid_mapping(), duplicate_concept)},
        )
        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides):
                with self.assertRaises(AccountingValidationError):
                    _valid_profile(**overrides)

        profile = _valid_profile(concept_mappings=[_valid_mapping()])
        self.assertIsInstance(profile.concept_mappings, tuple)


if __name__ == "__main__":
    unittest.main()
