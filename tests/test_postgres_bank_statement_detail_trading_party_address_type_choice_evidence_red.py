"""PostgreSQL REDs for PartyIdentification272 PostalAddress27 address-type choice evidence."""

from __future__ import annotations

import copy

from tests import (
    test_postgres_bank_statement_detail_trading_party_postal_address_evidence_red as party_postal,
)


class BankStatementDetailTradingPartyAddressTypeChoiceEvidenceRedTests(
    party_postal.BankStatementDetailTradingPartyPostalAddressEvidenceRedTests
):
    """Retain party AddressType3Choice semantics without changing accounting truth."""

    @staticmethod
    def _postal_address() -> dict[str, object]:
        """Use a complete party address with the proprietary AddressType3Choice branch."""
        address = (
            party_postal.BankStatementDetailTradingPartyPostalAddressEvidenceRedTests
            ._postal_address()
        )
        address["address_type"] = {
            "proprietary": {
                "id": "BIZZ",
                "issuer": "CWL Address Registry",
                "scheme_name": "PARTY_ADDR_TYPE",
            }
        }
        return address

    @classmethod
    def _postal_variants(cls, base: dict[str, object]) -> dict[str, dict[str, object]]:
        """Add proprietary-field and Cd/Prtry variants to the full postal field matrix."""
        variants = super()._postal_variants(base)

        for field, replacement in (
            ("id", "CSTM"),
            ("issuer", "Alternate Address Registry"),
            ("scheme_name", "ALT_PARTY_ADDR_TYPE"),
        ):
            variant = copy.deepcopy(base)
            postal = variant.get("postal_address")
            if not isinstance(postal, dict):
                raise AssertionError("address-type variant requires PostalAddress27")
            address_type = postal.get("address_type")
            if not isinstance(address_type, dict):
                raise AssertionError("address-type variant requires AddressType3Choice")
            proprietary = address_type.get("proprietary")
            if not isinstance(proprietary, dict):
                raise AssertionError("address-type variant requires proprietary identification")
            proprietary[field] = replacement
            variants[f"postal-address-type-proprietary-{field}"] = variant

        without_scheme = copy.deepcopy(base)
        postal = without_scheme.get("postal_address")
        if not isinstance(postal, dict):
            raise AssertionError("scheme-absence variant requires PostalAddress27")
        address_type = postal.get("address_type")
        if not isinstance(address_type, dict):
            raise AssertionError("scheme-absence variant requires AddressType3Choice")
        proprietary = address_type.get("proprietary")
        if not isinstance(proprietary, dict):
            raise AssertionError("scheme-absence variant requires proprietary identification")
        proprietary.pop("scheme_name")
        variants["postal-address-type-proprietary-scheme-absent"] = without_scheme

        same_identification_code = copy.deepcopy(base)
        postal = same_identification_code.get("postal_address")
        if not isinstance(postal, dict):
            raise AssertionError("choice variant requires PostalAddress27")
        postal["address_type"] = {"code": "BIZZ"}
        variants["postal-address-type-choice-code-same-identification"] = (
            same_identification_code
        )
        return variants

    @staticmethod
    def _postal_xml(address: dict[str, object], indent: str) -> str:
        """Serialize either valid AddressType3Choice branch before remaining postal fields."""
        address_type = address.get("address_type")
        if not isinstance(address_type, dict) or len(address_type) != 1:
            raise AssertionError("AdrTp must contain exactly one AddressType3Choice branch")

        parent = (
            party_postal.BankStatementDetailTradingPartyPostalAddressEvidenceRedTests
        )
        if "code" in address_type:
            return parent._postal_xml(address, indent)
        if "proprietary" not in address_type:
            raise AssertionError("unsupported AddressType3Choice branch")

        proprietary = address_type["proprietary"]
        if not isinstance(proprietary, dict):
            raise AssertionError("proprietary AdrTp must be structured")
        if "id" not in proprietary or "issuer" not in proprietary:
            raise AssertionError("GenericIdentification30 requires Id and Issr")

        coded_shadow = copy.deepcopy(address)
        coded_shadow["address_type"] = {"code": "BIZZ"}
        rendered = parent._postal_xml(coded_shadow, indent)
        coded_block = (
            f"{indent}  <AdrTp>\n"
            f"{indent}    <Cd>BIZZ</Cd>\n"
            f"{indent}  </AdrTp>\n"
        )
        scheme_name = proprietary.get("scheme_name")
        scheme_xml = (
            f"{indent}      <SchmeNm>{scheme_name}</SchmeNm>\n"
            if scheme_name is not None
            else ""
        )
        proprietary_block = (
            f"{indent}  <AdrTp>\n"
            f"{indent}    <Prtry>\n"
            f"{indent}      <Id>{proprietary['id']}</Id>\n"
            f"{indent}      <Issr>{proprietary['issuer']}</Issr>\n"
            + scheme_xml
            + f"{indent}    </Prtry>\n"
            f"{indent}  </AdrTp>\n"
        )
        if rendered.count(coded_block) != 1:
            raise AssertionError("coded shadow must contain exactly one AdrTp block")
        return rendered.replace(coded_block, proprietary_block, 1)
