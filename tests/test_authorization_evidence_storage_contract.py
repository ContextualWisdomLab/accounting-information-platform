"""Repository contracts for bounded durable authorization-decision evidence."""

from __future__ import annotations

from pathlib import Path
import unittest


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
        self.assertIn("octet_length(correlation_reference) <= 512", text)
        self.assertIn("operation_code ~ '^[a-z][a-z0-9_]{1,63}$'", text)
        self.assertIn(
            "permission_code ~ '^[a-z][a-z0-9_]{1,63}\\.[a-z][a-z0-9_]{1,63}$'",
            text,
        )
        self.assertIn("purpose_code ~ '^[a-z][a-z0-9_]{1,63}$'", text)


if __name__ == "__main__":  # pragma: no cover - direct local invocation only
    unittest.main()
