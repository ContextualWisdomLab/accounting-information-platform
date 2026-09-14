"""RED contracts for current ISO 20022 camt.053 adapter provenance metadata."""

from __future__ import annotations

import unittest

from accounting_information_platform import (
    CAMT053_MESSAGE_DEFINITION,
    load_adapter_manifest,
)


class BankStatementAdapterProvenanceRedTests(unittest.TestCase):
    """Keep retained adapter provenance aligned with the ISO catalogue entry."""

    def test_manifest_records_current_v14_catalogue_provenance(self) -> None:
        """Do not misattribute the pinned V14 definition or omit its catalogue edition."""
        manifest = load_adapter_manifest()

        self.assertEqual(
            manifest["message_definition_identifier"],
            CAMT053_MESSAGE_DEFINITION,
        )
        self.assertEqual(
            manifest.get("message_definition_name"),
            "BankToCustomerStatementV14",
        )
        self.assertEqual(
            manifest.get("submitting_organization"),
            "ISTH",
            "ISO 20022 currently attributes camt.053.001.14 to submitting organisation ISTH",
        )
        self.assertEqual(
            manifest.get("source_message_set_last_updated"),
            "2026-03-19",
            "retain the ISO catalogue edition separately from local retrieval_time",
        )


if __name__ == "__main__":
    unittest.main()
