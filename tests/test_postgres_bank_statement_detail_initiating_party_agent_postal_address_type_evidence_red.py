"""PostgreSQL REDs for initiating-party agent PostalAddress27 address-type choice evidence."""

from __future__ import annotations

import copy
import re
import unittest
import uuid
from decimal import Decimal

from accounting_information_platform import AccountingValidationError, MemoryArtifactStore
from tests import (
    test_postgres_bank_statement_detail_initiating_party_agent_postal_address_evidence_red as postal,
)
from tests import test_postgres_bank_statement_detail_initiating_party_evidence_red as initiating
from tests import test_postgres_posting as posting

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)

_BASE_PROPRIETARY = {
    "id": "BIZZ",
    "issuer": "Initiating Address Registry",
    "scheme_name": "ADDR-TYPE",
}


class BankStatementDetailInitiatingPartyAgentPostalAddressTypeEvidenceRedTests(
    unittest.TestCase
):
    """Retain AdrTp/Cd|Prtry discriminator and GenericIdentification30 provenance."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL initiating-agent fixture."""
        postal.BankStatementDetailInitiatingPartyAgentPostalAddressEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare an isolated initiating-party postal fixture."""
        self.case = postal.BankStatementDetailInitiatingPartyAgentPostalAddressEvidenceRedTests(
            "setUp"
        )
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)

    def test_proprietary_address_type_nested_facts_and_choice_are_material(self) -> None:
        """Prtry Id/Issr/SchmeNm and the Cd|Prtry discriminator change evidence."""
        for placement in ("institution", "branch"):
            baseline = self._statement(
                self._payload(placement, proprietary=_BASE_PROPRIETARY)
            )
            baseline_entry = baseline.entries[0]
            baseline_detail = baseline_entry.entry_details[0]
            self._assert_statement_truth(baseline, baseline_entry, baseline_detail)

            for semantic, variant in self._variants().items():
                with self.subTest(placement=placement, semantic=semantic):
                    if semantic == "same-id-choice-discriminator":
                        changed_payload = self._payload(
                            placement,
                            proprietary=None,
                            coded="BIZZ",
                        )
                    else:
                        changed_payload = self._payload(
                            placement,
                            proprietary=variant,
                        )
                    changed = self._statement(changed_payload)
                    changed_entry = changed.entries[0]
                    changed_detail = changed_entry.entry_details[0]
                    self._assert_statement_truth(changed, changed_entry, changed_detail)

                    self.assertNotEqual(
                        baseline_detail.initiating_party_evidence_hash,
                        changed_detail.initiating_party_evidence_hash,
                    )
                    self.assertNotEqual(
                        baseline_detail.source_detail_hash,
                        changed_detail.source_detail_hash,
                    )
                    self.assertNotEqual(
                        baseline_entry.source_entry_hash,
                        changed_entry.source_entry_hash,
                    )
                    self.assertNotEqual(
                        baseline.normalized_payload_hash,
                        changed.normalized_payload_hash,
                    )
                    self.assertEqual(
                        baseline.account_identifier_hash,
                        changed.account_identifier_hash,
                    )
                    self.assertEqual(
                        baseline.entries[1].source_entry_hash,
                        changed.entries[1].source_entry_hash,
                    )

    def test_proprietary_address_type_layout_is_representation_only(self) -> None:
        """Whitespace inside GenericIdentification30 changes bytes but not semantics."""
        for placement in ("institution", "branch"):
            baseline_payload = self._payload(placement, proprietary=_BASE_PROPRIETARY)
            needle = b"                        <Prtry>\n"
            self.assertEqual(baseline_payload.count(needle), 1)
            formatted_payload = baseline_payload.replace(
                needle,
                needle + b"                          \n",
                1,
            )
            baseline = self._statement(baseline_payload)
            formatted = self._statement(formatted_payload)
            baseline_entry = baseline.entries[0]
            formatted_entry = formatted.entries[0]
            baseline_detail = baseline_entry.entry_details[0]
            formatted_detail = formatted_entry.entry_details[0]
            self._assert_statement_truth(baseline, baseline_entry, baseline_detail)
            self._assert_statement_truth(formatted, formatted_entry, formatted_detail)
            self._assert_sha256(baseline.source_artifact_hash)
            self._assert_sha256(formatted.source_artifact_hash)

            self.assertNotEqual(
                baseline.source_artifact_hash,
                formatted.source_artifact_hash,
            )
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

    def test_every_proprietary_address_type_variant_reaches_correction_boundary(self) -> None:
        """Accepted proprietary address-type evidence requires explicit correction."""
        for placement in ("institution", "branch"):
            baseline_payload = self._payload(placement, proprietary=_BASE_PROPRIETARY)
            for semantic, variant in self._variants().items():
                with self.subTest(placement=placement, semantic=semantic):
                    reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
                    self.case.case.helper._register_bank_account(reference)
                    store = MemoryArtifactStore()
                    accepted = initiating.accept_bank_statement_evidence(
                        self.case.case.helper._command(
                            baseline_payload,
                            f"initiating-agent-address-type-{placement}-{semantic}-baseline",
                            reference,
                        ),
                        posting.DATABASE_URL,
                        self.case.case.helper.case.policy.tenant_reference,
                        artifact_store=store,
                    )
                    self.assertFalse(accepted["replayed"])
                    changed_payload = (
                        self._payload(placement, proprietary=None, coded="BIZZ")
                        if semantic == "same-id-choice-discriminator"
                        else self._payload(placement, proprietary=variant)
                    )
                    with self.assertRaisesRegex(
                        AccountingValidationError,
                        _CORRECTION_ERROR,
                    ):
                        initiating.accept_bank_statement_evidence(
                            self.case.case.helper._command(
                                changed_payload,
                                f"initiating-agent-address-type-{placement}-{semantic}-changed",
                                reference,
                            ),
                            posting.DATABASE_URL,
                            self.case.case.helper.case.policy.tenant_reference,
                            artifact_store=store,
                        )

    def test_proprietary_address_type_source_values_are_not_buyer_reversible(self) -> None:
        """GenericIdentification30 affects internal evidence without buyer disclosure."""
        for placement in ("institution", "branch"):
            rich_payload = self._payload(placement, proprietary=_BASE_PROPRIETARY)
            absent_payload = self.case._payload(
                institution_address=None,
                branch_address=None,
            )
            rich_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
            absent_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
            self.case.case.helper._register_bank_account(rich_reference)
            self.case.case.helper._register_bank_account(absent_reference)
            rich_entry, rich_detail = self.case.case._ingest_and_read_first_entry_and_detail(
                rich_payload,
                rich_reference,
                f"initiating-agent-address-type-{placement}-rich-{uuid.uuid4().hex}",
            )
            absent_entry, absent_detail = self.case.case._ingest_and_read_first_entry_and_detail(
                absent_payload,
                absent_reference,
                f"initiating-agent-address-type-{placement}-absent-{uuid.uuid4().hex}",
            )

            for entry, detail in ((rich_entry, rich_detail), (absent_entry, absent_detail)):
                self._assert_uuid(entry["bank_statement_entry_id"])
                self._assert_sha256(entry["source_entry_hash"])
                self._assert_sha256(detail["initiating_party_evidence_hash"])
                self._assert_sha256(detail["source_detail_hash"])
                self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
                self.assertEqual(entry["entry_currency_code"], "KRW")
                self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
                self.assertEqual(detail["detail_currency_code"], "KRW")

            self.assertNotEqual(
                rich_detail["initiating_party_evidence_hash"],
                absent_detail["initiating_party_evidence_hash"],
            )
            self.assertNotEqual(
                rich_detail["source_detail_hash"],
                absent_detail["source_detail_hash"],
            )
            self.assertNotEqual(
                rich_entry["source_entry_hash"],
                absent_entry["source_entry_hash"],
            )

            rich_public = self._public_entry_projection(rich_entry)
            absent_public = self._public_entry_projection(absent_entry)
            self.assertEqual(rich_public, absent_public)
            source_values = set(self._scalar_leaves(_BASE_PROPRIETARY))
            buyer_values = set(self._scalar_leaves(rich_public))
            self.assertTrue(source_values.isdisjoint(buyer_values))

    def _payload(
        self,
        placement: str,
        *,
        proprietary: dict[str, str] | None,
        coded: str | None = None,
    ) -> bytes:
        """Insert a single PstlAdr/AdrTp choice after the selected agent name."""
        if (proprietary is None) == (coded is None):
            raise AssertionError("exactly one proprietary or coded address type is required")
        payload = self.case._payload(
            institution_address=None,
            branch_address=None,
        )
        identity = self.case.identity
        if placement == "institution":
            name = identity["name"]
        elif placement == "branch":
            name = identity["branch_name"]
        else:
            raise AssertionError(f"unsupported address placement: {placement}")
        needle = f"                    <Nm>{name}</Nm>\n".encode("utf-8")
        if payload.count(needle) != 1:
            raise AssertionError("selected initiating-agent name must be unique")
        return payload.replace(
            needle,
            needle + self._address_type_xml(proprietary=proprietary, coded=coded),
            1,
        )

    @staticmethod
    def _address_type_xml(
        *,
        proprietary: dict[str, str] | None,
        coded: str | None,
    ) -> bytes:
        """Serialize a minimal PostalAddress27 carrying one AdrTp choice."""
        lines = [
            "                    <PstlAdr>\n",
            "                      <AdrTp>\n",
        ]
        if proprietary is not None:
            identifier = proprietary.get("id")
            issuer = proprietary.get("issuer")
            if not identifier or not issuer:
                raise AssertionError("AdrTp/Prtry requires Id and Issr")
            lines.extend(
                [
                    "                        <Prtry>\n",
                    f"                          <Id>{identifier}</Id>\n",
                    f"                          <Issr>{issuer}</Issr>\n",
                ]
            )
            scheme_name = proprietary.get("scheme_name")
            if scheme_name is not None:
                if not scheme_name:
                    raise AssertionError("AdrTp/Prtry/SchmeNm must be non-empty")
                lines.append(f"                          <SchmeNm>{scheme_name}</SchmeNm>\n")
            lines.append("                        </Prtry>\n")
        else:
            if not coded:
                raise AssertionError("AdrTp/Cd must be non-empty")
            lines.append(f"                        <Cd>{coded}</Cd>\n")
        lines.extend(
            [
                "                      </AdrTp>\n",
                "                    </PstlAdr>\n",
            ]
        )
        return "".join(lines).encode("utf-8")

    @staticmethod
    def _variants() -> dict[str, dict[str, str]]:
        """Return independent proprietary-value/presence changes plus choice marker."""
        variants: dict[str, dict[str, str]] = {}

        changed_id = copy.deepcopy(_BASE_PROPRIETARY)
        changed_id["id"] = "HOME"
        variants["proprietary-id-value"] = changed_id

        changed_issuer = copy.deepcopy(_BASE_PROPRIETARY)
        changed_issuer["issuer"] = "Alternate Initiating Address Registry"
        variants["proprietary-issuer-value"] = changed_issuer

        changed_scheme = copy.deepcopy(_BASE_PROPRIETARY)
        changed_scheme["scheme_name"] = "ALT-ADDR-TYPE"
        variants["proprietary-scheme-value"] = changed_scheme

        scheme_absent = copy.deepcopy(_BASE_PROPRIETARY)
        scheme_absent.pop("scheme_name")
        variants["proprietary-scheme-absent"] = scheme_absent

        variants["same-id-choice-discriminator"] = copy.deepcopy(_BASE_PROPRIETARY)
        return variants

    @staticmethod
    def _statement(payload: bytes) -> object:
        """Parse one V14 statement through the same supported boundary."""
        return postal.parse_bank_statement_payload(
            payload,
            postal.CAMT053_MESSAGE_DEFINITION,
        )

    def _assert_statement_truth(self, statement: object, entry: object, detail: object) -> None:
        """Require canonical evidence identities and exact financial facts."""
        for value in (
            getattr(detail, "initiating_party_evidence_hash"),
            getattr(detail, "source_detail_hash"),
            getattr(entry, "source_entry_hash"),
            getattr(statement, "normalized_payload_hash"),
            getattr(statement, "account_identifier_hash"),
            getattr(statement.entries[1], "source_entry_hash"),
        ):
            self._assert_sha256(value)
        self.assertEqual(getattr(entry, "entry_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(entry, "entry_currency_code"), "KRW")
        self.assertEqual(getattr(detail, "detail_amount"), Decimal("25000.00"))
        self.assertEqual(getattr(detail, "detail_currency_code"), "KRW")

    @staticmethod
    def _assert_sha256(value: object) -> None:
        """Require canonical lowercase SHA-256 evidence text."""
        if not isinstance(value, str) or _HASH_PATTERN.fullmatch(value) is None:
            raise AssertionError(f"expected canonical sha256 digest, got {value!r}")

    @staticmethod
    def _assert_uuid(value: object) -> None:
        """Require canonical lowercase hyphenated UUID text before hiding identity."""
        if not isinstance(value, str):
            raise AssertionError(f"expected UUID text, got {value!r}")
        try:
            parsed = uuid.UUID(value)
        except (ValueError, AttributeError) as exc:
            raise AssertionError(f"expected canonical UUID text, got {value!r}") from exc
        if str(parsed) != value:
            raise AssertionError(f"expected canonical UUID text, got {value!r}")

    @staticmethod
    def _public_entry_projection(entry: dict[str, object]) -> dict[str, object]:
        """Remove only server identity and initiating evidence identities."""
        projection = copy.deepcopy(entry)
        projection.pop("bank_statement_entry_id")
        projection.pop("source_entry_hash")
        details = projection.get("entry_details")
        if not isinstance(details, list):
            raise AssertionError("expected entry_details to be a list")
        for detail in details:
            if not isinstance(detail, dict):
                raise AssertionError("expected buyer detail mapping")
            detail.pop("initiating_party_evidence_hash")
            detail.pop("source_detail_hash")
        return projection

    @classmethod
    def _scalar_leaves(cls, value: object) -> list[str]:
        """Collect exact scalar leaves recursively without substring matching."""
        leaves: list[str] = []
        if isinstance(value, dict):
            for child in value.values():
                leaves.extend(cls._scalar_leaves(child))
        elif isinstance(value, (list, tuple)):
            for child in value:
                leaves.extend(cls._scalar_leaves(child))
        elif value is not None:
            leaves.append(str(value))
        return leaves


if __name__ == "__main__":
    unittest.main()
