"""Repository contracts for bounded durable authorization-decision evidence."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from accounting_information_platform.http_api import _authorization_correlation


MIGRATION = Path("database/migrations/0015_authorization_decision_evidence.sql")


class AuthorizationEvidenceStorageContractTests(unittest.TestCase):
    """Keep every caller-derived authorization text field bounded at PostgreSQL."""

    def test_identity_references_are_bounded_cwl_urns(self) -> None:
        """Normalized identity references cannot become unbounded durable audit payloads."""
        text = MIGRATION.read_text(encoding="utf-8")
        reference_columns = (
            "principal_reference",
            "principal_tenant_reference",
            "requested_tenant_reference",
            "authentication_context_reference",
            "credential_evidence_reference",
        )

        for column in reference_columns:
            with self.subTest(column=column):
                column_block = text.split(f"{column} text NOT NULL", 1)[1].split(",\n", 1)[0]
                self.assertIn(f"octet_length({column}) <= 255", column_block)
                self.assertIn(
                    f"{column} ~ '^urn:cwl:[A-Za-z0-9_:.-]+$'",
                    column_block,
                )

    def test_decision_vocabulary_and_correlation_are_bounded(self) -> None:
        """Direct SQL cannot bypass the application vocabulary or correlation size budgets."""
        text = MIGRATION.read_text(encoding="utf-8")

        self.assertIn("octet_length(operation_code) <= 64", text)
        self.assertIn("octet_length(permission_code) <= 129", text)
        self.assertIn("octet_length(purpose_code) <= 64", text)
        self.assertIn("octet_length(policy_version) <= 64", text)
        self.assertIn("char_length(correlation_reference) <= 512", text)
        self.assertIn("operation_code ~ '^[a-z][a-z0-9_]{1,63}$'", text)
        self.assertIn(
            "permission_code ~ '^[a-z][a-z0-9_]{1,63}\\.[a-z][a-z0-9_]{1,63}$'",
            text,
        )
        self.assertIn("purpose_code ~ '^[a-z][a-z0-9_]{1,63}$'", text)

    def test_multibyte_command_identity_uses_the_same_character_budget(self) -> None:
        """UTF-8 command identities cannot fail storage merely because bytes exceed characters."""
        key = "한" * 160
        raw_body = json.dumps(
            {"idempotency_key": key}, ensure_ascii=False
        ).encode("utf-8")

        correlation = _authorization_correlation("/journal-proposals", raw_body)

        self.assertEqual(correlation, f"idempotency_key:{key}")
        self.assertLessEqual(len(correlation), 512)
        self.assertGreater(len(correlation.encode("utf-8")), 512)


if __name__ == "__main__":  # pragma: no cover - direct local invocation only
    unittest.main()
