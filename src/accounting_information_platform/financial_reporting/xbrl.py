"""Deterministic XBRL 2.1 serialization from canonical report artifacts."""

from __future__ import annotations

import json
import xml.etree.ElementTree as element_tree
from collections.abc import Mapping
from datetime import date

from ..core import AccountingValidationError
from .artifact import build_financial_report_artifact
from .contracts import FinancialReportContext, XbrlTaxonomyProfile
from .primitives import _HASH_PATTERN, _digest, _json_bytes

_XBRLI_NAMESPACE = "http://www.xbrl.org/2003/instance"
_LINK_NAMESPACE = "http://www.xbrl.org/2003/linkbase"
_XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
_ISO4217_NAMESPACE = "http://www.xbrl.org/2003/iso4217"

for _prefix_name, _namespace_uri in (
    ("xbrli", _XBRLI_NAMESPACE),
    ("link", _LINK_NAMESPACE),
    ("xlink", _XLINK_NAMESPACE),
    ("iso4217", _ISO4217_NAMESPACE),
):
    element_tree.register_namespace(_prefix_name, _namespace_uri)


def export_xbrl_instance(
    report_artifact: Mapping[str, object],
    taxonomy_profile: XbrlTaxonomyProfile,
) -> dict[str, object]:
    """Serialize a verified report artifact as a deterministic XBRL 2.1 instance."""
    if not isinstance(report_artifact, Mapping):
        raise AccountingValidationError("report_artifact must be a mapping")
    if not isinstance(taxonomy_profile, XbrlTaxonomyProfile):
        raise AccountingValidationError(
            "taxonomy_profile must be an XbrlTaxonomyProfile"
        )
    artifact_document, report_context = _verified_artifact(report_artifact)
    fact_index = _facts(artifact_document.get("fact_records"))

    xml_root = element_tree.Element(
        f"{{{_XBRLI_NAMESPACE}}}xbrl",
        {
            f"xmlns:{taxonomy_profile.taxonomy_prefix}": (
                taxonomy_profile.taxonomy_namespace_uri
            )
        },
    )
    element_tree.SubElement(
        xml_root,
        f"{{{_LINK_NAMESPACE}}}schemaRef",
        {
            f"{{{_XLINK_NAMESPACE}}}type": "simple",
            f"{{{_XLINK_NAMESPACE}}}href": (
                taxonomy_profile.schema_reference_uri
            ),
        },
    )
    _contexts(xml_root, report_context)
    unit_element = element_tree.SubElement(
        xml_root,
        f"{{{_XBRLI_NAMESPACE}}}unit",
        {"id": "reporting_currency"},
    )
    measure_element = element_tree.SubElement(
        unit_element,
        f"{{{_XBRLI_NAMESPACE}}}measure",
    )
    measure_element.text = f"iso4217:{report_context.reporting_currency_code}"

    for mapping_record in taxonomy_profile.concept_mappings:
        mapped_facts = [
            fact_record
            for (fact_code, _period_code), fact_record in fact_index.items()
            if fact_code == mapping_record.fact_code
        ]
        if not mapped_facts:
            raise AccountingValidationError(
                "mapped fact is missing from report artifact: "
                + mapping_record.fact_code
            )
        mapped_facts.sort(
            key=lambda fact_record: (
                fact_record["period_context_code"] != "current"
            )
        )
        for fact_record in mapped_facts:
            if (
                fact_record["period_type_code"]
                != mapping_record.period_type_code
            ):
                raise AccountingValidationError(
                    "taxonomy mapping period type does not match canonical report fact"
                )
            context_reference = (
                f"{fact_record['period_context_code']}_"
                f"{mapping_record.period_type_code}"
            )
            fact_element = element_tree.SubElement(
                xml_root,
                (
                    f"{taxonomy_profile.taxonomy_prefix}:"
                    f"{mapping_record.concept_local_name}"
                ),
                {
                    "contextRef": context_reference,
                    "unitRef": "reporting_currency",
                    "decimals": str(report_context.decimal_precision),
                },
            )
            fact_element.text = str(fact_record["fact_amount"])

    xml_bytes = element_tree.tostring(
        xml_root,
        encoding="utf-8",
        xml_declaration=True,
        short_empty_elements=True,
    )
    artifact_hash = str(artifact_document["report_artifact_hash"])
    return {
        "export_contract_version": 1,
        "media_type": "application/xbrl+xml",
        "file_name": f"financial-report-{artifact_hash[7:23]}.xbrl",
        "report_artifact_reference": artifact_document[
            "report_artifact_reference"
        ],
        "report_artifact_hash": artifact_hash,
        "taxonomy_profile_identifier": taxonomy_profile.profile_identifier,
        "taxonomy_profile_version": taxonomy_profile.profile_version,
        "reporting_standard_code": taxonomy_profile.reporting_standard_code,
        "taxonomy_release_code": taxonomy_profile.taxonomy_release_code,
        "taxonomy_package_hash": taxonomy_profile.taxonomy_package_hash,
        "xbrl_instance_hash": _digest(xml_bytes),
        "xbrl_instance": xml_bytes.decode("utf-8"),
    }


def _verified_artifact(
    report_artifact: Mapping[str, object],
) -> tuple[dict[str, object], FinancialReportContext]:
    """Verify hashes and reproduce every derived field from embedded source evidence."""
    artifact_document = json.loads(
        _json_bytes(
            report_artifact,
            "report_artifact is not JSON-compatible",
        ).decode("utf-8")
    )
    source_document = artifact_document.get("source_statement_package")
    if not isinstance(source_document, Mapping):
        raise AccountingValidationError(
            "source_statement_package must be a mapping"
        )
    source_hash = _digest(
        _json_bytes(
            source_document,
            "source_statement_package is not JSON-compatible",
        )
    )
    if artifact_document.get("source_package_hash") != source_hash:
        raise AccountingValidationError(
            "report artifact source package hash does not match"
        )
    stored_hash = artifact_document.get("report_artifact_hash")
    if (
        not isinstance(stored_hash, str)
        or _HASH_PATTERN.fullmatch(stored_hash) is None
    ):
        raise AccountingValidationError(
            "report_artifact_hash is not a SHA-256 digest"
        )
    hash_document = dict(artifact_document)
    del hash_document["report_artifact_hash"]
    if stored_hash != _digest(
        _json_bytes(
            hash_document,
            "report artifact hash payload is invalid",
        )
    ):
        raise AccountingValidationError("report artifact hash does not match")
    report_context = _context(artifact_document.get("report_context"))
    rebuilt_artifact = build_financial_report_artifact(
        source_document,
        report_context,
    )
    if artifact_document != rebuilt_artifact:
        raise AccountingValidationError(
            "report artifact does not reproduce from its source package and context"
        )
    return artifact_document, report_context


def _context(raw_context: object) -> FinancialReportContext:
    """Rehydrate a validated report context from a JSON-compatible mapping."""
    if not isinstance(raw_context, Mapping):
        raise AccountingValidationError("report_context must be a mapping")
    try:
        return FinancialReportContext(
            entity_identifier_scheme=str(
                raw_context["entity_identifier_scheme"]
            ),
            entity_identifier_value=str(raw_context["entity_identifier_value"]),
            reporting_currency_code=str(raw_context["reporting_currency_code"]),
            current_period_start_date=date.fromisoformat(
                str(raw_context["current_period_start_date"])
            ),
            current_period_end_date=date.fromisoformat(
                str(raw_context["current_period_end_date"])
            ),
            comparison_period_start_date=(
                date.fromisoformat(
                    str(raw_context["comparison_period_start_date"])
                )
                if "comparison_period_start_date" in raw_context
                else None
            ),
            comparison_period_end_date=(
                date.fromisoformat(
                    str(raw_context["comparison_period_end_date"])
                )
                if "comparison_period_end_date" in raw_context
                else None
            ),
            decimal_precision=int(raw_context["decimal_precision"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise AccountingValidationError(
            "report_context contains invalid values"
        ) from error


def _facts(raw_facts: object) -> dict[tuple[str, str], dict[str, object]]:
    """Index facts that already reproduced from the canonical source package."""
    fact_records = list(raw_facts)
    return {
        (
            str(fact_record["fact_code"]),
            str(fact_record["period_context_code"]),
        ): dict(fact_record)
        for fact_record in fact_records
    }


def _contexts(
    xml_root: element_tree.Element,
    report_context: FinancialReportContext,
) -> None:
    """Append current and optional comparison XBRL duration and instant contexts."""
    _context_element(
        xml_root,
        "current_duration",
        report_context,
        False,
        False,
    )
    _context_element(
        xml_root,
        "current_instant",
        report_context,
        False,
        True,
    )
    if report_context.comparison_period_start_date is not None:
        _context_element(
            xml_root,
            "comparison_duration",
            report_context,
            True,
            False,
        )
        _context_element(
            xml_root,
            "comparison_instant",
            report_context,
            True,
            True,
        )


def _context_element(
    xml_root: element_tree.Element,
    context_identifier: str,
    report_context: FinancialReportContext,
    comparison_period: bool,
    instant_period: bool,
) -> None:
    """Append one XBRL entity-period context."""
    context_element = element_tree.SubElement(
        xml_root,
        f"{{{_XBRLI_NAMESPACE}}}context",
        {"id": context_identifier},
    )
    entity_element = element_tree.SubElement(
        context_element,
        f"{{{_XBRLI_NAMESPACE}}}entity",
    )
    identifier_element = element_tree.SubElement(
        entity_element,
        f"{{{_XBRLI_NAMESPACE}}}identifier",
        {"scheme": report_context.entity_identifier_scheme},
    )
    identifier_element.text = report_context.entity_identifier_value
    period_element = element_tree.SubElement(
        context_element,
        f"{{{_XBRLI_NAMESPACE}}}period",
    )
    if comparison_period:
        start_date = report_context.comparison_period_start_date
        end_date = report_context.comparison_period_end_date
    else:
        start_date = report_context.current_period_start_date
        end_date = report_context.current_period_end_date
    if start_date is None or end_date is None:
        raise AccountingValidationError(
            "comparison period dates are required"
        )
    if instant_period:
        instant_element = element_tree.SubElement(
            period_element,
            f"{{{_XBRLI_NAMESPACE}}}instant",
        )
        instant_element.text = end_date.isoformat()
    else:
        start_element = element_tree.SubElement(
            period_element,
            f"{{{_XBRLI_NAMESPACE}}}startDate",
        )
        start_element.text = start_date.isoformat()
        end_element = element_tree.SubElement(
            period_element,
            f"{{{_XBRLI_NAMESPACE}}}endDate",
        )
        end_element.text = end_date.isoformat()
