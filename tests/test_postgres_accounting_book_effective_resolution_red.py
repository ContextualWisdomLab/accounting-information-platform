"""Real PostgreSQL REDs for effective-time Accounting Book reference resolution."""

from __future__ import annotations

from inspect import signature
import unittest

from accounting_information_platform.persistence import AccountingValidationError

from tests import test_postgres_posting as posting


class PostgresAccountingBookEffectiveResolutionRedTests(unittest.TestCase):
    """Require durable book references to resolve by the selected effective interval."""

    @classmethod
    def setUpClass(cls) -> None:
        """Reuse the canonical PostgreSQL integration fixture for resolver REDs."""
        posting.PostgresPostingTests.setUpClass()

    def setUp(self) -> None:
        """Create isolated accounting master data with ordinary cleanup semantics."""
        self.case = posting.PostgresPostingTests("setUp")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.addCleanup(self.case.tearDown)

    def _legal_entity_id(self, connection: object) -> object:
        """Return the fixture legal-entity key without inferring another book identity."""
        return connection.execute(
            """
            SELECT legal_entity_id
            FROM accounting_core.legal_entity_record
            WHERE tenant_account_id = %s
              AND legal_entity_code = %s
              AND valid_from <= clock_timestamp()
              AND (valid_to IS NULL OR valid_to > clock_timestamp())
            """,
            (
                self.case.tenant_id,
                self.case.policy.legal_entity_reference,
            ),
        ).fetchone()[0]

    def _delete_book_reference(self, book_reference: str) -> None:
        """Remove test-owned Accounting Book rows after a committed fixture session."""
        with self.case.ledger._session() as connection:
            connection.execute(
                """
                DELETE FROM accounting_core.accounting_book
                WHERE tenant_account_id = %s AND book_name = %s
                """,
                (self.case.tenant_id, book_reference),
            )

    def test_future_open_ended_book_reference_is_not_effective_yet(self) -> None:
        """A scheduled future book must not resolve merely because ``valid_to`` is NULL."""
        future_reference = f"{self.case.policy.accounting_book_reference}-future"
        self.addCleanup(self._delete_book_reference, future_reference)

        with self.case.ledger._session() as connection:
            legal_entity_id = self._legal_entity_id(connection)
            connection.execute(
                """
                INSERT INTO accounting_core.accounting_book (
                    tenant_account_id,
                    legal_entity_id,
                    book_role_code,
                    book_name,
                    reporting_currency_code,
                    valid_from
                ) VALUES (%s, %s, 'management', %s, 'KRW', clock_timestamp() + interval '30 days')
                """,
                (
                    self.case.tenant_id,
                    legal_entity_id,
                    future_reference,
                ),
            )

            with self.assertRaises(AccountingValidationError):
                self.case.ledger._require_book_for_close(
                    connection,
                    self.case.tenant_id,
                    legal_entity_id,
                    future_reference,
                    "the effective accounting-book read",
                )

    def test_finite_book_reference_effective_now_resolves(self) -> None:
        """A currently effective finite interval must resolve before its scheduled end."""
        finite_reference = f"{self.case.policy.accounting_book_reference}-finite"
        self.addCleanup(self._delete_book_reference, finite_reference)

        with self.case.ledger._session() as connection:
            legal_entity_id = self._legal_entity_id(connection)
            book_id = connection.execute(
                """
                INSERT INTO accounting_core.accounting_book (
                    tenant_account_id,
                    legal_entity_id,
                    book_role_code,
                    book_name,
                    reporting_currency_code,
                    valid_from,
                    valid_to
                ) VALUES (
                    %s,
                    %s,
                    'management',
                    %s,
                    'KRW',
                    clock_timestamp() - interval '1 day',
                    clock_timestamp() + interval '1 day'
                )
                RETURNING accounting_book_id
                """,
                (
                    self.case.tenant_id,
                    legal_entity_id,
                    finite_reference,
                ),
            ).fetchone()[0]

            self.assertEqual(
                self.case.ledger._require_book_for_close(
                    connection,
                    self.case.tenant_id,
                    legal_entity_id,
                    finite_reference,
                    "the effective accounting-book read",
                ),
                (book_id, "KRW"),
            )

    def test_explicit_historical_effective_instant_selects_historical_entity(self) -> None:
        """Historical accounting reads must resolve the Entity effective at their own instant."""
        historical_reference = f"{self.case.policy.accounting_book_reference}-historical"
        self.addCleanup(self._delete_book_reference, historical_reference)

        with self.case.ledger._session() as connection:
            legal_entity_id = self._legal_entity_id(connection)
            effective_at = connection.execute(
                "SELECT clock_timestamp() - interval '36 hours'"
            ).fetchone()[0]
            historical_id = connection.execute(
                """
                INSERT INTO accounting_core.accounting_book (
                    tenant_account_id,
                    legal_entity_id,
                    book_role_code,
                    book_name,
                    reporting_currency_code,
                    valid_from,
                    valid_to
                ) VALUES (
                    %s,
                    %s,
                    'management',
                    %s,
                    'KRW',
                    %s - interval '1 hour',
                    %s + interval '1 hour'
                )
                RETURNING accounting_book_id
                """,
                (
                    self.case.tenant_id,
                    legal_entity_id,
                    historical_reference,
                    effective_at,
                    effective_at,
                ),
            ).fetchone()[0]
            successor_id = connection.execute(
                """
                INSERT INTO accounting_core.accounting_book (
                    tenant_account_id,
                    legal_entity_id,
                    book_role_code,
                    book_name,
                    reporting_currency_code,
                    valid_from
                ) VALUES (
                    %s,
                    %s,
                    'statutory',
                    %s,
                    'KRW',
                    %s + interval '1 hour'
                )
                RETURNING accounting_book_id
                """,
                (
                    self.case.tenant_id,
                    legal_entity_id,
                    historical_reference,
                    effective_at,
                ),
            ).fetchone()[0]

            self.assertNotEqual(historical_id, successor_id)
            self.assertIn(
                "effective_at",
                signature(self.case.ledger._require_book_for_close).parameters,
                "book-reference resolution must accept the owning use case's accounting-effective instant",
            )
            self.assertEqual(
                self.case.ledger._require_book_for_close(
                    connection,
                    self.case.tenant_id,
                    legal_entity_id,
                    historical_reference,
                    "the historical accounting-book read",
                    effective_at=effective_at,
                ),
                (historical_id, "KRW"),
            )


if __name__ == "__main__":
    unittest.main()
