"""PostgreSQL REDs for direct-party PostalAddress27 address-type choice evidence."""

from __future__ import annotations

import copy
import json
import uuid

from tests import (
    test_postgres_bank_statement_debtor_creditor_party_postal_address_evidence_red as postal,
)

_BASE_PROPRIETARY = {
    "id": "BIZZ",
    "issuer": "Direct Party Address Registry",
    "scheme_name": "PARTY_ADDR_TYPE",
}


class BankStatementDebtorCreditorPartyAddressTypeChoiceEvidenceRedTests(
    postal.BankStatementDebtorCreditorPartyPostalAddressEvidenceRedTests
):
    """Retain direct-party AdrTp Cd|Prtry semantics without changing accounting truth."""

    def setUp(self) -> None:
        """Prepare proprietary AddressType3Choice variants for both direct party roles."""
        super().setUp()
        self.base_postal = copy.deepcopy(self.base_postal)
        self.base_postal["address_type"] = {
            "proprietary": copy.deepcopy(_BASE_PROPRIETARY)
        }
        self.variants = self._address_type_variants(self.base_postal)

    def test_proprietary_address_type_layout_is_representation_only_for_both_roles(
        self,
    ) -> None:
        """Whitespace inside GenericIdentification30 changes bytes without semantic drift."""
        needle = b"                      <Prtry>\n"
        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                baseline_payload = self._with_role_postal(role, self.base_postal)
                if baseline_payload.count(needle) != 1:
                    raise AssertionError("direct-party proprietary AdrTp must be unique")
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
                target_index = self.case._target_entry_index(role)
                untouched_index = 1 - target_index
                baseline_entry = baseline.entries[target_index]
                formatted_entry = formatted.entries[target_index]
                baseline_detail = baseline_entry.entry_details[0]
                formatted_detail = formatted_entry.entry_details[0]
                self._assert_financial_truth(role, baseline_entry, baseline_detail)
                self._assert_financial_truth(role, formatted_entry, formatted_detail)

                for value in (
                    baseline.source_artifact_hash,
                    formatted.source_artifact_hash,
                    baseline_entry.counterparty_evidence_hash,
                    formatted_entry.counterparty_evidence_hash,
                    baseline_detail.source_detail_hash,
                    formatted_detail.source_detail_hash,
                    baseline_entry.source_entry_hash,
                    formatted_entry.source_entry_hash,
                    baseline.normalized_payload_hash,
                    formatted.normalized_payload_hash,
                    baseline.account_identifier_hash,
                    formatted.account_identifier_hash,
                    baseline.entries[untouched_index].source_entry_hash,
                    formatted.entries[untouched_index].source_entry_hash,
                ):
                    self.case._assert_sha256(value)

                self.assertNotEqual(
                    baseline.source_artifact_hash,
                    formatted.source_artifact_hash,
                )
                self.assertEqual(
                    baseline_entry.counterparty_evidence_hash,
                    formatted_entry.counterparty_evidence_hash,
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
                    baseline.entries[untouched_index].source_entry_hash,
                    formatted.entries[untouched_index].source_entry_hash,
                )

    def test_proprietary_address_type_registry_values_are_not_buyer_reversible(self) -> None:
        """Proprietary registry metadata changes evidence without appearing in tenant reads."""
        coded = copy.deepcopy(self.base_postal)
        coded["address_type"] = {"code": "BIZZ"}

        for role in ("debtor", "creditor"):
            with self.subTest(role=role):
                rich = self.case._ingest_and_read_target_entry(
                    role,
                    self._with_role_postal(role, self.base_postal),
                    f"{role}-address-type-rich-{uuid.uuid4().hex}",
                )
                coded_entry = self.case._ingest_and_read_target_entry(
                    role,
                    self._with_role_postal(role, coded),
                    f"{role}-address-type-coded-{uuid.uuid4().hex}",
                )
                for projection in (rich, coded_entry):
                    self.case._assert_sha256(
                        projection["counterparty_evidence_hash"]
                    )
                    self.case._assert_sha256(projection["source_entry_hash"])
                    detail = projection["entry_details"][0]
                    self.case._assert_sha256(detail["source_detail_hash"])

                self.assertNotEqual(
                    rich["counterparty_evidence_hash"],
                    coded_entry["counterparty_evidence_hash"],
                )
                self.assertNotEqual(
                    rich["entry_details"][0]["source_detail_hash"],
                    coded_entry["entry_details"][0]["source_detail_hash"],
                )
                self.assertNotEqual(
                    rich["source_entry_hash"],
                    coded_entry["source_entry_hash"],
                )
                self.assertEqual(
                    self._public_projection(rich),
                    self._public_projection(coded_entry),
                )

                serialized = json.dumps(rich, sort_keys=True, default=str)
                self.assertNotIn(_BASE_PROPRIETARY["issuer"], serialized)
                self.assertNotIn(_BASE_PROPRIETARY["scheme_name"], serialized)

    @staticmethod
    def _postal_xml(address: dict[str, object]) -> bytes:
        """Serialize PostalAddress27 while preserving the AddressType3Choice branch."""
        address_type = address.get("address_type")
        if not isinstance(address_type, dict) or len(address_type) != 1:
            raise AssertionError("AdrTp must contain exactly one AddressType3Choice branch")
        if "code" in address_type:
            return postal.BankStatementDebtorCreditorPartyPostalAddressEvidenceRedTests._postal_xml(
                address
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
            postal.BankStatementDebtorCreditorPartyPostalAddressEvidenceRedTests._postal_xml(
                coded_shadow
            )
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
        """Return proprietary value/presence changes plus same-scalar choice semantics."""
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
        proprietary(changed_issuer)["issuer"] = "Alternate Direct Party Address Registry"
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
