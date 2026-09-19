"""PostgreSQL REDs for initiating-party agent alternate financial identification."""

from __future__ import annotations

import copy
import re
import unittest
import uuid
from decimal import Decimal

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    MemoryArtifactStore,
    parse_bank_statement_payload,
)
from tests import test_postgres_bank_statement_detail_initiating_party_choice_evidence_red as choice
from tests import test_postgres_bank_statement_detail_initiating_party_evidence_red as initiating
from tests import test_postgres_posting as posting

_HASH_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementDetailInitiatingPartyAgentOtherIdentificationEvidenceRedTests(
    unittest.TestCase
):
    """Retain InitgPty/Agt/FinInstnId/Othr as non-reversible Evidence-Audit provenance."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL initiating-party fixture."""
        choice.BankStatementDetailInitiatingPartyChoiceEvidenceRedTests.setUpClass()

    def setUp(self) -> None:
        """Prepare an isolated initiating-party helper and alternate-ID baseline."""
        self.case = choice.BankStatementDetailInitiatingPartyChoiceEvidenceRedTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.base = {
            "bicfi": "DEUTDEFF",
            "other": {
                "id": "INITIATING-AGENT-ID-001",
                "scheme_proprietary": "CWL-BANK-REL",
                "issuer": "Initiating Agent Registry",
            },
        }

    def test_other_identification_is_material_initiating_evidence(self) -> None:
        """Id, proprietary scheme, issuer, optionals, and Othr presence change evidence."""
        baseline = parse_bank_statement_payload(
            self._payload(self.base),
            CAMT053_MESSAGE_DEFINITION,
        )
        baseline_entry = baseline.entries[0]
        baseline_detail = baseline_entry.entry_details[0]
        self._assert_statement_truth(baseline, baseline_entry, baseline_detail)

        for semantic, changed_agent in self._variants(self.base).items():
            with self.subTest(semantic=semantic):
                changed = parse_bank_statement_payload(
                    self._payload(changed_agent),
                    CAMT053_MESSAGE_DEFINITION,
                )
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

    def test_other_identification_layout_is_representation_only(self) -> None:
        """Whitespace inside Othr changes raw bytes without semantic evidence drift."""
        baseline_payload = self._payload(self.base)
        needle = b"                    <Othr>\n"
        self.assertEqual(baseline_payload.count(needle), 1)
        formatted_payload = baseline_payload.replace(
            needle,
            needle + b"                      \n",
            1,
        )
        self.assertNotEqual(baseline_payload, formatted_payload)

        baseline = parse_bank_statement_payload(
            baseline_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        formatted = parse_bank_statement_payload(
            formatted_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
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

    def test_every_other_identification_variant_reaches_correction_boundary(self) -> None:
        """Every accepted alternate-ID change requires an explicit correction contract."""
        baseline_payload = self._payload(self.base)
        for semantic, changed_agent in self._variants(self.base).items():
            with self.subTest(semantic=semantic):
                bank_account_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
                self.case.helper._register_bank_account(bank_account_reference)
                store = MemoryArtifactStore()
                accepted = initiating.accept_bank_statement_evidence(
                    self.case.helper._command(
                        baseline_payload,
                        f"initiating-agent-other-{semantic}-baseline",
                        bank_account_reference,
                    ),
                    posting.DATABASE_URL,
                    self.case.helper.case.policy.tenant_reference,
                    artifact_store=store,
                )
                self.assertFalse(accepted["replayed"])
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    _CORRECTION_ERROR,
                ):
                    initiating.accept_bank_statement_evidence(
                        self.case.helper._command(
                            self._payload(changed_agent),
                            f"initiating-agent-other-{semantic}-changed",
                            bank_account_reference,
                        ),
                        posting.DATABASE_URL,
                        self.case.helper.case.policy.tenant_reference,
                        artifact_store=store,
                    )

    def test_buyer_projection_keeps_other_identification_non_reversible(self) -> None:
        """Alternate financial IDs affect internal evidence without buyer disclosure."""
        rich_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        bare_reference = f"urn:cwl:bank_account:{uuid.uuid4().hex}"
        self.case.helper._register_bank_account(rich_reference)
        self.case.helper._register_bank_account(bare_reference)

        rich_entry, rich_detail = self.case._ingest_and_read_first_entry_and_detail(
            self._payload(self.base),
            rich_reference,
            f"initiating-agent-other-rich-{uuid.uuid4().hex}",
        )
        bare_agent = {"bicfi": self.base["bicfi"]}
        bare_entry, bare_detail = self.case._ingest_and_read_first_entry_and_detail(
            self._payload(bare_agent),
            bare_reference,
            f"initiating-agent-other-bare-{uuid.uuid4().hex}",
        )
        evidence_key = "initiating_party_evidence_hash"

        for entry, detail in ((rich_entry, rich_detail), (bare_entry, bare_detail)):
            self._assert_uuid(entry["bank_statement_entry_id"])
            self._assert_sha256(entry["source_entry_hash"])
            self._assert_sha256(detail[evidence_key])
            self._assert_sha256(detail["source_detail_hash"])
            self.assertEqual(Decimal(str(entry["entry_amount"])), Decimal("25000.00"))
            self.assertEqual(entry["entry_currency_code"], "KRW")
            self.assertEqual(Decimal(str(detail["detail_amount"])), Decimal("25000.00"))
            self.assertEqual(detail["detail_currency_code"], "KRW")

        self.assertNotEqual(
            rich_detail[evidence_key],
            bare_detail[evidence_key],
        )
        self.assertNotEqual(
            rich_detail["source_detail_hash"],
            bare_detail["source_detail_hash"],
        )
        self.assertNotEqual(
            rich_entry["source_entry_hash"],
            bare_entry["source_entry_hash"],
        )

        rich_public = self._public_entry_projection(rich_entry, evidence_key)
        bare_public = self._public_entry_projection(bare_entry, evidence_key)
        self.assertEqual(rich_public, bare_public)

        other = self.base.get("other")
        if not isinstance(other, dict):
            raise AssertionError("initiating-agent baseline requires Othr")
        source_values = {
            str(other["id"]),
            str(other["scheme_proprietary"]),
            str(other["issuer"]),
        }
        buyer_values = set(self._scalar_leaves(rich_public))
        self.assertTrue(source_values.isdisjoint(buyer_values))

    def _payload(self, agent: dict[str, object]) -> bytes:
        """Insert one schema-shaped InitgPty/Agt alternate identification branch."""
        return self.case._with_choice(self._agent_xml(agent))

    @staticmethod
    def _agent_xml(agent: dict[str, object]) -> str:
        """Serialize FinancialInstitutionIdentification23 with optional Othr."""
        bicfi = agent.get("bicfi")
        if not isinstance(bicfi, str) or not bicfi:
            raise AssertionError("initiating agent requires BICFI")
        lines = [
            "                <Agt>",
            "                  <FinInstnId>",
            f"                    <BICFI>{bicfi}</BICFI>",
        ]
        other = agent.get("other")
        if other is not None:
            if not isinstance(other, dict):
                raise AssertionError("initiating-agent Othr must be a mapping")
            identifier = other.get("id")
            if not isinstance(identifier, str) or not identifier:
                raise AssertionError("initiating-agent Othr requires Id")
            lines.extend(
                [
                    "                    <Othr>",
                    f"                      <Id>{identifier}</Id>",
                ]
            )
            scheme = other.get("scheme_proprietary")
            if scheme is not None:
                if not isinstance(scheme, str) or not scheme:
                    raise AssertionError("proprietary scheme must be non-empty text")
                lines.extend(
                    [
                        "                      <SchmeNm>",
                        f"                        <Prtry>{scheme}</Prtry>",
                        "                      </SchmeNm>",
                    ]
                )
            issuer = other.get("issuer")
            if issuer is not None:
                if not isinstance(issuer, str) or not issuer:
                    raise AssertionError("issuer must be non-empty text")
                lines.append(f"                      <Issr>{issuer}</Issr>")
            lines.append("                    </Othr>")
        lines.extend(
            [
                "                  </FinInstnId>",
                "                </Agt>",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _variants(agent: dict[str, object]) -> dict[str, dict[str, object]]:
        """Return independent schema-valid changes without fabricating external Cd values."""
        variants: dict[str, dict[str, object]] = {}

        identifier_changed = copy.deepcopy(agent)
        identifier_other = identifier_changed.get("other")
        if not isinstance(identifier_other, dict):
            raise AssertionError("initiating-agent baseline requires Othr")
        identifier_other["id"] = "INITIATING-AGENT-ID-002"
        variants["other-id"] = identifier_changed

        scheme_changed = copy.deepcopy(agent)
        scheme_other = scheme_changed.get("other")
        if not isinstance(scheme_other, dict):
            raise AssertionError("initiating-agent baseline requires Othr")
        scheme_other["scheme_proprietary"] = "CWL-BANK-REL-ALT"
        variants["scheme-proprietary"] = scheme_changed

        scheme_absent = copy.deepcopy(agent)
        scheme_absent_other = scheme_absent.get("other")
        if not isinstance(scheme_absent_other, dict):
            raise AssertionError("initiating-agent baseline requires Othr")
        scheme_absent_other.pop("scheme_proprietary")
        variants["scheme-absent"] = scheme_absent

        issuer_changed = copy.deepcopy(agent)
        issuer_changed_other = issuer_changed.get("other")
        if not isinstance(issuer_changed_other, dict):
            raise AssertionError("initiating-agent baseline requires Othr")
        issuer_changed_other["issuer"] = "Alternate Initiating Agent Registry"
        variants["issuer"] = issuer_changed

        issuer_absent = copy.deepcopy(agent)
        issuer_absent_other = issuer_absent.get("other")
        if not isinstance(issuer_absent_other, dict):
            raise AssertionError("initiating-agent baseline requires Othr")
        issuer_absent_other.pop("issuer")
        variants["issuer-absent"] = issuer_absent

        other_absent = copy.deepcopy(agent)
        other_absent.pop("other")
        variants["other-absent"] = other_absent

        return variants

    def _assert_statement_truth(self, statement: object, entry: object, detail: object) -> None:
        """Require canonical evidence identities and preserve exact transaction facts."""
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
        """Require canonical lowercase hyphenated UUID text before hiding server identity."""
        if not isinstance(value, str):
            raise AssertionError(f"expected UUID text, got {value!r}")
        try:
            parsed = uuid.UUID(value)
        except (ValueError, AttributeError) as exc:
            raise AssertionError(f"expected canonical UUID text, got {value!r}") from exc
        if str(parsed) != value:
            raise AssertionError(f"expected canonical UUID text, got {value!r}")

    @staticmethod
    def _public_entry_projection(
        entry: dict[str, object],
        evidence_key: str,
    ) -> dict[str, object]:
        """Remove only server identity and internal evidence hashes for comparison."""
        projection = copy.deepcopy(entry)
        projection.pop("bank_statement_entry_id")
        projection.pop("source_entry_hash")
        details = projection.get("entry_details")
        if not isinstance(details, list):
            raise AssertionError("expected entry_details to be a list")
        for detail in details:
            if not isinstance(detail, dict):
                raise AssertionError("expected buyer detail mapping")
            detail.pop(evidence_key)
            detail.pop("source_detail_hash")
        return projection

    @classmethod
    def _scalar_leaves(cls, value: object) -> list[str]:
        """Collect exact scalar buyer leaves recursively without substring heuristics."""
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
