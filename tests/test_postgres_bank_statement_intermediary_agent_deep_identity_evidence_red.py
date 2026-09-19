"""PostgreSQL REDs for deep identity inside intermediary-agent chain positions."""

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
    accept_bank_account_record,
    accept_bank_statement_evidence,
    load_canonical_statement_fixture,
    lookup_bank_statement_entries,
    parse_bank_statement_payload,
)
from tests import test_postgres_posting as posting

_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_CORRECTION_ERROR = (
    r"^statement identity already exists with different entry evidence\. "
    r"Use an explicit correction contract, then retry ingest\.$"
)


class BankStatementIntermediaryAgentDeepIdentityEvidenceRedTests(unittest.TestCase):
    """Retain institution and branch identity for IntrmyAgt1, IntrmyAgt2 and IntrmyAgt3."""

    AGENTS = {
        1: {
            "bicfi": "CHASUS33",
            "lei": "7LTWFZYICNSX8D621K86",
            "name": "Intermediary One New York",
            "branch_id": "INT-ONE-NYC-001",
            "branch_lei": "529900Z6KVD8Y83D7K60",
            "branch_name": "New York Intermediary Branch",
        },
        2: {
            "bicfi": "BOFAUS3N",
            "lei": "5493001KJTIIGC8Y1R12",
            "name": "Intermediary Two Charlotte",
            "branch_id": "INT-TWO-CLT-001",
            "branch_lei": "7LTWFZYICNSX8D621K86",
            "branch_name": "Charlotte Intermediary Branch",
        },
        3: {
            "bicfi": "WFBIUS6S",
            "lei": "529900Z6KVD8Y83D7K60",
            "name": "Intermediary Three San Francisco",
            "branch_id": "INT-THREE-SFO-001",
            "branch_lei": "5493001KJTIIGC8Y1R12",
            "branch_name": "San Francisco Intermediary Branch",
        },
    }

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare the unique outgoing-payment detail with exception-safe cleanup."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.marker = (
            "            <AmtDtls>\n"
            "              <TxAmt>\n"
            "                <Amt Ccy=\"KRW\">6000.00</Amt>\n"
            "              </TxAmt>\n"
            "            </AmtDtls>\n"
            "            <RmtInf>"
        )
        self.assertEqual(self.fixture.count(self.marker), 1)

    def test_institution_and_branch_identity_are_material_at_each_chain_position(self) -> None:
        """Deep identity changes the position digest and every normalized evidence identity."""
        for slot in (1, 2, 3):
            base = self._statement(self._with_chain(slot, self.AGENTS[slot]))
            variants = self._variants(self.AGENTS[slot])
            base_entry = base.entries[1]
            base_detail = base_entry.entry_details[0]
            digest_key = f"intermediary_agent_{slot}_evidence_hash"
            base_digest = getattr(base_detail, digest_key, None)
            self._assert_digest(base_digest)
            self._assert_digest(base_detail.source_detail_hash)
            self._assert_exact_amount(base_entry, base_detail)

            for variant_name, identity in variants.items():
                with self.subTest(slot=slot, variant=variant_name):
                    changed = self._statement(self._with_chain(slot, identity))
                    changed_entry = changed.entries[1]
                    changed_detail = changed_entry.entry_details[0]
                    changed_digest = getattr(changed_detail, digest_key, None)
                    self._assert_digest(changed_digest)
                    self._assert_digest(changed_detail.source_detail_hash)
                    self.assertNotEqual(base_digest, changed_digest)
                    self.assertEqual(base.account_identifier_hash, changed.account_identifier_hash)
                    self.assertNotEqual(base_detail.source_detail_hash, changed_detail.source_detail_hash)
                    self.assertNotEqual(base_entry.source_entry_hash, changed_entry.source_entry_hash)
                    self.assertNotEqual(base.normalized_payload_hash, changed.normalized_payload_hash)
                    self.assertEqual(base.entries[0].source_entry_hash, changed.entries[0].source_entry_hash)
                    self._assert_exact_amount(changed_entry, changed_detail)

    def test_branch_layout_is_representation_only_at_each_chain_position(self) -> None:
        """Whitespace inside the target BrnchId changes raw bytes, not intermediary semantics."""
        for slot in (1, 2, 3):
            with self.subTest(slot=slot):
                payload = self._with_chain(slot, self.AGENTS[slot])
                needle = b"                <BrnchId>\n"
                self.assertEqual(payload.count(needle), 1)
                changed_payload = payload.replace(
                    needle,
                    needle + b"                  \n",
                    1,
                )
                base = self._statement(payload)
                changed = self._statement(changed_payload)
                base_entry = base.entries[1]
                changed_entry = changed.entries[1]
                base_detail = base_entry.entry_details[0]
                changed_detail = changed_entry.entry_details[0]
                digest_key = f"intermediary_agent_{slot}_evidence_hash"
                base_digest = getattr(base_detail, digest_key, None)
                changed_digest = getattr(changed_detail, digest_key, None)
                self._assert_digest(base_digest)
                self._assert_digest(changed_digest)
                self._assert_digest(base_detail.source_detail_hash)
                self._assert_digest(changed_detail.source_detail_hash)
                self.assertNotEqual(base.source_artifact_hash, changed.source_artifact_hash)
                self.assertEqual(base_digest, changed_digest)
                self.assertEqual(base_detail.source_detail_hash, changed_detail.source_detail_hash)
                self.assertEqual(base_entry.source_entry_hash, changed_entry.source_entry_hash)
                self.assertEqual(base.normalized_payload_hash, changed.normalized_payload_hash)
                self._assert_exact_amount(changed_entry, changed_detail)

    def test_buyer_projection_remains_digest_only_for_deep_identity(self) -> None:
        """Any deep intermediary identity stays purpose-digested instead of reversible buyer data."""
        for slot in (1, 2, 3):
            with self.subTest(slot=slot):
                deep_payload = self._with_chain(slot, self.AGENTS[slot])
                bicfi_only_payload = self._with_chain(
                    slot,
                    self.AGENTS[slot],
                    target_deep=False,
                )
                deep_statement = self._statement(deep_payload)
                bicfi_only_statement = self._statement(bicfi_only_payload)
                deep_reference = self._register_account(deep_statement, f"deep-{slot}")
                bicfi_only_reference = self._register_account(
                    bicfi_only_statement,
                    f"bicfi-only-{slot}",
                )
                deep_detail = self._ingest_and_read(
                    deep_payload,
                    deep_reference,
                    f"deep-{slot}",
                )
                bicfi_only_detail = self._ingest_and_read(
                    bicfi_only_payload,
                    bicfi_only_reference,
                    f"bicfi-only-{slot}",
                )
                digest_key = f"intermediary_agent_{slot}_evidence_hash"
                self._assert_digest(deep_detail.get(digest_key))
                self._assert_digest(bicfi_only_detail.get(digest_key))
                self._assert_digest(deep_detail.get("source_detail_hash"))
                self._assert_digest(bicfi_only_detail.get("source_detail_hash"))
                self.assertNotEqual(deep_detail[digest_key], bicfi_only_detail[digest_key])
                self.assertNotEqual(
                    deep_detail["source_detail_hash"],
                    bicfi_only_detail["source_detail_hash"],
                )
                deep_projection = dict(deep_detail)
                bicfi_only_projection = dict(bicfi_only_detail)
                for projection in (deep_projection, bicfi_only_projection):
                    projection.pop(digest_key)
                    projection.pop("source_detail_hash")
                self.assertEqual(deep_projection, bicfi_only_projection)
                self.assertEqual(deep_detail["detail_amount"], "6000")
                self.assertEqual(bicfi_only_detail["detail_amount"], "6000")
                self.assertEqual(deep_detail["detail_currency_code"], "KRW")
                self.assertEqual(bicfi_only_detail["detail_currency_code"], "KRW")

    def test_material_deep_identity_change_requires_explicit_correction(self) -> None:
        """Accepted intermediary deep identity cannot be silently replaced on replay."""
        slot = 3
        base_payload = self._with_chain(slot, self.AGENTS[slot])
        changed_payload = self._with_chain(
            slot,
            self._variants(self.AGENTS[slot])["branch-id"],
        )
        statement = self._statement(base_payload)
        account_reference = self._register_account(statement, "correction")
        store = MemoryArtifactStore()
        accepted = accept_bank_statement_evidence(
            self._command(base_payload, account_reference, "base"),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=store,
        )
        self.assertFalse(accepted["replayed"])
        with self.assertRaisesRegex(AccountingValidationError, _CORRECTION_ERROR):
            accept_bank_statement_evidence(
                self._command(changed_payload, account_reference, "changed"),
                posting.DATABASE_URL,
                self.case.policy.tenant_reference,
                artifact_store=store,
            )

    @staticmethod
    def _variants(identity: dict[str, str]) -> dict[str, dict[str, str]]:
        """Change one institution or branch identity fact while retaining BICFI."""
        institution_lei = copy.deepcopy(identity)
        institution_lei["lei"] = "213800D1EI4B9WTWWD28"
        institution_name = copy.deepcopy(identity)
        institution_name["name"] = identity["name"] + " Updated"
        branch_id = copy.deepcopy(identity)
        branch_id["branch_id"] = identity["branch_id"] + "-ALT"
        branch_lei = copy.deepcopy(identity)
        branch_lei["branch_lei"] = "213800D1EI4B9WTWWD28"
        branch_name = copy.deepcopy(identity)
        branch_name["branch_name"] = identity["branch_name"] + " Updated"
        return {
            "institution-lei": institution_lei,
            "institution-name": institution_name,
            "branch-id": branch_id,
            "branch-lei": branch_lei,
            "branch-name": branch_name,
        }

    def _with_chain(
        self,
        target_slot: int,
        target_identity: dict[str, str],
        *,
        target_deep: bool = True,
    ) -> bytes:
        """Insert an ordered intermediary chain, optionally omitting target deep identity."""
        if target_slot not in self.AGENTS:
            raise AssertionError(f"unsupported intermediary slot: {target_slot}")
        lines = ["            <RltdAgts>"]
        for slot in range(1, target_slot + 1):
            identity = target_identity if slot == target_slot else self.AGENTS[slot]
            lines.extend(
                self._agent_lines(
                    slot,
                    identity,
                    deep=target_deep and slot == target_slot,
                )
            )
        lines.append("            </RltdAgts>")
        replacement = (
            "            <AmtDtls>\n"
            "              <TxAmt>\n"
            "                <Amt Ccy=\"KRW\">6000.00</Amt>\n"
            "              </TxAmt>\n"
            "            </AmtDtls>\n"
            + "\n".join(lines)
            + "\n            <RmtInf>"
        )
        changed = self.fixture.replace(self.marker, replacement, 1)
        if changed == self.fixture:
            raise AssertionError("intermediary deep-identity insertion must change XML")
        return changed.encode("utf-8")

    @staticmethod
    def _agent_lines(slot: int, identity: dict[str, str], *, deep: bool) -> list[str]:
        """Serialize one BranchAndFinancialInstitutionIdentification8 in V14 sequence order."""
        lines = [
            f"              <IntrmyAgt{slot}>",
            "                <FinInstnId>",
            f"                  <BICFI>{identity['bicfi']}</BICFI>",
        ]
        if deep:
            lines.extend(
                [
                    f"                  <LEI>{identity['lei']}</LEI>",
                    f"                  <Nm>{identity['name']}</Nm>",
                ]
            )
        lines.append("                </FinInstnId>")
        if deep:
            lines.extend(
                [
                    "                <BrnchId>",
                    f"                  <Id>{identity['branch_id']}</Id>",
                    f"                  <LEI>{identity['branch_lei']}</LEI>",
                    f"                  <Nm>{identity['branch_name']}</Nm>",
                    "                </BrnchId>",
                ]
            )
        lines.append(f"              </IntrmyAgt{slot}>")
        return lines

    @staticmethod
    def _statement(payload: bytes):
        """Parse source-real camt.053.001.14 through the supported adapter."""
        return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    @staticmethod
    def _assert_digest(value: object) -> None:
        """Require a canonical purpose-bound SHA-256 digest."""
        if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
            raise AssertionError("intermediary-agent evidence digest must be canonical SHA-256")

    @staticmethod
    def _assert_exact_amount(entry: object, detail: object) -> None:
        """Keep exact accounting facts independent from intermediary provenance."""
        if getattr(entry, "entry_amount", None) != Decimal("6000.00"):
            raise AssertionError("intermediary evidence must retain exact 6000.00 entry amount")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("intermediary evidence must retain KRW entry currency")
        if getattr(detail, "detail_amount", None) != Decimal("6000.00"):
            raise AssertionError("intermediary evidence must retain exact 6000.00 detail amount")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("intermediary evidence must retain KRW detail currency")

    def _register_account(self, statement: object, suffix: str) -> str:
        """Register an isolated buyer account for one statement scenario."""
        reference = f"urn:cwl:bank_account:intermediary-deep:{suffix}:{uuid.uuid4().hex}"
        accept_bank_account_record(
            {
                "tenant_reference": self.case.policy.tenant_reference,
                "bank_account_reference": reference,
                "account_currency_code": statement.account_currency_code,
                "account_identifier_hash": statement.account_identifier_hash,
            },
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
        )
        return reference

    def _ingest_and_read(
        self,
        payload: bytes,
        account_reference: str,
        suffix: str,
    ) -> dict[str, object]:
        """Ingest one statement and return the outgoing-payment transaction detail."""
        accepted = accept_bank_statement_evidence(
            self._command(payload, account_reference, suffix),
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            artifact_store=MemoryArtifactStore(),
        )
        document = lookup_bank_statement_entries(
            posting.DATABASE_URL,
            self.case.policy.tenant_reference,
            str(accepted["bank_statement_record_id"]),
        )
        return document["bank_statement_entries"][1]["entry_details"][0]

    def _command(
        self,
        payload: bytes,
        account_reference: str,
        suffix: str,
    ) -> dict[str, object]:
        """Build one supported ingest command with a fresh idempotency key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": account_reference,
            "ingestion_idempotency_key": (
                f"intermediary-deep-{suffix}-{uuid.uuid4().hex}"
            ),
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
