"""RED contracts for integrity-pinned camt.053.001.14 schema admission."""

from __future__ import annotations

import unittest

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    load_adapter_manifest,
    load_canonical_statement_fixture,
    parse_bank_statement_payload,
)


class BankStatementXsdAdmissionRedTests(unittest.TestCase):
    """Require schema-backed source admission before normalized evidence exists."""

    def test_adapter_manifest_pins_the_supported_message_xsd(self) -> None:
        """The supported ISO revision retains an integrity-pinned XSD artifact."""
        manifest = load_adapter_manifest()
        artifacts = manifest["artifacts"]
        schema_paths = [
            str(artifact["local_package_path"])
            for artifact in artifacts
            if str(artifact["local_package_path"]).lower().endswith(".xsd")
        ]
        self.assertTrue(
            any("camt.053.001.14" in path for path in schema_paths),
            "camt.053.001.14 admission must retain its vendored XSD in the hash-checked adapter manifest",
        )

    def test_unknown_iso_element_fails_before_normalization(self) -> None:
        """Required-path presence cannot substitute for the message schema grammar."""
        fixture = load_canonical_statement_fixture()
        marker = b"      <Id>BANK-STMT-2026-08-24</Id>\n"
        self.assertEqual(fixture.count(marker), 1)
        hostile = fixture.replace(
            marker,
            marker + b"      <CwlUnexpectedEvidence>not-in-camt053</CwlUnexpectedEvidence>\n",
            1,
        )
        with self.assertRaises(AccountingValidationError):
            parse_bank_statement_payload(hostile, CAMT053_MESSAGE_DEFINITION)


if __name__ == "__main__":
    unittest.main()
