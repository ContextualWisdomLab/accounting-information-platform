"""PostgreSQL REDs for complete camt.053 TaxData1 party-field materiality."""

from __future__ import annotations

import unittest

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    accept_bank_statement_evidence,
    parse_bank_statement_payload,
)
from tests.test_postgres_bank_statement_detail_tax_party_evidence_red import (
    BankStatementDetailTaxPartyEvidenceRedTests as TaxPartyRed,
    _CORRECTION_ERROR,
)
from tests.test_postgres_bank_statement_detail_tax_evidence_red import (
    BankStatementDetailTaxEvidenceRedTests as TaxEvidenceRed,
)


class BankStatementDetailTaxPartyFieldMaterialityRedTests(unittest.TestCase):
    """Require every admitted TaxParty1/TaxParty2 field to affect evidence identity."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        TaxPartyRed.setUpClass()

    def setUp(self) -> None:
        """Build the existing schema-valid TaxData1 party fixture."""
        self.fixture = TaxPartyRed("test_tax_parties_are_independently_material_evidence")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.addCleanup(self.fixture.tearDown)

    def test_every_remaining_tax_party_field_is_independently_material(self) -> None:
        """No admitted TaxParty field may be silently omitted from canonical identity."""
        base = self.fixture.base_statement
        base_detail = base.entries[0].entry_details[0]
        variants = self._variants()

        expected_hashes = {self.fixture._expected_hash(self.fixture.base_parties)}
        for label, parties, payload in variants:
            with self.subTest(field=label):
                statement = parse_bank_statement_payload(
                    payload, CAMT053_MESSAGE_DEFINITION
                )
                expected_hash = self.fixture._expected_hash(parties)
                detail = statement.entries[0].entry_details[0]

                self.assertNotIn(expected_hash, expected_hashes)
                expected_hashes.add(expected_hash)
                self.assertEqual(
                    getattr(detail, "detail_tax_evidence_hash", None), expected_hash
                )
                TaxEvidenceRed._assert_entry_hash_binding(
                    statement.entries[0], expected_hash
                )
                self.assertEqual(
                    base.account_identifier_hash, statement.account_identifier_hash
                )
                self.assertEqual(
                    base.entries[0].entry_amount, statement.entries[0].entry_amount
                )
                self.assertEqual(base_detail.detail_amount, detail.detail_amount)
                self.assertNotEqual(base_detail.source_detail_hash, detail.source_detail_hash)
                self.assertNotEqual(
                    base.entries[0].source_entry_hash,
                    statement.entries[0].source_entry_hash,
                )
                self.assertNotEqual(
                    base.normalized_payload_hash, statement.normalized_payload_hash
                )
                self.assertEqual(
                    base.entries[1].source_entry_hash,
                    statement.entries[1].source_entry_hash,
                )

    def test_every_remaining_tax_party_field_requires_explicit_correction(self) -> None:
        """Each omitted-field candidate must reach the existing correction boundary."""
        accepted = accept_bank_statement_evidence(
            self.fixture._command(self.fixture.base_payload, "all-party-fields-base"),
            self.fixture.case.DATABASE_URL
            if hasattr(self.fixture.case, "DATABASE_URL")
            else __import__("tests.test_postgres_posting", fromlist=["DATABASE_URL"]).DATABASE_URL,
            self.fixture.case.policy.tenant_reference,
            artifact_store=self.fixture.store,
        )
        self.assertFalse(accepted["replayed"])

        database_url = __import__(
            "tests.test_postgres_posting", fromlist=["DATABASE_URL"]
        ).DATABASE_URL
        for label, _parties, payload in self._variants():
            with self.subTest(field=label):
                with self.assertRaisesRegex(
                    AccountingValidationError, _CORRECTION_ERROR
                ):
                    accept_bank_statement_evidence(
                        self.fixture._command(payload, f"party-{label}"),
                        database_url,
                        self.fixture.case.policy.tenant_reference,
                        artifact_store=self.fixture.store,
                    )

    def _variants(self) -> tuple[tuple[str, dict[str, object], bytes], ...]:
        """Vary only fields not independently exercised by the predecessor RED."""
        cases = (
            ("creditor-registration-id", "creditor", "registration_id", "KR-REG-CRED-002", False),
            ("creditor-tax-type", "creditor", "tax_type", "GST", False),
            ("debtor-tax-id", "debtor", "tax_id", "KR-TAX-DEBT-002", False),
            ("debtor-registration-id", "debtor", "registration_id", "KR-REG-DEBT-002", False),
            ("debtor-tax-type", "debtor", "tax_type", "GST", False),
            ("ultimate-debtor-tax-id", "ultimate_debtor", "tax_id", "KR-TAX-ULT-002", False),
            ("ultimate-debtor-tax-type", "ultimate_debtor", "tax_type", "GST", False),
            ("ultimate-debtor-authorisation-title", "ultimate_debtor", "title", "Senior ultimate tax agent", True),
            ("ultimate-debtor-authorisation-name", "ultimate_debtor", "name", "Ultimate Tax Representative Revised", True),
        )
        variants: list[tuple[str, dict[str, object], bytes]] = []
        for label, party_name, field_name, value, authorisation in cases:
            if authorisation:
                parties = self.fixture._changed_authorisation(
                    party_name, field_name, value
                )
            else:
                parties = self.fixture._changed_party(party_name, field_name, value)
            payload = self._replace_single_field(
                self.fixture.base_payload,
                party_name,
                field_name,
                value,
            )
            variants.append((label, parties, payload))
        return tuple(variants)

    def _replace_single_field(
        self, payload: bytes, party_name: str, field_name: str, value: str
    ) -> bytes:
        """Change exactly one XML scalar in the schema-valid base payload."""
        party_tags = {
            "creditor": "Cdtr",
            "debtor": "Dbtr",
            "ultimate_debtor": "UltmtDbtr",
        }
        field_tags = {
            "tax_id": "TaxId",
            "registration_id": "RegnId",
            "tax_type": "TaxTp",
            "title": "Titl",
            "name": "Nm",
        }
        party = self.fixture.base_parties[party_name]
        if not isinstance(party, dict):
            raise AssertionError("base tax party must be a mapping")
        if field_name in {"title", "name"}:
            authorisation = party.get("authorisation")
            if not isinstance(authorisation, dict):
                raise AssertionError("authorised tax party must have authorisation")
            old_value = str(authorisation[field_name])
        else:
            old_value = str(party[field_name])

        party_tag = party_tags[party_name]
        field_tag = field_tags[field_name]
        start = payload.index(f"            <{party_tag}>".encode("utf-8"))
        end = payload.index(
            f"            </{party_tag}>".encode("utf-8"), start
        )
        segment = payload[start:end]
        old = f"<{field_tag}>{old_value}</{field_tag}>".encode("utf-8")
        new = f"<{field_tag}>{value}</{field_tag}>".encode("utf-8")
        if segment.count(old) != 1:
            raise AssertionError(f"expected one {party_name}/{field_name} scalar")
        changed_segment = segment.replace(old, new, 1)
        return payload[:start] + changed_segment + payload[end:]
