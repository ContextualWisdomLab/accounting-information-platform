"""Validated value objects for financial-report and XBRL generation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from ..core import AccountingValidationError
from .primitives import (
    _CODE_PATTERN,
    _FACT_PATTERN,
    _HASH_PATTERN,
    _RESERVED_PREFIXES,
    _XML_NAME_PATTERN,
    _absolute_uri,
    _required_text,
)


@dataclass(frozen=True, slots=True)
class FinancialReportContext:
    """Filing-independent entity, currency, period, and precision context."""

    entity_identifier_scheme: str
    entity_identifier_value: str
    reporting_currency_code: str
    current_period_start_date: date
    current_period_end_date: date
    comparison_period_start_date: date | None = None
    comparison_period_end_date: date | None = None
    decimal_precision: int = 0

    def __post_init__(self) -> None:
        """Reject incomplete report context before artifact generation."""
        _absolute_uri(self.entity_identifier_scheme, "entity_identifier_scheme")
        _required_text(self.entity_identifier_value, "entity_identifier_value")
        reporting_currency_code = _required_text(
            self.reporting_currency_code,
            "reporting_currency_code",
        )
        if re.fullmatch(r"[A-Z]{3}", reporting_currency_code) is None:
            raise AccountingValidationError(
                "reporting_currency_code must be an uppercase three-letter currency code"
            )
        if type(self.current_period_start_date) is not date or type(
            self.current_period_end_date
        ) is not date:
            raise AccountingValidationError("current period values must be dates")
        if self.current_period_end_date < self.current_period_start_date:
            raise AccountingValidationError("current period dates are out of order")
        comparison_pair = (
            self.comparison_period_start_date is not None,
            self.comparison_period_end_date is not None,
        )
        if comparison_pair[0] != comparison_pair[1]:
            raise AccountingValidationError(
                "comparison period start and end dates must be supplied together"
            )
        if comparison_pair[0] and (
            type(self.comparison_period_start_date) is not date
            or type(self.comparison_period_end_date) is not date
        ):
            raise AccountingValidationError("comparison period values must be dates")
        if (
            comparison_pair[0]
            and self.comparison_period_end_date < self.comparison_period_start_date
        ):
            raise AccountingValidationError("comparison period dates are out of order")
        if (
            isinstance(self.decimal_precision, bool)
            or not isinstance(self.decimal_precision, int)
            or not -18 <= self.decimal_precision <= 18
        ):
            raise AccountingValidationError(
                "decimal_precision must be an integer between -18 and 18"
            )

    def _document(self) -> dict[str, object]:
        """Return a stable JSON-compatible report-context document."""
        context_document: dict[str, object] = {
            "entity_identifier_scheme": self.entity_identifier_scheme,
            "entity_identifier_value": self.entity_identifier_value,
            "reporting_currency_code": self.reporting_currency_code,
            "current_period_start_date": self.current_period_start_date.isoformat(),
            "current_period_end_date": self.current_period_end_date.isoformat(),
            "decimal_precision": self.decimal_precision,
        }
        if self.comparison_period_start_date is not None:
            context_document.update(
                {
                    "comparison_period_start_date": self.comparison_period_start_date.isoformat(),
                    "comparison_period_end_date": self.comparison_period_end_date.isoformat(),
                }
            )
        return context_document


@dataclass(frozen=True, slots=True)
class XbrlConceptMapping:
    """Map one canonical report fact to a taxonomy concept and period type."""

    fact_code: str
    concept_local_name: str
    period_type_code: str

    def __post_init__(self) -> None:
        """Reject mappings that cannot become unambiguous XBRL facts."""
        fact_code = _required_text(self.fact_code, "fact_code")
        concept_local_name = _required_text(
            self.concept_local_name,
            "concept_local_name",
        )
        if _FACT_PATTERN.fullmatch(fact_code) is None:
            raise AccountingValidationError("fact_code is not a canonical reporting code")
        if _XML_NAME_PATTERN.fullmatch(concept_local_name) is None:
            raise AccountingValidationError("concept_local_name is not an XML local name")
        if self.period_type_code not in {"duration", "instant"}:
            raise AccountingValidationError("period_type_code must be duration or instant")


@dataclass(frozen=True, slots=True)
class XbrlTaxonomyProfile:
    """Versioned external taxonomy identity and canonical-fact mapping."""

    profile_identifier: str
    profile_version: int
    reporting_standard_code: str
    taxonomy_release_code: str
    taxonomy_prefix: str
    taxonomy_namespace_uri: str
    schema_reference_uri: str
    taxonomy_package_hash: str
    concept_mappings: tuple[XbrlConceptMapping, ...]

    def __post_init__(self) -> None:
        """Reject unaddressable, unhashed, or ambiguous taxonomy profiles."""
        _required_text(self.profile_identifier, "profile_identifier")
        if (
            isinstance(self.profile_version, bool)
            or not isinstance(self.profile_version, int)
            or self.profile_version < 1
        ):
            raise AccountingValidationError("profile_version must be a positive integer")
        reporting_standard_code = _required_text(
            self.reporting_standard_code,
            "reporting_standard_code",
        )
        if _CODE_PATTERN.fullmatch(reporting_standard_code) is None:
            raise AccountingValidationError("reporting_standard_code must be lower snake case")
        _required_text(self.taxonomy_release_code, "taxonomy_release_code")
        taxonomy_prefix = _required_text(self.taxonomy_prefix, "taxonomy_prefix")
        if _XML_NAME_PATTERN.fullmatch(taxonomy_prefix) is None:
            raise AccountingValidationError("taxonomy_prefix is not an XML prefix")
        normalized_prefix = taxonomy_prefix.lower()
        if normalized_prefix.startswith("xml") or normalized_prefix in _RESERVED_PREFIXES:
            raise AccountingValidationError("taxonomy_prefix is reserved")
        _absolute_uri(self.taxonomy_namespace_uri, "taxonomy_namespace_uri")
        _absolute_uri(self.schema_reference_uri, "schema_reference_uri")
        taxonomy_package_hash = _required_text(
            self.taxonomy_package_hash,
            "taxonomy_package_hash",
        )
        if _HASH_PATTERN.fullmatch(taxonomy_package_hash) is None:
            raise AccountingValidationError("taxonomy_package_hash is not a SHA-256 digest")
        mapping_records = tuple(self.concept_mappings)
        if not mapping_records or any(
            not isinstance(mapping_record, XbrlConceptMapping)
            for mapping_record in mapping_records
        ):
            raise AccountingValidationError(
                "concept_mappings must contain XbrlConceptMapping values"
            )
        fact_codes = [mapping_record.fact_code for mapping_record in mapping_records]
        concept_names = [mapping_record.concept_local_name for mapping_record in mapping_records]
        if len(fact_codes) != len(set(fact_codes)):
            raise AccountingValidationError("concept_mappings repeat a canonical fact code")
        if len(concept_names) != len(set(concept_names)):
            raise AccountingValidationError("concept_mappings repeat a taxonomy concept")
        object.__setattr__(self, "concept_mappings", mapping_records)
