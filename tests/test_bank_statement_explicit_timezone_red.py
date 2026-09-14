"""RED contracts for unambiguous bank-statement timestamp evidence."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from accounting_information_platform import (
    AccountingValidationError,
    CAMT053_MESSAGE_DEFINITION,
    load_canonical_statement_fixture,
    parse_bank_statement_payload,
)


class BankStatementExplicitTimezoneRedTests(unittest.TestCase):
    """Do not invent UTC for source timestamps that carry no timezone."""

    def test_timezone_less_source_datetimes_fail_closed(self) -> None:
        """A local ISO dateTime cannot become an authoritative UTC instant by assumption."""
        fixture = load_canonical_statement_fixture()
        baseline = parse_bank_statement_payload(fixture, CAMT053_MESSAGE_DEFINITION)
        self.assertEqual(
            baseline.period_start_at,
            datetime(2026, 8, 23, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(
            baseline.entries[0].booking_occurred_at,
            datetime(2026, 8, 24, 1, 15, tzinfo=timezone.utc),
        )

        mutations = {
            "statement_period_start": (
                b"<FrDtTm>2026-08-23T00:00:00+00:00</FrDtTm>",
                b"<FrDtTm>2026-08-23T00:00:00</FrDtTm>",
            ),
            "entry_booking_datetime": (
                b"<DtTm>2026-08-24T01:15:00+00:00</DtTm>",
                b"<DtTm>2026-08-24T01:15:00</DtTm>",
            ),
        }

        for field, (source, hostile_value) in mutations.items():
            with self.subTest(field=field):
                self.assertEqual(fixture.count(source), 1)
                hostile = fixture.replace(source, hostile_value, 1)
                self.assertNotEqual(hostile, fixture)
                with self.assertRaisesRegex(
                    AccountingValidationError,
                    r"(?i:timezone)",
                ):
                    parse_bank_statement_payload(
                        hostile,
                        CAMT053_MESSAGE_DEFINITION,
                    )

    def test_explicit_non_utc_offset_is_normalized_without_losing_the_instant(self) -> None:
        """An explicit offset is unambiguous and may be normalized to UTC."""
        fixture = load_canonical_statement_fixture()
        source = b"<FrDtTm>2026-08-23T00:00:00+00:00</FrDtTm>"
        replacement = b"<FrDtTm>2026-08-23T00:00:00+09:00</FrDtTm>"
        self.assertEqual(fixture.count(source), 1)

        shifted = parse_bank_statement_payload(
            fixture.replace(source, replacement, 1),
            CAMT053_MESSAGE_DEFINITION,
        )

        self.assertEqual(
            shifted.period_start_at,
            datetime(2026, 8, 22, 15, 0, tzinfo=timezone.utc),
        )


if __name__ == "__main__":
    unittest.main()
