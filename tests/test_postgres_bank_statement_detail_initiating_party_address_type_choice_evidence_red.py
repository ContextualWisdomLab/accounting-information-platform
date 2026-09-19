"""PostgreSQL REDs for initiating-party PostalAddress27 address-type choice evidence."""

from __future__ import annotations

import copy
import uuid
from decimal import Decimal

from tests import (
    test_postgres_bank_statement_detail_initiating_party_postal_address_evidence_red as postal,
)

_BASE_PROPRIETARY = {
    "id": "BIZZ",
    "issuer": "Initiating Party Address Registry",
    "scheme_name": "PARTY_ADDR_TYPE",
}


class BankStatementDetailInitiatingPartyAddressTypeChoiceEvidenceRedTests(
    postal.BankStatementDetailInitiatingPartyPostalAddressEvidenceRedTests
):
    """Retain InitgPty/Pty/PstlAdr/AdrTp Cd|Prtry semantics as private evidence."""

    def setUp(self) -> None:
        """Prepare proprietary AddressType3Choice variants on the canonical party postal RED."""
        super().setUp()
        self.base_postal = copy.deepcopy(self.base_postal)
        self.base_postal["address_type"] = {
            "proprietary": copy.deepcopy(_BASE_PROPRIETARY)
        }
        self.variants = self._address_type_variants(self.base_postal)

    def test_proprietary_address_type_layout_is_representation_only(self) -> None:
        """Whitespace inside GenericIdentification30 changes bytes without semantic drift."""
        baseline_payload = self._with_postal(self.base_postal)
        needle = b"                      <Prtry>\n"
        if baseline_payload.count(needle) != 1:
            raise AssertionError("initiating-party proprietary AdrTp must be unique")
        formatted_payload = baseline_payload.replace(
            needle,
            needle + b"                        \n",
            1,
        )
        if baseline_payload == formatted_payload:
            raise AssertionError("representation-only fixture must change raw bytes")

        baseline = postal.parse_bank_statement_payload(
            baseline_payload,
            postal.CAMT053_MESSAGE_DEFINITION,
        )
        formatted = postal.parse_bank_statement_payload(
            formatted_payload,
            postal.CAMT053_MESSAGE_DEFINITION,
        )
        baseline_entry = baseline.entries[0]
        formatted_entry = formatted.entries[0]
        baseline_detail = baseline_entry.entry_details[0]
        formatted_detail = formatted_entry.entry_details[0]
        self.case._assert_financial_truth(baseline_entry, baseline_detail)
        self.case._assert_financial_truth(formatted_entry, formatted_detail)

        for value in (
            baseline.source_artifact_hash,
            formatted.source_artifact_hash,
            baseline_detail.initiating_party_evidence_hash,
            formatted_detail.initiating_party_evidence_hash,
            baseline_detail.source_detail_hash,
            formatted_detail.source_detail_hash,
            baseline_entry.source_entry_hash,
            formatted_entry.source_entry_hash,
            baseline.normalized_payload_hash,
            formatted.normalized_payload_hash,
            baseline.account_identifier_hash,
            formatted.account_identifier_hash,
            baseline.entries[1].source_entry_hash,
            formatted.entries[1].source_entry_hash,
        ):
            self.case._assert_sha256(value)

        self.assertNotEqual(baseline.source_artifact_hash, formatted.source_artifact_hash)
        self.assertEqual(
            baseline_detail.initiating_party_evidence_hash,
            formatted_detail.initiating_party_evidence_hash,
        )
        self.assertEqual(
            baseline_detail.source_detail_hash,
            formatted_detail.source_detail_hash,
        )
        self.assertEqual(
            baseline_entry.source_entry_hash,
            formatted_entry.source_entry_hash,
        )
        self.assertEqual(
            baseline.normalized_payload_hash,
            formatted.normalized_payload_hash,
        )
        self.assertEqual(
            baseline.account_identifier_hash,
            formatted.account_identifier_hash,
        )
        self.assertEqual(
            baseline.entries[1].source_entry_hash,
            formatted.entries[1].source_entry_hash,
        )

    def test_proprietary_address_type_is_not_buyer_reversible(self) -> None:
        """GenericIdentification30 changes internal evidence without exposing source values."""
        coded = copy.deepcopy(self.base_postal)
        coded["address_type"] = {"code": "BIZZ"}

        rich = self.case._ingest_and_read_first_entry(
            self._with_postal(self.base_postal),
            f"initiating-party-address-type-rich-{uuid.uuid4().hex}",
        )
        coded_entry = self.case._ingest_and_read_first_entry(
            self._with_postal(coded),
            f"initiating-party-address-type-coded-{uuid.uuid4().hex}",
        )
        evidence_key = "initiating_party_evidence_hash"

        for entry in (rich, coded_entry):
            self.case._assert_uuid(entry["bank_statement_entry_id"])
            self.case._assert_sha256(entry["source_entry_hash"])
            details = entry.get("entry_details")
            if not isinstance(details, list) or not details:
                raise AssertionError("expected buyer entry to expose first detail")
            detail = details[0]
            if not isinstance(detail, dict):
                raise AssertionError("expected buyer entry detail mapping")
            self.case._assert_sha256(detail[evidence_key])
            self.case._assert_sha256(detail["source_detail_hash"])
            self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
            self.assertEqual(entry["entry_currency_code"], "KRW")
            self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
            self.assertEqual(detail["detail_currency_code"], "KRW")

        rich_detail = rich["entry_details"][0]
        coded_detail = coded_entry["entry_details"][0]
        self.assertNotEqual(
            rich_detail[evidence_key],
            coded_detail[evidence_key],
        )
        self.assertNotEqual(
            rich_detail["source_detail_hash"],
            coded_detail["source_detail_hash"],
        )
        self.assertNotEqual(rich["source_entry_hash"], coded_entry["source_entry_hash"])

        rich_public = self.case._public_entry_projection(rich, evidence_key)
        coded_public = self.case._public_entry_projection(coded_entry, evidence_key)
        self.assertEqual(rich_public, coded_public)
        buyer_values = set(self.case._scalar_leaves(rich_public))
        source_values = set(self.case._scalar_leaves(_BASE_PROPRIETARY))
        self.assertTrue(source_values.isdisjoint(buyer_values))

    def _with_postal(self, postal_value: dict[str, object] | None) -> bytes:
        """Return InitgPty/Pty with optional PostalAddress27 and either AdrTp branch."""
        payload = self.case._with_identified_party(
            "organisation",
            self.case.base_identifier,
        )
        if postal_value is None:
            return payload
        name_marker = f"                  <Nm>{self.case.party_name}</Nm>\n".encode("utf-8")
        id_marker = b"                  <Id>\n"
        insertion = name_marker + id_marker
        if payload.count(insertion) != 1:
            raise AssertionError("target initiating-party Nm/Id sequence must be unique")
        return payload.replace(
            insertion,
            name_marker + self._postal_xml(postal_value) + id_marker,
            1,
        )

    @staticmethod
    def _postal_xml(address: dict[str, object]) -> bytes:
        """Serialize PostalAddress27 while preserving AddressType3Choice discriminator."""
        address_type = address.get("address_type")
        if not isinstance(address_type, dict) or len(address_type) != 1:
            raise AssertionError("AdrTp must contain exactly one AddressType3Choice branch")
        if "code" in address_type:
            return (
                postal.ultimate_postal.BankStatementDetailUltimatePartyPostalAddressEvidenceRedTests
                ._postal_xml(address)
            )
        if "proprietary" not in address_type:
            raise AssertionError("unsupported AddressType3Choice branch")

        proprietary = address_type["proprietary"]
        if not isinstance(proprietary, dict):
            raise AssertionError("proprietary AdrTp must be structured")
        identifier = proprietary.get("id")
        issuer = proprietary.get("issuer")
        if not isinstance(identifier, str) or not identifier:
            raise AssertionError("GenericIdentification30 requires non-empty Id")
        if not isinstance(issuer, str) or not issuer:
            raise AssertionError("GenericIdentification30 requires non-empty Issr")

        coded_shadow = copy.deepcopy(address)
        coded_shadow["address_type"] = {"code": "BIZZ"}
        rendered = (
            postal.ultimate_postal.BankStatementDetailUltimatePartyPostalAddressEvidenceRedTests
            ._postal_xml(coded_shadow)
        )
        coded_block = (
            b"                    <AdrTp>\n"
            b"                      <Cd>BIZZ</Cd>\n"
            b"                    </AdrTp>\n"
        )
        if rendered.count(coded_block) != 1:
            raise AssertionError("coded shadow must contain exactly one AdrTp block")

        scheme_name = proprietary.get("scheme_name")
        if scheme_name is not None and (
            not isinstance(scheme_name, str) or not scheme_name
        ):
            raise AssertionError("GenericIdentification30 SchmeNm must be non-empty")
        scheme_xml = (
            f"                        <SchmeNm>{scheme_name}</SchmeNm>\n"
            if scheme_name is not None
            else ""
        )
        proprietary_block = (
            "                    <AdrTp>\n"
            "                      <Prtry>\n"
            f"                        <Id>{identifier}</Id>\n"
            f"                        <Issr>{issuer}</Issr>\n"
            + scheme_xml
            + "                      </Prtry>\n"
            "                    </AdrTp>\n"
        ).encode("utf-8")
        return rendered.replace(coded_block, proprietary_block, 1)

    @staticmethod
    def _address_type_variants(
        base: dict[str, object],
    ) -> dict[str, dict[str, object]]:
        """Return proprietary value/presence changes plus a same-scalar Cd|Prtry choice."""
        variants: dict[str, dict[str, object]] = {}

        def proprietary(value: dict[str, object]) -> dict[str, object]:
            address_type = value.get("address_type")
            if not isinstance(address_type, dict):
                raise AssertionError("address-type variant requires AdrTp")
            nested = address_type.get("proprietary")
            if not isinstance(nested, dict):
                raise AssertionError("address-type variant requires proprietary branch")
            return nested

        changed_id = copy.deepcopy(base)
        proprietary(changed_id)["id"] = "HOME"
        variants["proprietary-id-value"] = changed_id

        changed_issuer = copy.deepcopy(base)
        proprietary(changed_issuer)["issuer"] = "Alternate Initiating Party Address Registry"
        variants["proprietary-issuer-value"] = changed_issuer

        changed_scheme = copy.deepcopy(base)
        proprietary(changed_scheme)["scheme_name"] = "ALT_PARTY_ADDR_TYPE"
        variants["proprietary-scheme-value"] = changed_scheme

        scheme_absent = copy.deepcopy(base)
        proprietary(scheme_absent).pop("scheme_name")
        variants["proprietary-scheme-absent"] = scheme_absent

        coded = copy.deepcopy(base)
        coded["address_type"] = {"code": "BIZZ"}
        variants["same-id-choice-discriminator"] = coded
        return variants


if __name__ == "__main__":
    import unittest

    unittest.main()
