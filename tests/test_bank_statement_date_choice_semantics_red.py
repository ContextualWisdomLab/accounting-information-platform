"""RED contracts for lossless ISO 20022 date-or-dateTime entry evidence."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from accounting_information_platform import (
    CAMT053_MESSAGE_DEFINITION,
    load_canonical_statement_fixture,
    parse_bank_statement_payload,
)

_FIRST_VALUE_DATE = b"""        <ValDt>\n          <Dt>2026-08-24</Dt>\n        </ValDt>"""
_FIRST_VALUE_MIDNIGHT = b"""        <ValDt>\n          <DtTm>2026-08-24T00:00:00+00:00</DtTm>\n        </ValDt>"""
_SECOND_BOOKING_DATE = b"""        <BookgDt>\n          <Dt>2026-08-24</Dt>\n        </BookgDt>"""
_SECOND_BOOKING_MIDNIGHT = b"""        <BookgDt>\n          <DtTm>2026-08-24T00:00:00+00:00</DtTm>\n        </BookgDt>"""


class BankStatementDateChoiceSemanticsRedTests(unittest.TestCase):
    """Keep ISODate evidence distinct from an explicit midnight ISODateTime instant."""

    def test_value_date_remains_a_date_instead_of_becoming_midnight_utc(self) -> None:
        """A reported ValDt/Dt is a calendar date, not an inferred UTC instant."""
        fixture = load_canonical_statement_fixture()
        self.assertEqual(fixture.count(_FIRST_VALUE_DATE), 1)
        self.assertEqual(fixture.count(_SECOND_BOOKING_DATE), 1)

        # Isolate this choice: the other canonical date-only field is the same
        # explicit instant in both descendants.
        date_payload = fixture.replace(
            _SECOND_BOOKING_DATE,
            _SECOND_BOOKING_MIDNIGHT,
            1,
        )
        datetime_payload = date_payload.replace(
            _FIRST_VALUE_DATE,
            _FIRST_VALUE_MIDNIGHT,
            1,
        )

        date_statement = parse_bank_statement_payload(
            date_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        datetime_statement = parse_bank_statement_payload(
            datetime_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        date_entry = date_statement.entries[0]
        datetime_entry = datetime_statement.entries[0]

        self.assertIsNone(date_entry.value_occurred_at)
        self.assertEqual(getattr(date_entry, "value_date", None), date(2026, 8, 24))
        self.assertEqual(
            datetime_entry.value_occurred_at,
            datetime(2026, 8, 24, 0, 0, tzinfo=timezone.utc),
        )
        self.assertIsNone(getattr(datetime_entry, "value_date", None))
        self.assertNotEqual(date_entry.source_entry_hash, datetime_entry.source_entry_hash)
        self.assertNotEqual(
            date_statement.normalized_payload_hash,
            datetime_statement.normalized_payload_hash,
        )
        self.assertEqual(
            date_statement.entries[1].source_entry_hash,
            datetime_statement.entries[1].source_entry_hash,
        )

    def test_booking_date_remains_a_date_instead_of_becoming_midnight_utc(self) -> None:
        """A reported BookgDt/Dt must retain date semantics independently of DateTime."""
        fixture = load_canonical_statement_fixture()
        self.assertEqual(fixture.count(_FIRST_VALUE_DATE), 1)
        self.assertEqual(fixture.count(_SECOND_BOOKING_DATE), 1)

        # Isolate this choice: the other canonical date-only field is the same
        # explicit instant in both descendants.
        date_payload = fixture.replace(
            _FIRST_VALUE_DATE,
            _FIRST_VALUE_MIDNIGHT,
            1,
        )
        datetime_payload = date_payload.replace(
            _SECOND_BOOKING_DATE,
            _SECOND_BOOKING_MIDNIGHT,
            1,
        )

        date_statement = parse_bank_statement_payload(
            date_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        datetime_statement = parse_bank_statement_payload(
            datetime_payload,
            CAMT053_MESSAGE_DEFINITION,
        )
        date_entry = date_statement.entries[1]
        datetime_entry = datetime_statement.entries[1]

        self.assertIsNone(date_entry.booking_occurred_at)
        self.assertEqual(getattr(date_entry, "booking_date", None), date(2026, 8, 24))
        self.assertEqual(
            datetime_entry.booking_occurred_at,
            datetime(2026, 8, 24, 0, 0, tzinfo=timezone.utc),
        )
        self.assertIsNone(getattr(datetime_entry, "booking_date", None))
        self.assertNotEqual(date_entry.source_entry_hash, datetime_entry.source_entry_hash)
        self.assertNotEqual(
            date_statement.normalized_payload_hash,
            datetime_statement.normalized_payload_hash,
        )
        self.assertEqual(
            date_statement.entries[0].source_entry_hash,
            datetime_statement.entries[0].source_entry_hash,
        )


if __name__ == "__main__":
    unittest.main()
