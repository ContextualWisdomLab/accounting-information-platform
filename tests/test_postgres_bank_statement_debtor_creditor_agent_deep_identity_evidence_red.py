"""PostgreSQL REDs for deep debtor/creditor transaction-agent identity evidence."""

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


class BankStatementDebtorCreditorAgentDeepIdentityEvidenceRedTests(unittest.TestCase):
    """Retain deep BranchAndFinancialInstitutionIdentification8 facts for DbtrAgt/CdtrAgt."""

    AGENTS = {
        "DbtrAgt": {
            "digest_key": "debtor_agent_evidence_hash",
            "bicfi": "DEUTDEFF",
            "lei": "7LTWFZYICNSX8D621K86",
            "name": "Debtor Bank Frankfurt",
            "branch_id": "DBTR-FRA-001",
            "branch_lei": "529900Z6KVD8Y83D7K60",
            "branch_name": "Frankfurt Debtor Branch",
        },
        "CdtrAgt": {
            "digest_key": "creditor_agent_evidence_hash",
            "bicfi": "BNPAFRPP",
            "lei": "5493001KJTIIGC8Y1R12",
            "name": "Creditor Bank Paris",
            "branch_id": "CDTR-PAR-001",
            "branch_lei": "213800D1EI4B9WTWWD28",
            "branch_name": "Paris Creditor Branch",
        },
    }

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical real-PostgreSQL integration fixture."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Prepare the first transaction detail with exception-safe nested cleanup."""
        self.case = posting.PostgresPostingTests("setUp")
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.addCleanup(self.case.tearDown)
        self.fixture = load_canonical_statement_fixture().decode("utf-8")
        self.marker = "            </RltdPties>\n            <RmtInf>"
        self.assertEqual(self.fixture.count(self.marker), 1)

    def test_institution_and_branch_identity_are_material_for_debtor_and_creditor_agent(self) -> None:
        """Deep agent facts change the role digest and normalized evidence identity."""
        for role, identity in self.AGENTS.items():
            base = self._statement(self._with_agent(role, identity))
            base_entry = base.entries[0]
            base_detail = base_entry.entry_details[0]
            digest_key = identity["digest_key"]
            base_digest = getattr(base_detail, digest_key, None)
            self._assert_digest(base_digest)
            self._assert_digest(base_detail.source_detail_hash)
            self._assert_exact_amount(base_entry, base_detail)

            for variant_name, variant in self._variants(identity).items():
                with self.subTest(role=role, variant=variant_name):
                    changed = self._statement(self._with_agent(role, variant))
                    changed_entry = changed.entries[0]
                    changed_detail = changed_entry.entry_details[0]
                    changed_digest = getattr(changed_detail, digest_key, None)
                    self._assert_digest(changed_digest)
                    self._assert_digest(changed_detail.source_detail_hash)
                    self.assertNotEqual(base_digest, changed_digest)
                    self.assertEqual(
                        base.account_identifier_hash,
                        changed.account_identifier_hash,
                    )
                    self.assertNotEqual(
                        base_detail.source_detail_hash,
                        changed_detail.source_detail_hash,
                    )
                    self.assertNotEqual(
                        base_entry.source_entry_hash,
                        changed_entry.source_entry_hash,
                    )
                    self.assertNotEqual(
                        base.normalized_payload_hash,
                        changed.normalized_payload_hash,
                    )
                    self.assertEqual(
                        base.entries[1].source_entry_hash,
                        changed.entries[1].source_entry_hash,
                    )
                    self._assert_exact_amount(changed_entry, changed_detail)

    def test_branch_layout_is_representation_only_for_debtor_and_creditor_agent(self) -> None:
        """Whitespace inside BrnchId changes raw bytes without changing admitted semantics."""
        needle = b"              <BrnchId>\n"
        whitespace = b"                \n"

        for role, identity in self.AGENTS.items():
            with self.subTest(role=role):
                payload = self._with_agent(role, identity)
                self.assertEqual(payload.count(needle), 1)
                reformatted = payload.replace(needle, needle + whitespace, 1)
                self.assertNotEqual(payload, reformatted)

                base = self._statement(payload)
                changed = self._statement(reformatted)
                base_entry = base.entries[0]
                changed_entry = changed.entries[0]
                base_detail = base_entry.entry_details[0]
                changed_detail = changed_entry.entry_details[0]
                digest_key = identity["digest_key"]
                base_digest = getattr(base_detail, digest_key, None)
                changed_digest = getattr(changed_detail, digest_key, None)
                self._assert_digest(base_digest)
                self._assert_digest(changed_digest)
                self._assert_digest(base_detail.source_detail_hash)
                self._assert_digest(changed_detail.source_detail_hash)

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
                self._assert_exact_amount(changed_entry, changed_detail)

    def test_buyer_projection_remains_digest_only_for_deep_debtor_creditor_identity(self) -> None:
        """Deep debtor/creditor-agent identity adds no reversible buyer projection fields."""
        for role, identity in self.AGENTS.items():
            with self.subTest(role=role):
                deep_payload = self._with_agent(role, identity)
                bicfi_only_payload = self._with_agent(role, identity, deep=False)
                deep_statement = self._statement(deep_payload)
                bicfi_statement = self._statement(bicfi_only_payload)
                deep_reference = self._register_account(
                    deep_statement,
                    f"{role.lower()}-deep",
                )
                bicfi_reference = self._register_account(
                    bicfi_statement,
                    f"{role.lower()}-bicfi",
                )
                deep_detail = self._ingest_and_read(
                    deep_payload,
                    deep_reference,
                    f"{role.lower()}-deep",
                )
                bicfi_detail = self._ingest_and_read(
                    bicfi_only_payload,
                    bicfi_reference,
                    f"{role.lower()}-bicfi",
                )
                digest_key = identity["digest_key"]

                for projection in (deep_detail, bicfi_detail):
                    self._assert_digest(projection.get(digest_key))
                    self._assert_digest(projection.get("source_detail_hash"))
                    self.assertEqual(projection["detail_amount"], "25000")
                    self.assertEqual(projection["detail_currency_code"], "KRW")

                self.assertNotEqual(
                    deep_detail[digest_key],
                    bicfi_detail[digest_key],
                )
                self.assertNotEqual(
                    deep_detail["source_detail_hash"],
                    bicfi_detail["source_detail_hash"],
                )
                deep_visible = dict(deep_detail)
                bicfi_visible = dict(bicfi_detail)
                for projection in (deep_visible, bicfi_visible):
                    projection.pop(digest_key)
                    projection.pop("source_detail_hash")
                self.assertEqual(deep_visible, bicfi_visible)

    def test_material_deep_agent_change_requires_explicit_correction(self) -> None:
        """Accepted debtor/creditor deep identity cannot be replaced on ordinary replay."""
        role = "CdtrAgt"
        identity = self.AGENTS[role]
        base_payload = self._with_agent(role, identity)
        changed_payload = self._with_agent(
            role,
            self._variants(identity)["branch-id"],
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
        """Change one deep institution/branch fact while retaining the reported BICFI."""
        institution_lei = copy.deepcopy(identity)
        institution_lei["lei"] = "529900Z6KVD8Y83D7K60"
        if institution_lei["lei"] == identity["lei"]:
            institution_lei["lei"] = "7LTWFZYICNSX8D621K86"
        institution_name = copy.deepcopy(identity)
        institution_name["name"] = identity["name"] + " Updated"
        branch_id = copy.deepcopy(identity)
        branch_id["branch_id"] = identity["branch_id"] + "-ALT"
        branch_lei = copy.deepcopy(identity)
        branch_lei["branch_lei"] = "5493001KJTIIGC8Y1R12"
        if branch_lei["branch_lei"] == identity["branch_lei"]:
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

    def _with_agent(
        self,
        role: str,
        identity: dict[str, str],
        *,
        deep: bool = True,
    ) -> bytes:
        """Insert one supported debtor/creditor agent in V14 sequence order."""
        if role not in self.AGENTS:
            raise AssertionError(f"unsupported debtor/creditor agent role: {role}")
        lines = [
            "            <RltdAgts>",
            f"              <{role}>",
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
        lines.extend([f"              </{role}>", "            </RltdAgts>"])
        replacement = (
            "            </RltdPties>\n"
            + "\n".join(lines)
            + "\n            <RmtInf>"
        )
        changed = self.fixture.replace(self.marker, replacement, 1)
        if changed == self.fixture:
            raise AssertionError("debtor/creditor deep-identity insertion must change XML")
        return changed.encode("utf-8")

    @staticmethod
    def _statement(payload: bytes):
        """Parse source-real camt.053.001.14 through the supported adapter."""
        return parse_bank_statement_payload(payload, CAMT053_MESSAGE_DEFINITION)

    @staticmethod
    def _assert_digest(value: object) -> None:
        """Require a canonical purpose-bound SHA-256 digest."""
        if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
            raise AssertionError("agent evidence digest must be canonical SHA-256")

    @staticmethod
    def _assert_exact_amount(entry: object, detail: object) -> None:
        """Keep exact accounting facts independent from bank-reported agent identity."""
        if getattr(entry, "entry_amount", None) != Decimal("25000.00"):
            raise AssertionError("agent evidence must retain exact 25000.00 entry amount")
        if getattr(entry, "entry_currency_code", None) != "KRW":
            raise AssertionError("agent evidence must retain KRW entry currency")
        if getattr(detail, "detail_amount", None) != Decimal("25000.00"):
            raise AssertionError("agent evidence must retain exact 25000.00 detail amount")
        if getattr(detail, "detail_currency_code", None) != "KRW":
            raise AssertionError("agent evidence must retain KRW detail currency")

    def _register_account(self, statement: object, suffix: str) -> str:
        """Register an isolated buyer account for one statement scenario."""
        reference = f"urn:cwl:bank_account:counterparty-agent-deep:{suffix}:{uuid.uuid4().hex}"
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
        """Ingest one statement and return its first transaction detail projection."""
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
        return document["bank_statement_entries"][0]["entry_details"][0]

    def _command(
        self,
        payload: bytes,
        account_reference: str,
        suffix: str,
    ) -> dict[str, object]:
        """Build one supported ingest command with an isolated replay key."""
        return {
            "tenant_reference": self.case.policy.tenant_reference,
            "bank_account_reference": account_reference,
            "ingestion_idempotency_key": f"counterparty-agent-deep-{suffix}-{uuid.uuid4().hex}",
            "message_definition_identifier": CAMT053_MESSAGE_DEFINITION,
            "statement_payload": payload.decode("utf-8"),
        }


if __name__ == "__main__":
    unittest.main()
