"""Compatibility shim for the verified book-period normalization lane.

The one-shot repair is deliberately exact-match based. Its reviewed source lives
at the immutable RED-repair head below; this shim patches only matcher vocabulary
and formatting that drifted before the RED defect itself changed. The
normalization workflow still removes this temporary file before product
validation and publication.
"""

from __future__ import annotations

from pathlib import Path
import subprocess


SCRIPT_PATH = Path(__file__).resolve()
ROOT = SCRIPT_PATH.parents[1]
PERSISTENCE_PATH = ROOT / "src/accounting_information_platform/persistence.py"
POSTGRES_TEST_PATH = ROOT / "tests/test_postgres_posting.py"
BOOK_PERIOD_BOUNDARY_TEST_PATH = ROOT / "tests/test_book_period_helper_boundaries.py"
REPAIR_SOURCE_SHA = "fc4a9e60de914a62cc75c572cc424d99adb79aa9"
previous = subprocess.run(
    [
        "git",
        "show",
        f"{REPAIR_SOURCE_SHA}:scripts/repair_accounting_book_period_scope.py",
    ],
    check=True,
    capture_output=True,
    text=True,
).stdout


def replace_known_source_form(
    text: str,
    alternatives: tuple[str, ...],
    new: str,
    label: str,
) -> str:
    """Replace one known repair-source spelling while failing closed on drift."""
    matches = [(old, text.count(old)) for old in alternatives]
    matched = [(old, count) for old, count in matches if count]
    if len(matched) != 1:
        counts = ", ".join(str(count) for _old, count in matches)
        raise SystemExit(f"{label}: expected one known source spelling, counts={counts}")
    old, _count = matched[0]
    return text.replace(old, new)


def replace_exact(text: str, old: str, new: str, expected: int, label: str) -> str:
    """Replace an exact normalized boundary with an explicit expected count."""
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"{label}: expected {expected} exact boundaries, found {count}")
    return text.replace(old, new)


previous = replace_known_source_form(
    previous,
    (
        r'connection, tenant_id, policy.legal_entity_reference, \"the journal post\"',
        'connection, tenant_id, policy.legal_entity_reference, "the journal post"',
    ),
    "connection, tenant_id, proposal.legal_entity_reference",
    "legal-entity matcher",
)
previous = replace_known_source_form(
    previous,
    (
        r"policy.accounting_book_reference,\n                proposal.intended_book_role_code,",
        "policy.accounting_book_reference,\n                proposal.intended_book_role_code,",
    ),
    r"policy.intended_book_role_code,\n                policy.accounting_book_reference,",
    "accounting-book matcher",
)
previous = replace_known_source_form(
    previous,
    ("close_idempotency_key = idempotency_key or (",),
    "close_idempotency_key = idempotency_key.strip() or (",
    "close-idempotency matcher",
)

# close_fiscal_period gained a try/finally wrapper after the reviewed repair was
# written. Shift only the exact repair boundaries that live inside that try.
previous = replace_known_source_form(
    previous,
    (
        '"""            self._acquire_command_lock(connection, f"period:{period_code}")\\n""",',
    ),
    '"""                self._acquire_command_lock(\\n                    connection, f"period:{period_code}"\\n                )\\n""",',
    "close-lock old matcher",
)
previous = replace_known_source_form(
    previous,
    (
        '"""            self._acquire_command_lock(\\n                connection, f"period:{accounting_book_reference}:{period_code}"\\n            )\\n""",',
    ),
    '"""                self._acquire_command_lock(\\n                    connection, f"period:{accounting_book_reference}:{period_code}"\\n                )\\n""",',
    "close-lock replacement indentation",
)
previous = replace_known_source_form(
    previous,
    (
        '"""            period_id, current_status, period_end_date = self._lock_fiscal_period(\\n                connection, tenant_id, period_code\\n            )\\n""",',
    ),
    '"""                period_id, current_status, period_end_date = self._lock_fiscal_period(\\n                    connection, tenant_id, period_code\\n                )\\n""",',
    "close-period old matcher indentation",
)
previous = replace_known_source_form(
    previous,
    (
        '"""            period_id, current_status, period_end_date = self._lock_book_period(\\n                connection, tenant_id, book_id, period_code\\n            )\\n""",',
    ),
    '"""                period_id, current_status, period_end_date = self._lock_book_period(\\n                    connection, tenant_id, book_id, period_code\\n                )\\n""",',
    "close-period replacement indentation",
)
previous = replace_known_source_form(
    previous,
    ('marker = "## Unreleased\\n"',),
    'marker = "## [Unreleased]\\n"',
    "changelog unreleased marker",
)

# The reviewed loader matcher predates a second _import_psycopg() in _session.
# Hide only that non-loader occurrence while the exact repair executes, then
# restore it so no compatibility-only product diff survives normalization.
persistence = PERSISTENCE_PATH.read_text(encoding="utf-8")
session_import = "        psycopg = _import_psycopg()\n        try:\n            connection = psycopg.connect(self._database_url)\n"
shimmed_session_import = "        psycopg = _import_psycopg()  # repair-shim-session\n        try:\n            connection = psycopg.connect(self._database_url)\n"
if persistence.count(session_import) != 1:
    raise SystemExit("session import compatibility: expected one exact _session boundary")
PERSISTENCE_PATH.write_text(
    persistence.replace(session_import, shimmed_session_import, 1),
    encoding="utf-8",
)

namespace = {
    "__name__": "__main__",
    "__file__": str(SCRIPT_PATH),
}
exec(compile(previous, str(SCRIPT_PATH), "exec"), namespace)

normalized = PERSISTENCE_PATH.read_text(encoding="utf-8")
if normalized.count(shimmed_session_import) != 1:
    raise SystemExit("session import compatibility: temporary marker was not preserved exactly")
normalized = normalized.replace(shimmed_session_import, session_import, 1)

# Book-scoped close identity is externally stable and meaningful: use the
# published accounting-book reference, not an implementation UUID. Preserve the
# established next-action phrase while qualifying it to the selected book.
normalized = replace_exact(
    normalized,
    '            f"{period_code}:{book_id}"\n',
    '            f"{period_code}:{accounting_book_reference}"\n',
    1,
    "closing journal public identity",
)
normalized = replace_exact(
    normalized,
    '                f"{self._tenant_reference}:period_closing:{period_code}:{book_id}",\n',
    '                f"{self._tenant_reference}:period_closing:{period_code}:"\n'
    '                f"{accounting_book_reference}",\n',
    1,
    "closing proposal public identity",
)
normalized = replace_exact(
    normalized,
    '                "Open that book period or post into an open book period; "\n',
    '                "Open that period or post into an open period for this accounting book; "\n',
    1,
    "book-period operator next action",
)

# Once all close callers are book-scoped, the old calendar-wide mutators are
# dead production paths. Delete them instead of manufacturing coverage for
# behavior that must no longer be callable from the ledger implementation.
legacy_period_lock = '''    def _lock_fiscal_period(
        self,
        connection: object,
        tenant_id: UUID,
        period_code: str,
    ) -> tuple[UUID, str, date]:
        """Lock one fiscal period row while its close command evaluates and writes."""
        row = connection.execute(
            """
            SELECT fiscal_period_id, period_status_code, period_end_date
            FROM accounting_core.fiscal_period
            WHERE tenant_account_id = %s AND period_code = %s
            FOR UPDATE
            """,
            (tenant_id, period_code),
        ).fetchone()
        if row is None:
            raise AccountingValidationError(
                f"Fiscal period {period_code} is not recorded for this tenant. "
                "Create the fiscal_period row, then retry the close."
            )
        return row[0], row[1], row[2]

'''
normalized = replace_exact(
    normalized,
    legacy_period_lock,
    "",
    1,
    "remove obsolete calendar-wide period close lock",
)
legacy_period_writer = '''    def _set_period_closed(
        self,
        connection: object,
        tenant_id: UUID,
        period_id: UUID,
        period_status_code: str,
    ) -> datetime:
        return connection.execute(
            """
            UPDATE accounting_core.fiscal_period
            SET period_status_code = %s,
                period_closed_at = clock_timestamp()
            WHERE tenant_account_id = %s AND fiscal_period_id = %s
            RETURNING period_closed_at
            """,
            (period_status_code, tenant_id, period_id),
        ).fetchone()[0]

'''
normalized = replace_exact(
    normalized,
    legacy_period_writer,
    "",
    1,
    "remove obsolete calendar-wide period close writer",
)
PERSISTENCE_PATH.write_text(normalized, encoding="utf-8")

# Existing tests that address the AIS-owned closing proposal by its durable
# idempotency key must include the newly explicit book scope. This is a contract
# update, not a relaxation: the same tenant can close sibling books independently.
postgres_tests = POSTGRES_TEST_PATH.read_text(encoding="utf-8")
old_closing_key = 'f"{self.policy.tenant_reference}:period_closing:2026-08"'
new_closing_key = (
    'f"{self.policy.tenant_reference}:period_closing:2026-08:'
    '{self.policy.accounting_book_reference}"'
)
closing_key_count = postgres_tests.count(old_closing_key)
if closing_key_count < 4:
    raise SystemExit(
        "closing proposal test identity: expected at least four established references, "
        f"found {closing_key_count}"
    )
POSTGRES_TEST_PATH.write_text(
    postgres_tests.replace(old_closing_key, new_closing_key),
    encoding="utf-8",
)

# Exercise each new fail-closed book-period helper boundary directly while the
# end-to-end PostgreSQL regression continues to prove the buyer-visible defect.
BOOK_PERIOD_BOUNDARY_TEST_PATH.write_text(
    '''"""Fail-closed branch tests for accounting-book period control helpers."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from accounting_information_platform.persistence import (
    AccountingValidationError,
    PostgresPostingLedger,
    apply_foundation_migration,
)


class _ScriptedConnection:
    """Minimal connection double whose fetch results are supplied in order."""

    def __init__(self, rows: list[object | None]) -> None:
        self._rows = iter(rows)
        self.executed: list[tuple[str, object | None]] = []

    def execute(self, statement: str, params: object | None = None) -> "_ScriptedConnection":
        self.executed.append((statement, params))
        return self

    def fetchone(self) -> object | None:
        return next(self._rows)


class BookPeriodHelperBoundaryTests(unittest.TestCase):
    """Cover fail-closed book-period branches without weakening integration tests."""

    def setUp(self) -> None:
        self.ledger = PostgresPostingLedger(
            "postgresql://unused",
            tenant_reference="urn:cwl:tenant:coverage",
        )
        self.tenant_id = uuid4()
        self.book_id = uuid4()
        self.period_id = uuid4()
        self.accounting_date = date(2026, 8, 31)

    def _period_row(self, status: str = "open") -> tuple[object, ...]:
        return (
            self.period_id,
            "2026-08",
            status,
            date(2026, 8, 1),
            date(2026, 8, 31),
        )

    def test_open_book_period_rejects_missing_initial_period(self) -> None:
        connection = _ScriptedConnection([None])
        with self.assertRaisesRegex(AccountingValidationError, "No fiscal period covers"):
            self.ledger._require_open_book_period_bounds(
                connection, self.tenant_id, self.book_id, self.accounting_date
            )

    def test_open_book_period_rejects_period_removed_after_lock(self) -> None:
        connection = _ScriptedConnection([self._period_row(), None])
        with self.assertRaisesRegex(AccountingValidationError, "No fiscal period covers"):
            self.ledger._require_open_book_period_bounds(
                connection, self.tenant_id, self.book_id, self.accounting_date
            )

    def test_open_book_period_reports_soft_and_hard_close_distinctly(self) -> None:
        for status, expected in (
            ("soft_closed", "soft_closed"),
            ("hard_closed", "hard_closed \\(period_closed\\)"),
        ):
            with self.subTest(status=status):
                connection = _ScriptedConnection(
                    [self._period_row(status), self._period_row(status)]
                )
                with self.assertRaisesRegex(AccountingValidationError, expected):
                    self.ledger._require_open_book_period_bounds(
                        connection,
                        self.tenant_id,
                        self.book_id,
                        self.accounting_date,
                    )

    def test_open_book_period_returns_locked_bounds(self) -> None:
        connection = _ScriptedConnection([self._period_row(), self._period_row()])
        self.assertEqual(
            self.ledger._require_open_book_period_bounds(
                connection, self.tenant_id, self.book_id, self.accounting_date
            ),
            (self.period_id, date(2026, 8, 1), date(2026, 8, 31)),
        )

    def test_lock_book_period_rejects_missing_calendar_period(self) -> None:
        connection = _ScriptedConnection([None])
        with self.assertRaisesRegex(AccountingValidationError, "not recorded for this tenant"):
            self.ledger._lock_book_period(
                connection, self.tenant_id, self.book_id, "2026-08"
            )

    def test_lock_book_period_rejects_missing_control_row(self) -> None:
        connection = _ScriptedConnection(
            [(self.period_id, "open", None), None]
        )
        with self.assertRaisesRegex(AccountingValidationError, "no control row"):
            self.ledger._lock_book_period(
                connection, self.tenant_id, self.book_id, "2026-08"
            )

    def test_load_book_period_state_preserves_missing_state(self) -> None:
        connection = _ScriptedConnection([None])
        self.assertIsNone(
            self.ledger._load_book_period_state(
                connection, self.tenant_id, self.book_id, "2026-08"
            )
        )

    def test_set_book_period_closed_covers_open_and_closed_aggregate_states(self) -> None:
        closed_at = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
        for aggregate_row in ((None, None), ("soft_closed", closed_at)):
            with self.subTest(aggregate_row=aggregate_row):
                connection = _ScriptedConnection([(closed_at,), aggregate_row])
                self.assertEqual(
                    self.ledger._set_book_period_closed(
                        connection,
                        self.tenant_id,
                        self.book_id,
                        self.period_id,
                        "soft_closed",
                    ),
                    closed_at,
                )

    def test_migration_loader_fails_closed_when_book_period_migration_is_missing(self) -> None:
        migration_names = (
            "0001_accounting_foundation.sql",
            "0002_chart_account_class.sql",
            "0003_home_tax_submission.sql",
            "0004_close_idempotency_key.sql",
            "0005_closed_period_guard.sql",
            "0006_concurrency_hot_partition.sql",
            "0007_runtime_tenant_binding.sql",
            "0008_fiscal_period_open_command.sql",
        )
        with tempfile.TemporaryDirectory() as directory:
            migration_root = Path(directory)
            for migration_name in migration_names:
                (migration_root / migration_name).write_text("-- test fixture\\n", encoding="utf-8")
            with self.assertRaisesRegex(AccountingValidationError, "0009_accounting_book_period_control.sql"):
                apply_foundation_migration(
                    "postgresql://unused",
                    migration_root / "0001_accounting_foundation.sql",
                )


if __name__ == "__main__":
    unittest.main()
''',
    encoding="utf-8",
)
