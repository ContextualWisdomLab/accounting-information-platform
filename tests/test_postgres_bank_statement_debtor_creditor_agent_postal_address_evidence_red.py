"""PostgreSQL REDs for debtor/creditor-agent PostalAddress27 evidence."""

from __future__ import annotations

import copy
import unittest
import uuid

from accounting_information_platform import AccountingValidationError, MemoryArtifactStore
from tests import (
    test_postgres_bank_statement_debtor_creditor_agent_deep_identity_evidence_red as deep,
)

_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)

_INSTITUTION_ADDRESS = {
    "street_name": "Counterparty Institution Street",
    "building_number": "10",
    "post_code": "10000",
    "town_name": "Counterparty City",
    "country": "DE",
    "address_lines": [
        "Counterparty Institution Street 10",
        "10000 Counterparty City",
    ],
}

_BRANCH_ADDRESS = {
    "street_name": "Counterparty Branch Street",
    "building_number": "20",
    "post_code": "20000",
    "town_name": "Counterparty Branch City",
    "country": "DE",
    "address_lines": [
        "Counterparty Branch Street 20",
        "20000 Counterparty Branch City",
    ],
}


class BankStatementDebtorCreditorAgentPostalAddressEvidenceRedTests(unittest.TestCase):
    """Retain institution and branch PostalAddress27 for DbtrAgt and CdtrAgt."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the source-real PostgreSQL and camt.053 integration fixture."""
        deep.BankStatementDebtorCreditorAgentDeepIdentityEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare the focused debtor/creditor helper with exception-safe cleanup."""
        self.case = deep.BankStatementDebtorCreditorAgentDeepIdentityEvidenceRedTests(
            "setUp"
        )
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def test_postal_address_is_material_for_debtor_and_creditor_agent(self) -> None:
        """Institution and branch postal facts change canonical role evidence."""
        for role, identity in self.case.AGENTS.items():
            with self.subTest(role=role):
                base_payload = self._payload(
                    role,
                    institution_address=_INSTITUTION_ADDRESS,
                    branch_address=_BRANCH_ADDRESS,
                )
                base = self.case._statement(base_payload)
                base_entry = base.entries[0]
                base_detail = base_entry.entry_details[0]
                digest_key = identity["digest_key"]
                base_digest = getattr(base_detail, digest_key, None)
                self.case._assert_digest(base_digest)
                self.case._assert_digest(base_detail.source_detail_hash)
                self.case._assert_exact_amount(base_entry, base_detail)

                for variant_name, payload in self._variants(role).items():
                    with self.subTest(role=role, variant=variant_name):
                        statement = self.case._statement(payload)
                        entry = statement.entries[0]
                        detail = entry.entry_details[0]
                        digest = getattr(detail, digest_key, None)
                        self.case._assert_digest(digest)
                        self.case._assert_digest(detail.source_detail_hash)
                        self.assertNotEqual(base_digest, digest)
                        self.assertEqual(
                            base.account_identifier_hash,
                            statement.account_identifier_hash,
                        )
                        self.assertNotEqual(
                            base_detail.source_detail_hash,
                            detail.source_detail_hash,
                        )
                        self.assertNotEqual(
                            base_entry.source_entry_hash,
                            entry.source_entry_hash,
                        )
                        self.assertNotEqual(
                            base.normalized_payload_hash,
                            statement.normalized_payload_hash,
                        )
                        self.assertEqual(
                            base.entries[1].source_entry_hash,
                            statement.entries[1].source_entry_hash,
                        )
                        self.case._assert_exact_amount(entry, detail)

    def test_postal_layout_is_representation_only_for_both_roles(self) -> None:
        """Whitespace in either PstlAdr changes raw bytes without semantic drift."""
        needle = b"                  <PstlAdr>\n"
        whitespace = b"                    \n"

        for role in self.case.AGENTS:
            payload = self._payload(
                role,
                institution_address=_INSTITUTION_ADDRESS,
                branch_address=_BRANCH_ADDRESS,
            )
            occurrences = [
                index
                for index in range(len(payload))
                if payload.startswith(needle, index)
            ]
            self.assertEqual(len(occurrences), 2)

            for placement, target_index in (
                ("institution", occurrences[0]),
                ("branch", occurrences[1]),
            ):
                with self.subTest(role=role, placement=placement):
                    changed_payload = (
                        payload[: target_index + len(needle)]
                        + whitespace
                        + payload[target_index + len(needle) :]
                    )
                    base = self.case._statement(payload)
                    changed = self.case._statement(changed_payload)
                    base_entry = base.entries[0]
                    changed_entry = changed.entries[0]
                    base_detail = base_entry.entry_details[0]
                    changed_detail = changed_entry.entry_details[0]
                    digest_key = self.case.AGENTS[role]["digest_key"]
                    base_digest = getattr(base_detail, digest_key, None)
                    changed_digest = getattr(changed_detail, digest_key, None)
                    self.case._assert_digest(base_digest)
                    self.case._assert_digest(changed_digest)
                    self.case._assert_digest(base_detail.source_detail_hash)
                    self.case._assert_digest(changed_detail.source_detail_hash)

                    self.assertNotEqual(
                        base.source_artifact_hash,
                        changed.source_artifact_hash,
                    )
                    self.assertEqual(base_digest, changed_digest)
                    self.assertEqual(
                        base_detail.source_detail_hash,
                        changed_detail.source_detail_hash,
                    )
                    self.assertEqual(
                        base_entry.source_entry_hash,
                        changed_entry.source_entry_hash,
                    )
                    self.assertEqual(
                        base.normalized_payload_hash,
                        changed.normalized_payload_hash,
                    )
                    self.case._assert_exact_amount(changed_entry, changed_detail)

    def test_postal_change_requires_explicit_correction(self) -> None:
        """Accepted debtor/creditor postal evidence cannot be replaced on replay."""
        role = "CdtrAgt"
        base_payload = self._payload(
            role,
            institution_address=_INSTITUTION_ADDRESS,
            branch_address=_BRANCH_ADDRESS,
        )
        changed_address = copy.deepcopy(_BRANCH_ADDRESS)
        changed_address["street_name"] = "Changed Counterparty Branch Street"
        changed_payload = self._payload(
            role,
            institution_address=_INSTITUTION_ADDRESS,
            branch_address=changed_address,
        )
        statement = self.case._statement(base_payload)
        account_reference = self.case._register_account(statement, "postal-correction")
        store = MemoryArtifactStore()
        accepted = deep.accept_bank_statement_evidence(
            self.case._command(base_payload, account_reference, "postal-base"),
            deep.posting.DATABASE_URL,
            self.case.case.policy.tenant_reference,
            artifact_store=store,
        )
        self.assertFalse(accepted["replayed"])
        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            deep.accept_bank_statement_evidence(
                self.case._command(
                    changed_payload,
                    account_reference,
                    "postal-changed",
                ),
                deep.posting.DATABASE_URL,
                self.case.case.policy.tenant_reference,
                artifact_store=store,
            )

    def test_postal_evidence_stays_digest_only_on_buyer_projection(self) -> None:
        """Buyer reads add no reversible debtor/creditor postal fields."""
        for role, identity in self.case.AGENTS.items():
            with self.subTest(role=role):
                rich_payload = self._payload(
                    role,
                    institution_address=_INSTITUTION_ADDRESS,
                    branch_address=_BRANCH_ADDRESS,
                )
                no_postal_payload = self._payload(
                    role,
                    institution_address=None,
                    branch_address=None,
                )
                rich_statement = self.case._statement(rich_payload)
                no_postal_statement = self.case._statement(no_postal_payload)
                rich_reference = self.case._register_account(
                    rich_statement,
                    f"postal-rich-{role.lower()}",
                )
                no_postal_reference = self.case._register_account(
                    no_postal_statement,
                    f"postal-none-{role.lower()}",
                )
                rich_detail = self.case._ingest_and_read(
                    rich_payload,
                    rich_reference,
                    f"postal-rich-{role.lower()}-{uuid.uuid4().hex}",
                )
                no_postal_detail = self.case._ingest_and_read(
                    no_postal_payload,
                    no_postal_reference,
                    f"postal-none-{role.lower()}-{uuid.uuid4().hex}",
                )
                digest_key = identity["digest_key"]

                for projection in (rich_detail, no_postal_detail):
                    self.case._assert_digest(projection.get(digest_key))
                    self.case._assert_digest(projection.get("source_detail_hash"))
                    self.assertEqual(projection["detail_amount"], "25000")
                    self.assertEqual(projection["detail_currency_code"], "KRW")

                self.assertNotEqual(rich_detail[digest_key], no_postal_detail[digest_key])
                self.assertNotEqual(
                    rich_detail["source_detail_hash"],
                    no_postal_detail["source_detail_hash"],
                )
                rich_visible = dict(rich_detail)
                no_postal_visible = dict(no_postal_detail)
                for projection in (rich_visible, no_postal_visible):
                    projection.pop(digest_key)
                    projection.pop("source_detail_hash")
                self.assertEqual(rich_visible, no_postal_visible)

    def _variants(self, role: str) -> dict[str, bytes]:
        """Create material postal changes and whole-address absence variants."""
        institution_street = copy.deepcopy(_INSTITUTION_ADDRESS)
        institution_street["street_name"] = "Alternate Counterparty Institution Street"
        institution_line = copy.deepcopy(_INSTITUTION_ADDRESS)
        institution_line["address_lines"][1] = "10001 Alternate Counterparty City"
        branch_street = copy.deepcopy(_BRANCH_ADDRESS)
        branch_street["street_name"] = "Alternate Counterparty Branch Street"
        branch_lines_reordered = copy.deepcopy(_BRANCH_ADDRESS)
        branch_lines_reordered["address_lines"] = list(
            reversed(branch_lines_reordered["address_lines"])
        )
        return {
            "institution-street": self._payload(
                role,
                institution_address=institution_street,
                branch_address=_BRANCH_ADDRESS,
            ),
            "institution-address-line": self._payload(
                role,
                institution_address=institution_line,
                branch_address=_BRANCH_ADDRESS,
            ),
            "institution-address-absent": self._payload(
                role,
                institution_address=None,
                branch_address=_BRANCH_ADDRESS,
            ),
            "branch-street": self._payload(
                role,
                institution_address=_INSTITUTION_ADDRESS,
                branch_address=branch_street,
            ),
            "branch-address-line-order": self._payload(
                role,
                institution_address=_INSTITUTION_ADDRESS,
                branch_address=branch_lines_reordered,
            ),
            "branch-address-absent": self._payload(
                role,
                institution_address=_INSTITUTION_ADDRESS,
                branch_address=None,
            ),
        }

    def _payload(
        self,
        role: str,
        *,
        institution_address: dict[str, object] | None,
        branch_address: dict[str, object] | None,
    ) -> bytes:
        """Insert institution and branch PostalAddress27 into the target role."""
        identity = self.case.AGENTS[role]
        payload = self.case._with_agent(role, identity)

        if institution_address is not None:
            institution_needle = (
                f"                  <Nm>{identity['name']}</Nm>\n"
            ).encode("utf-8")
            if payload.count(institution_needle) != 1:
                raise AssertionError("target debtor/creditor institution name must be unique")
            payload = payload.replace(
                institution_needle,
                institution_needle + self._address_xml(institution_address),
                1,
            )

        if branch_address is not None:
            branch_needle = (
                f"                  <Nm>{identity['branch_name']}</Nm>\n"
            ).encode("utf-8")
            if payload.count(branch_needle) != 1:
                raise AssertionError("target debtor/creditor branch name must be unique")
            payload = payload.replace(
                branch_needle,
                branch_needle + self._address_xml(branch_address),
                1,
            )

        return payload

    @staticmethod
    def _address_xml(address: dict[str, object]) -> bytes:
        """Serialize the focused PostalAddress27 subset in schema sequence order."""
        lines = ["                  <PstlAdr>\n"]
        for field, tag in (
            ("street_name", "StrtNm"),
            ("building_number", "BldgNb"),
            ("post_code", "PstCd"),
            ("town_name", "TwnNm"),
            ("country", "Ctry"),
        ):
            value = address.get(field)
            if value is not None:
                lines.append(f"                    <{tag}>{value}</{tag}>\n")
        address_lines = address.get("address_lines", [])
        if not isinstance(address_lines, list):
            raise AssertionError("PostalAddress27 AdrLine values must be source ordered")
        for line in address_lines:
            lines.append(f"                    <AdrLine>{line}</AdrLine>\n")
        lines.append("                  </PstlAdr>\n")
        return "".join(lines).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
