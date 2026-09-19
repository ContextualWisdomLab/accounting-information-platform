"""Review RED for complete ultimate-party postal buyer non-reversibility."""

from __future__ import annotations

import copy
import unittest
import uuid
from decimal import Decimal

from tests import (
    test_postgres_bank_statement_detail_ultimate_party_postal_address_evidence_red
    as ultimate_postal,
)


class BankStatementDetailUltimatePartyPostalAddressPrivacyReviewRedTests(
    unittest.TestCase
):
    """Close false-GREEN paths in the ultimate-party postal privacy oracle."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        (
            ultimate_postal.BankStatementDetailUltimatePartyPostalAddressEvidenceRedTests
            .setUpClass()
        )

    def setUp(self) -> None:
        """Prepare one nested ultimate-party postal fixture with safe cleanup."""
        self.case = (
            ultimate_postal.BankStatementDetailUltimatePartyPostalAddressEvidenceRedTests(
                "setUp"
            )
        )
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def test_every_postal_source_value_stays_non_reversible_for_each_role(
        self,
    ) -> None:
        """Reject exact buyer disclosure of every admitted rich postal source value."""
        rich_postal = copy.deepcopy(self.case.base_postal)
        rich_postal.update(
            {
                "address_type": {"code": "ADDR"},
                "care_of": "Ultimate Confidential Treasury Recipient",
                "department": "Ultimate Restricted Treasury Department",
                "sub_department": "Ultimate Restricted Reconciliation Unit",
                "street_name": "Ultimate Sensitive Party Street",
                "building_number": "77",
                "building_name": "Ultimate Sensitive Party Tower",
                "floor": "91",
                "unit_number": "1901",
                "post_box": "ULT-PBOX-7788",
                "room": "Ultimate Private Treasury Room",
                "post_code": "04567",
                "town_name": "Busan",
                "town_location_name": "Jungang-dong",
                "district_name": "Jung-gu",
                "country_subdivision": "26",
                "country": "DE",
                "address_lines": [
                    "Ultimate Sensitive Party Line One",
                    "Ultimate Sensitive Party Line Two",
                ],
            }
        )
        source_values = self._postal_source_values(rich_postal)

        for role in ("ultimate_debtor", "ultimate_creditor"):
            with self.subTest(role=role):
                baseline = self.case.case._ingest_and_read_first_detail(
                    self.case._with_role_postal(role, None),
                    f"{role}-postal-privacy-none-{uuid.uuid4().hex}",
                )
                rich = self.case.case._ingest_and_read_first_detail(
                    self.case._with_role_postal(role, rich_postal),
                    f"{role}-postal-privacy-rich-{uuid.uuid4().hex}",
                )
                evidence_key = self.case.case._evidence_key(role)

                for projection in (baseline, rich):
                    self.case.case._assert_sha256(projection[evidence_key])
                    self.case.case._assert_sha256(projection["source_detail_hash"])
                    self.assertEqual(
                        Decimal(str(projection["detail_amount"])),
                        Decimal("25000.00"),
                    )
                    self.assertEqual(projection["detail_currency_code"], "KRW")

                self.assertNotEqual(
                    baseline[evidence_key],
                    rich[evidence_key],
                )
                self.assertNotEqual(
                    baseline["source_detail_hash"],
                    rich["source_detail_hash"],
                )

                baseline_public = self.case.case._public_projection(
                    baseline,
                    evidence_key,
                )
                rich_public = self.case.case._public_projection(
                    rich,
                    evidence_key,
                )
                self.assertEqual(baseline_public, rich_public)

                buyer_scalars = self._scalar_values(rich_public)
                for source_value in source_values:
                    with self.subTest(role=role, source_value=source_value):
                        self.assertNotIn(source_value, buyer_scalars)

    @staticmethod
    def _postal_source_values(postal: dict[str, object]) -> set[str]:
        """Return every admitted postal scalar as an exact source string."""
        values: set[str] = set()
        for field, value in postal.items():
            if field == "address_type":
                if not isinstance(value, dict) or set(value) != {"code"}:
                    raise AssertionError(
                        "privacy RED requires coded AddressType3Choice"
                    )
                code = value["code"]
                if not isinstance(code, str):
                    raise AssertionError("postal address type code must be a string")
                values.add(code)
                continue
            if field == "address_lines":
                if not isinstance(value, list):
                    raise AssertionError("postal address_lines must be a list")
                for line in value:
                    if not isinstance(line, str):
                        raise AssertionError("postal AdrLine must be a string")
                    values.add(line)
                continue
            if not isinstance(value, str):
                raise AssertionError(f"postal scalar must be a string: {field}")
            values.add(value)
        return values

    @classmethod
    def _scalar_values(cls, value: object) -> set[str]:
        """Collect exact buyer-visible scalar strings without substring heuristics."""
        if isinstance(value, dict):
            collected: set[str] = set()
            for nested in value.values():
                collected.update(cls._scalar_values(nested))
            return collected
        if isinstance(value, list):
            collected = set()
            for nested in value:
                collected.update(cls._scalar_values(nested))
            return collected
        if value is None:
            return set()
        return {str(value)}


if __name__ == "__main__":
    unittest.main()
