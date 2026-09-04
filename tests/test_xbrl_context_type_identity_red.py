"""RED contracts for exact JSON type identity in XBRL report context replay."""

from __future__ import annotations

import copy
import unittest
from dataclasses import replace

from accounting_information_platform import financial_reporting as reporting
from accounting_information_platform.core import AccountingValidationError
from financial_reporting_fixtures import (
    _rehash,
    _report_context,
    _statement_package,
    _taxonomy_profile,
)


class XbrlContextTypeIdentityTests(unittest.TestCase):
    """Reject JSON numeric aliases that compare equal after Python coercion."""

    def test_decimal_precision_json_type_cannot_alias_integer_context(self) -> None:
        """A bool or float must not replay as the canonical integer precision."""
        for canonical_precision, forged_precision in ((0, False), (1, 1.0)):
            with self.subTest(forged_precision=forged_precision):
                context = replace(
                    _report_context(),
                    decimal_precision=canonical_precision,
                )
                artifact = reporting.build_financial_report_artifact(
                    _statement_package(),
                    context,
                )
                forged_artifact = copy.deepcopy(artifact)
                forged_artifact["report_context"]["decimal_precision"] = forged_precision
                _rehash(forged_artifact)

                with self.assertRaisesRegex(
                    AccountingValidationError,
                    "report_context contains invalid values",
                ):
                    reporting.export_xbrl_instance(
                        forged_artifact,
                        _taxonomy_profile(),
                    )


if __name__ == "__main__":
    unittest.main()
