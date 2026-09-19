"""Focused REDs for ultimate-agent proprietary AddressType3Choice evidence."""

from __future__ import annotations

import copy
import unittest
import uuid
from decimal import Decimal

from accounting_information_platform import AccountingValidationError, MemoryArtifactStore
from tests import (
    test_postgres_bank_statement_detail_ultimate_agent_postal_address_evidence_red as postal,
)


class BankStatementDetailUltimateAgentAddressTypeReviewRedTests(unittest.TestCase):
    """Close correction and privacy gaps for PostalAddress27.AdrTp.Prtry."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        postal.BankStatementDetailUltimateAgentPostalAddressEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare one isolated helper without inheriting its full test suite."""
        self.helper = postal.BankStatementDetailUltimateAgentPostalAddressEvidenceRedTests(
            "setUp"
        )
        self.addCleanup(self.helper.doCleanups)
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)

    def test_proprietary_address_type_variants_reach_correction_boundary(self) -> None:
        """Every material GenericIdentification30 variant reaches explicit correction."""
        for role, identity in self.helper.case.identities.items():
            for placement, source_address in self._placements():
                proprietary = self._proprietary_address(source_address)
                baseline_payload = self.helper._payload_for_variant(
                    role,
                    identity,
                    placement,
                    proprietary,
                )
                reference = self.helper.case._register_statement_account(baseline_payload)
                store = MemoryArtifactStore()
                accepted = postal.deep.accept_bank_statement_evidence(
                    self.helper.case._command(
                        baseline_payload,
                        reference,
                        f"{role}-{placement}-proprietary-address-type-baseline",
                    ),
                    postal.deep.posting.DATABASE_URL,
                    self.helper.case.case.policy.tenant_reference,
                    artifact_store=store,
                )
                self.assertFalse(accepted["replayed"])

                for semantic, changed_address in self._variants(proprietary).items():
                    with self.subTest(
                        role=role,
                        placement=placement,
                        semantic=semantic,
                    ):
                        changed_payload = self.helper._payload_for_variant(
                            role,
                            identity,
                            placement,
                            changed_address,
                        )
                        with self.assertRaisesRegex(
                            AccountingValidationError,
                            postal._CORRECTION_ERROR,
                        ):
                            postal.deep.accept_bank_statement_evidence(
                                self.helper.case._command(
                                    changed_payload,
                                    reference,
                                    (
                                        f"{role}-{placement}-proprietary-address-type-"
                                        f"{semantic}"
                                    ),
                                ),
                                postal.deep.posting.DATABASE_URL,
                                self.helper.case.case.policy.tenant_reference,
                                artifact_store=store,
                            )

    def test_proprietary_address_type_remains_non_reversible_in_buyer_projection(
        self,
    ) -> None:
        """Nested address-type source facts affect evidence without buyer disclosure."""
        for role, identity in self.helper.case.identities.items():
            for placement, source_address in self._placements():
                with self.subTest(role=role, placement=placement):
                    proprietary = self._proprietary_address(source_address)
                    rich_payload = self.helper._payload_for_variant(
                        role,
                        identity,
                        placement,
                        proprietary,
                    )
                    absent_payload = self.helper._payload(
                        role,
                        identity,
                        institution_address=None,
                        branch_address=None,
                    )
                    rich = self.helper._ingest_and_read_first_detail(
                        rich_payload,
                        f"{role}-{placement}-proprietary-address-type-rich-{uuid.uuid4().hex}",
                    )
                    absent = self.helper._ingest_and_read_first_detail(
                        absent_payload,
                        f"{role}-{placement}-proprietary-address-type-absent-{uuid.uuid4().hex}",
                    )
                    evidence_key = self.helper.case._evidence_key(role)
                    for projection in (rich, absent):
                        self.helper._assert_sha256(projection[evidence_key])
                        self.helper._assert_sha256(projection["source_detail_hash"])
                        self.assertEqual(
                            Decimal(str(projection["detail_amount"])),
                            Decimal("25000.00"),
                        )
                        self.assertEqual(projection["detail_currency_code"], "KRW")

                    self.assertNotEqual(rich[evidence_key], absent[evidence_key])
                    self.assertNotEqual(
                        rich["source_detail_hash"],
                        absent["source_detail_hash"],
                    )
                    rich_public = self.helper._public_projection(rich, evidence_key)
                    absent_public = self.helper._public_projection(absent, evidence_key)
                    self.assertEqual(rich_public, absent_public)

                    buyer_values = set(self.helper._scalar_leaves(rich_public))
                    source_values = self.helper._privacy_source_values(proprietary)
                    source_values.update(
                        {
                            "Ultimate Address Registry",
                            "ADDR-TYPE",
                        }
                    )
                    self.assertTrue(source_values.isdisjoint(buyer_values))

    @staticmethod
    def _placements() -> tuple[tuple[str, dict[str, object]], ...]:
        """Return both PostalAddress27 placements owned by the ultimate-agent role."""
        return (
            ("institution", postal._INSTITUTION_ADDRESS),
            ("branch", postal._BRANCH_ADDRESS),
        )

    @staticmethod
    def _proprietary_address(address: dict[str, object]) -> dict[str, object]:
        """Replace coded AdrTp with a complete GenericIdentification30 branch."""
        result = copy.deepcopy(address)
        result["address_type"] = {
            "proprietary": {
                "id": "BIZZ",
                "issuer": "Ultimate Address Registry",
                "scheme_name": "ADDR-TYPE",
            }
        }
        return result

    @staticmethod
    def _proprietary_node(address: dict[str, object]) -> dict[str, object]:
        """Return AdrTp/Prtry or fail closed when the focused fixture drifts."""
        address_type = address.get("address_type")
        if not isinstance(address_type, dict):
            raise AssertionError("focused address fixture requires AdrTp")
        proprietary = address_type.get("proprietary")
        if not isinstance(proprietary, dict):
            raise AssertionError("focused address fixture requires AdrTp/Prtry")
        return proprietary

    @classmethod
    def _variants(
        cls,
        proprietary: dict[str, object],
    ) -> dict[str, dict[str, object]]:
        """Change every GenericIdentification30 fact and the choice discriminator."""
        variants: dict[str, dict[str, object]] = {}

        changed_id = copy.deepcopy(proprietary)
        cls._proprietary_node(changed_id)["id"] = "HOME"
        variants["proprietary-id-value"] = changed_id

        changed_issuer = copy.deepcopy(proprietary)
        cls._proprietary_node(changed_issuer)["issuer"] = "Alternate Address Registry"
        variants["proprietary-issuer-value"] = changed_issuer

        changed_scheme = copy.deepcopy(proprietary)
        cls._proprietary_node(changed_scheme)["scheme_name"] = "ALT-TYPE"
        variants["proprietary-scheme-value"] = changed_scheme

        scheme_absent = copy.deepcopy(proprietary)
        cls._proprietary_node(scheme_absent).pop("scheme_name")
        variants["proprietary-scheme-absent"] = scheme_absent

        coded_same_id = copy.deepcopy(proprietary)
        coded_same_id["address_type"] = {"code": "BIZZ"}
        variants["same-id-choice-discriminator"] = coded_same_id
        return variants


if __name__ == "__main__":
    unittest.main()
