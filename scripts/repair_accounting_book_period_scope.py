"""One-shot exact-head repair for accounting-book fiscal-period close isolation.

This file is temporary repair machinery. The repair workflow removes it before
product coverage and commits only the normalized source/docs/migration changes.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PERSISTENCE = ROOT / "src/accounting_information_platform/persistence.py"
VALIDATOR = ROOT / "scripts/validate_repository.py"
OPERABILITY = ROOT / "docs/OPERABILITY.md"
DATA_MODEL = ROOT / "docs/DATA_MODEL.md"
ARCHITECTURE = ROOT / "docs/ARCHITECTURE.md"
CHANGELOG = ROOT / "CHANGELOG.md"
MIGRATION = ROOT / "database/migrations/0009_accounting_book_period_control.sql"
ADR = ROOT / "docs/adr/0051-accounting-book-period-control.md"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    """Replace one exact source boundary or fail closed when the tree drifted."""
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one exact boundary, found {count}")
    return text.replace(old, new, 1)


persistence = PERSISTENCE.read_text(encoding="utf-8")

persistence = replace_once(
    persistence,
    """            period_id = self._require_open_period(\n                connection, tenant_id, proposal.accounting_date\n            )\n            legal_entity_id = self._require_legal_entity(\n                connection, tenant_id, policy.legal_entity_reference, \"the journal post\"\n            )\n            book_id = self._require_book(\n                connection,\n                tenant_id,\n                legal_entity_id,\n                policy.accounting_book_reference,\n                proposal.intended_book_role_code,\n            )\n""",
    """            legal_entity_id = self._require_legal_entity(\n                connection, tenant_id, policy.legal_entity_reference, \"the journal post\"\n            )\n            book_id = self._require_book(\n                connection,\n                tenant_id,\n                legal_entity_id,\n                policy.accounting_book_reference,\n                proposal.intended_book_role_code,\n            )\n            period_id = self._require_open_book_period(\n                connection, tenant_id, book_id, proposal.accounting_date\n            )\n""",
    "ordinary posting resolves book before period admission",
)

persistence = replace_once(
    persistence,
    """        _period_id, period_start, period_end = self._require_open_period_bounds(\n            connection, tenant_id, proposal.accounting_date\n        )\n""",
    """        _period_id, period_start, period_end = self._require_open_book_period_bounds(\n            connection, tenant_id, book_id, proposal.accounting_date\n        )\n""",
    "catalog policy uses book-scoped period admission",
)

book_period_helpers = '''    def _require_open_book_period(\n        self,\n        connection: object,\n        tenant_id: UUID,\n        book_id: UUID,\n        accounting_date: date,\n    ) -> UUID:\n        """Require an open fiscal period for the selected accounting book."""\n        return self._require_open_book_period_bounds(\n            connection, tenant_id, book_id, accounting_date\n        )[0]\n\n    def _require_open_book_period_bounds(\n        self,\n        connection: object,\n        tenant_id: UUID,\n        book_id: UUID,\n        accounting_date: date,\n    ) -> tuple[UUID, date, date]:\n        """Return period identity and bounds when this accounting book is open."""\n        row = connection.execute(\n            """\n            SELECT fiscal_period.fiscal_period_id,\n                   fiscal_period.period_code,\n                   COALESCE(\n                       accounting_book_period_control.period_status_code,\n                       fiscal_period.period_status_code\n                   ),\n                   fiscal_period.period_start_date,\n                   fiscal_period.period_end_date\n            FROM accounting_core.fiscal_period\n            LEFT JOIN accounting_core.accounting_book_period_control\n              ON accounting_book_period_control.tenant_account_id\n                 = fiscal_period.tenant_account_id\n             AND accounting_book_period_control.fiscal_period_id\n                 = fiscal_period.fiscal_period_id\n             AND accounting_book_period_control.accounting_book_id = %s\n            WHERE fiscal_period.tenant_account_id = %s\n              AND fiscal_period.period_start_date <= %s\n              AND fiscal_period.period_end_date >= %s\n            """,\n            (book_id, tenant_id, accounting_date, accounting_date),\n        ).fetchone()\n        if row is None:\n            raise AccountingValidationError(\n                f"No fiscal period covers accounting date {accounting_date.isoformat()}. "\n                "Create an open fiscal period on the tenant calendar, then retry posting."\n            )\n        period_id, period_code = row[0], row[1]\n        self._acquire_command_lock(connection, f"period:{book_id}:{period_code}")\n        row = connection.execute(\n            """\n            SELECT fiscal_period.fiscal_period_id,\n                   fiscal_period.period_code,\n                   COALESCE(\n                       accounting_book_period_control.period_status_code,\n                       fiscal_period.period_status_code\n                   ),\n                   fiscal_period.period_start_date,\n                   fiscal_period.period_end_date\n            FROM accounting_core.fiscal_period\n            LEFT JOIN accounting_core.accounting_book_period_control\n              ON accounting_book_period_control.tenant_account_id\n                 = fiscal_period.tenant_account_id\n             AND accounting_book_period_control.fiscal_period_id\n                 = fiscal_period.fiscal_period_id\n             AND accounting_book_period_control.accounting_book_id = %s\n            WHERE fiscal_period.tenant_account_id = %s\n              AND fiscal_period.fiscal_period_id = %s\n            """,\n            (book_id, tenant_id, period_id),\n        ).fetchone()\n        if row is None:\n            raise AccountingValidationError(\n                f"No fiscal period covers accounting date {accounting_date.isoformat()}. "\n                "Create an open fiscal period on the tenant calendar, then retry posting."\n            )\n        if row[2] != "open":\n            locked_marker = " (period_closed)" if row[2] == "hard_closed" else ""\n            raise AccountingValidationError(\n                f"Fiscal period {row[1]} is {row[2]}{locked_marker}. "\n                "Open that book period or post into an open book period; "\n                "no journal was written."\n            )\n        return row[0], row[3], row[4]\n\n'''

persistence = replace_once(
    persistence,
    "    def _require_open_period(\n",
    book_period_helpers + "    def _require_open_period(\n",
    "insert book-period posting helpers",
)

persistence = replace_once(
    persistence,
    """            period_state = self._load_period_state(connection, tenant_id, period_code)\n""",
    """            period_state = self._load_book_period_state(\n                connection, tenant_id, book_id, period_code\n            )\n""",
    "adjusting journal uses book-period state",
)

load_book_period_state = '''    def _load_book_period_state(\n        self,\n        connection: object,\n        tenant_id: UUID,\n        book_id: UUID,\n        period_code: str,\n    ) -> tuple[UUID, str, date, date] | None:\n        """Return the selected book's period state, falling back to legacy calendar state."""\n        row = connection.execute(\n            """\n            SELECT fiscal_period.fiscal_period_id,\n                   COALESCE(\n                       accounting_book_period_control.period_status_code,\n                       fiscal_period.period_status_code\n                   ),\n                   fiscal_period.period_start_date,\n                   fiscal_period.period_end_date\n            FROM accounting_core.fiscal_period\n            LEFT JOIN accounting_core.accounting_book_period_control\n              ON accounting_book_period_control.tenant_account_id\n                 = fiscal_period.tenant_account_id\n             AND accounting_book_period_control.fiscal_period_id\n                 = fiscal_period.fiscal_period_id\n             AND accounting_book_period_control.accounting_book_id = %s\n            WHERE fiscal_period.tenant_account_id = %s\n              AND fiscal_period.period_code = %s\n            """,\n            (book_id, tenant_id, period_code),\n        ).fetchone()\n        if row is None:\n            return None\n        return row[0], row[1], row[2], row[3]\n\n'''

persistence = replace_once(
    persistence,
    "    def _load_period_state(\n",
    load_book_period_state + "    def _load_period_state(\n",
    "insert book-period state loader",
)

persistence = replace_once(
    persistence,
    """        close_idempotency_key = idempotency_key or (\n            f"{self._tenant_reference}:period_close:{period_code}"\n        )\n""",
    """        close_idempotency_key = idempotency_key or (\n            f"{self._tenant_reference}:period_close:{accounting_book_reference}:{period_code}"\n        )\n""",
    "default close identity includes accounting book",
)

persistence = replace_once(
    persistence,
    """            self._acquire_command_lock(connection, f"period:{period_code}")\n""",
    """            self._acquire_command_lock(\n                connection, f"period:{accounting_book_reference}:{period_code}"\n            )\n""",
    "close command lock includes accounting book",
)

persistence = replace_once(
    persistence,
    """            period_id, current_status, period_end_date = self._lock_fiscal_period(\n                connection, tenant_id, period_code\n            )\n""",
    """            period_id, current_status, period_end_date = self._lock_book_period(\n                connection, tenant_id, book_id, period_code\n            )\n""",
    "close locks selected book period",
)

lock_book_period = '''    def _lock_book_period(\n        self,\n        connection: object,\n        tenant_id: UUID,\n        book_id: UUID,\n        period_code: str,\n    ) -> tuple[UUID, str, date]:\n        """Materialize and lock close state independently for one accounting book."""\n        period_row = connection.execute(\n            """\n            SELECT fiscal_period_id, period_status_code, period_closed_at\n            FROM accounting_core.fiscal_period\n            WHERE tenant_account_id = %s AND period_code = %s\n            """,\n            (tenant_id, period_code),\n        ).fetchone()\n        if period_row is None:\n            raise AccountingValidationError(\n                f"Fiscal period {period_code} is not recorded for this tenant. "\n                "Create the fiscal_period row, then retry the close."\n            )\n        period_id = period_row[0]\n        connection.execute(\n            """\n            INSERT INTO accounting_core.accounting_book_period_control (\n                tenant_account_id, accounting_book_id, fiscal_period_id,\n                period_status_code, period_closed_at\n            )\n            SELECT accounting_book.tenant_account_id,\n                   accounting_book.accounting_book_id,\n                   fiscal_period.fiscal_period_id,\n                   fiscal_period.period_status_code,\n                   fiscal_period.period_closed_at\n            FROM accounting_core.accounting_book\n            JOIN accounting_core.fiscal_period\n              ON fiscal_period.tenant_account_id = accounting_book.tenant_account_id\n            WHERE accounting_book.tenant_account_id = %s\n              AND accounting_book.valid_to IS NULL\n              AND fiscal_period.fiscal_period_id = %s\n            ON CONFLICT (tenant_account_id, accounting_book_id, fiscal_period_id)\n            DO NOTHING\n            """,\n            (tenant_id, period_id),\n        )\n        row = connection.execute(\n            """\n            SELECT fiscal_period.fiscal_period_id,\n                   accounting_book_period_control.period_status_code,\n                   fiscal_period.period_end_date\n            FROM accounting_core.fiscal_period\n            JOIN accounting_core.accounting_book_period_control\n              ON accounting_book_period_control.tenant_account_id\n                 = fiscal_period.tenant_account_id\n             AND accounting_book_period_control.fiscal_period_id\n                 = fiscal_period.fiscal_period_id\n             AND accounting_book_period_control.accounting_book_id = %s\n            WHERE fiscal_period.tenant_account_id = %s\n              AND fiscal_period.fiscal_period_id = %s\n            FOR UPDATE OF accounting_book_period_control\n            """,\n            (book_id, tenant_id, period_id),\n        ).fetchone()\n        if row is None:\n            raise AccountingValidationError(\n                f"Fiscal period {period_code} has no control row for this accounting book. "\n                "Repair accounting_book_period_control, then retry the close."\n            )\n        return row[0], row[1], row[2]\n\n'''

persistence = replace_once(
    persistence,
    "    def _load_book_period_state(\n",
    lock_book_period + "    def _load_book_period_state(\n",
    "insert book-period close locker",
)

persistence = replace_once(
    persistence,
    """        period_closed_at = connection.execute(\n            \"\"\"\n            SELECT COALESCE(period_closed_at, clock_timestamp())\n            FROM accounting_core.fiscal_period\n            WHERE tenant_account_id = %s AND fiscal_period_id = %s\n            \"\"\",\n            (tenant_id, period_id),\n        ).fetchone()[0]\n""",
    """        period_closed_at = connection.execute(\n            \"\"\"\n            SELECT COALESCE(period_closed_at, clock_timestamp())\n            FROM accounting_core.accounting_book_period_control\n            WHERE tenant_account_id = %s\n              AND accounting_book_id = %s\n              AND fiscal_period_id = %s\n            \"\"\",\n            (tenant_id, book_id, period_id),\n        ).fetchone()[0]\n""",
    "soft-close replay timestamp is book scoped",
)

persistence = replace_once(
    persistence,
    """        period_closed_at = self._set_period_closed(\n            connection, tenant_id, period_id, \"soft_closed\"\n        )\n""",
    """        period_closed_at = self._set_book_period_closed(\n            connection, tenant_id, book_id, period_id, \"soft_closed\"\n        )\n""",
    "soft close persists book control",
)

persistence = replace_once(
    persistence,
    """        self._set_period_closed(connection, tenant_id, period_id, period_status_code)\n""",
    """        self._set_book_period_closed(\n            connection, tenant_id, book_id, period_id, period_status_code\n        )\n""",
    "hard close persists book control",
)

set_book_period_closed = '''    def _set_book_period_closed(\n        self,\n        connection: object,\n        tenant_id: UUID,\n        book_id: UUID,\n        period_id: UUID,\n        period_status_code: str,\n    ) -> datetime:\n        """Close one book and retain aggregate calendar status only for compatibility."""\n        period_closed_at = connection.execute(\n            """\n            UPDATE accounting_core.accounting_book_period_control\n            SET period_status_code = %s,\n                period_closed_at = clock_timestamp()\n            WHERE tenant_account_id = %s\n              AND accounting_book_id = %s\n              AND fiscal_period_id = %s\n            RETURNING period_closed_at\n            """,\n            (period_status_code, tenant_id, book_id, period_id),\n        ).fetchone()[0]\n        aggregate_row = connection.execute(\n            """\n            SELECT CASE\n                       WHEN bool_and(\n                           accounting_book_period_control.period_status_code = 'hard_closed'\n                       ) THEN 'hard_closed'\n                       WHEN bool_and(\n                           accounting_book_period_control.period_status_code <> 'open'\n                       ) THEN 'soft_closed'\n                       ELSE 'open'\n                   END,\n                   max(accounting_book_period_control.period_closed_at)\n            FROM accounting_core.accounting_book_period_control\n            JOIN accounting_core.accounting_book\n              ON accounting_book.tenant_account_id\n                 = accounting_book_period_control.tenant_account_id\n             AND accounting_book.accounting_book_id\n                 = accounting_book_period_control.accounting_book_id\n            WHERE accounting_book_period_control.tenant_account_id = %s\n              AND accounting_book_period_control.fiscal_period_id = %s\n              AND accounting_book.valid_to IS NULL\n            """,\n            (tenant_id, period_id),\n        ).fetchone()\n        aggregate_status = aggregate_row[0] or "open"\n        aggregate_closed_at = None if aggregate_status == "open" else aggregate_row[1]\n        connection.execute(\n            """\n            UPDATE accounting_core.fiscal_period\n            SET period_status_code = %s,\n                period_closed_at = %s\n            WHERE tenant_account_id = %s AND fiscal_period_id = %s\n            """,\n            (aggregate_status, aggregate_closed_at, tenant_id, period_id),\n        )\n        return period_closed_at\n\n'''

persistence = replace_once(
    persistence,
    "    def _set_period_closed(\n",
    set_book_period_closed + "    def _set_period_closed(\n",
    "insert book-period close writer",
)

persistence = replace_once(
    persistence,
    """        closing_reference = (\n            f\"urn:cwl:accounting:general_journal:period_closing:{period_code}\"\n        )\n""",
    """        closing_reference = (\n            \"urn:cwl:accounting:general_journal:period_closing:\"\n            f\"{period_code}:{book_id}\"\n        )\n""",
    "closing journal identity includes book",
)

persistence = replace_once(
    persistence,
    """                f\"{self._tenant_reference}:period_closing:{period_code}\",\n""",
    """                f\"{self._tenant_reference}:period_closing:{period_code}:{book_id}\",\n""",
    "closing command identity includes book",
)

# Hard-close inquiry status must be the snapshot book's own control state.
persistence = replace_once(
    persistence,
    """                       fiscal_period.period_code,\n                       fiscal_period.period_status_code,\n                       accounting_book.book_name,\n""",
    """                       fiscal_period.period_code,\n                       accounting_book_period_control.period_status_code,\n                       accounting_book.book_name,\n""",
    "period-close list selects book status",
)
persistence = replace_once(
    persistence,
    """                JOIN accounting_core.accounting_book\n                  ON accounting_book.tenant_account_id = trial_balance_snapshot.tenant_account_id\n                 AND accounting_book.accounting_book_id = trial_balance_snapshot.accounting_book_id\n""",
    """                JOIN accounting_core.accounting_book\n                  ON accounting_book.tenant_account_id = trial_balance_snapshot.tenant_account_id\n                 AND accounting_book.accounting_book_id = trial_balance_snapshot.accounting_book_id\n                JOIN accounting_core.accounting_book_period_control\n                  ON accounting_book_period_control.tenant_account_id\n                     = trial_balance_snapshot.tenant_account_id\n                 AND accounting_book_period_control.accounting_book_id\n                     = trial_balance_snapshot.accounting_book_id\n                 AND accounting_book_period_control.fiscal_period_id\n                     = trial_balance_snapshot.fiscal_period_id\n""",
    "period-close list joins book control",
)
persistence = replace_once(
    persistence,
    """                  AND (%s OR fiscal_period.period_status_code = %s)\n""",
    """                  AND (%s OR accounting_book_period_control.period_status_code = %s)\n""",
    "period-close list filters book status",
)

# Require and execute migration 0009 in the canonical foundation loader.
persistence = replace_once(
    persistence,
    """    psycopg = _import_psycopg()\n""",
    """    book_period_control_migration_path = (\n        migration_path.parent / \"0009_accounting_book_period_control.sql\"\n    )\n    if not book_period_control_migration_path.is_file():\n        raise AccountingValidationError(\n            f\"Accounting-book-period control migration is missing at {book_period_control_migration_path}. \"\n            \"Restore database/migrations/0009_accounting_book_period_control.sql, then retry.\"\n        )\n    psycopg = _import_psycopg()\n""",
    "loader requires migration 0009",
)
persistence = replace_once(
    persistence,
    """            connection.execute(period_open_command_migration_path.read_text(encoding=\"utf-8\"))\n""",
    """            connection.execute(period_open_command_migration_path.read_text(encoding=\"utf-8\"))\n            connection.execute(book_period_control_migration_path.read_text(encoding=\"utf-8\"))\n""",
    "loader executes migration 0009",
)

PERSISTENCE.write_text(persistence, encoding="utf-8")

MIGRATION.write_text(
    '''BEGIN;\n\nCREATE TABLE accounting_core.accounting_book_period_control (\n    accounting_book_period_control_id uuid PRIMARY KEY DEFAULT uuidv7(),\n    tenant_account_id uuid NOT NULL,\n    accounting_book_id uuid NOT NULL,\n    fiscal_period_id uuid NOT NULL,\n    period_status_code text NOT NULL\n        CHECK (period_status_code IN ('open', 'soft_closed', 'hard_closed')),\n    period_closed_at timestamptz,\n    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),\n    FOREIGN KEY (tenant_account_id, accounting_book_id)\n        REFERENCES accounting_core.accounting_book (tenant_account_id, accounting_book_id),\n    FOREIGN KEY (tenant_account_id, fiscal_period_id)\n        REFERENCES accounting_core.fiscal_period (tenant_account_id, fiscal_period_id),\n    UNIQUE (tenant_account_id, accounting_book_id, fiscal_period_id),\n    UNIQUE (tenant_account_id, accounting_book_period_control_id)\n);\n\nCREATE INDEX accounting_book_period_scope_index\n    ON accounting_core.accounting_book_period_control (\n        tenant_account_id, accounting_book_id, fiscal_period_id, period_status_code\n    );\n\nALTER TABLE accounting_core.accounting_book_period_control ENABLE ROW LEVEL SECURITY;\nALTER TABLE accounting_core.accounting_book_period_control FORCE ROW LEVEL SECURITY;\nCREATE POLICY accounting_book_period_isolation\n    ON accounting_core.accounting_book_period_control\n    USING (tenant_account_id = accounting_core.current_tenant_account_id())\n    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());\n\nINSERT INTO accounting_core.accounting_book_period_control (\n    tenant_account_id, accounting_book_id, fiscal_period_id,\n    period_status_code, period_closed_at\n)\nSELECT accounting_book.tenant_account_id,\n       accounting_book.accounting_book_id,\n       fiscal_period.fiscal_period_id,\n       fiscal_period.period_status_code,\n       fiscal_period.period_closed_at\nFROM accounting_core.accounting_book\nJOIN accounting_core.fiscal_period\n  ON fiscal_period.tenant_account_id = accounting_book.tenant_account_id\nWHERE accounting_book.valid_to IS NULL\nON CONFLICT (tenant_account_id, accounting_book_id, fiscal_period_id) DO NOTHING;\n\nCREATE OR REPLACE FUNCTION accounting_core.guard_period_insert()\nRETURNS trigger\nLANGUAGE plpgsql\nSECURITY DEFINER\nSET search_path = pg_catalog, accounting_core\nAS $$\nDECLARE\n    period_status_value text;\n    journal_write_role_value text;\nBEGIN\n    SELECT COALESCE(\n               accounting_book_period_control.period_status_code,\n               fiscal_period.period_status_code\n           )\n      INTO period_status_value\n      FROM accounting_core.fiscal_period\n      LEFT JOIN accounting_core.accounting_book_period_control\n        ON accounting_book_period_control.tenant_account_id\n           = fiscal_period.tenant_account_id\n       AND accounting_book_period_control.fiscal_period_id\n           = fiscal_period.fiscal_period_id\n       AND accounting_book_period_control.accounting_book_id = NEW.accounting_book_id\n     WHERE fiscal_period.tenant_account_id = NEW.tenant_account_id\n       AND fiscal_period.fiscal_period_id = NEW.fiscal_period_id;\n\n    IF period_status_value IS NULL THEN\n        RAISE EXCEPTION\n            'fiscal period is missing for this accounting book journal insert (period_closed)'\n            USING ERRCODE = 'check_violation';\n    END IF;\n\n    IF period_status_value = 'open' THEN\n        RETURN NEW;\n    END IF;\n\n    journal_write_role_value := nullif(\n        current_setting('accounting_core.journal_write_role', true),\n        ''\n    );\n\n    IF period_status_value = 'soft_closed'\n       AND journal_write_role_value IN ('period_closing', 'adjusting', 'reversal')\n       AND pg_has_role(session_user, 'accounting_closing_writer', 'MEMBER')\n    THEN\n        RETURN NEW;\n    END IF;\n\n    RAISE EXCEPTION\n        'Accounting book fiscal period is % (period_closed). Ordinary journals cannot be inserted after close. Post the AIS closing journal before hard-close; do not insert a later ordinary or reversal journal into a locked book period.',\n        period_status_value\n        USING ERRCODE = 'check_violation';\nEND;\n$$;\n\nCREATE OR REPLACE TRIGGER closed_period_guard\n    BEFORE INSERT ON accounting_core.general_journal\n    FOR EACH ROW\n    EXECUTE FUNCTION accounting_core.guard_period_insert();\n\nCOMMIT;\n''',
    encoding="utf-8",
)

ADR.write_text(
    '''# ADR 0051: Accounting-book-scoped fiscal-period control\n\n## Status\nAccepted for the accounting posting foundation.\n\n## Decision\nA fiscal calendar period keeps shared dates and an aggregate compatibility status, while posting and close authority are controlled independently by `accounting_book_period_control` for each accounting book. A close command materializes controls for active books, locks only the selected book-period row, and changes only that book's authoritative close state. The legacy `fiscal_period.period_status_code` is maintained as an aggregate (`open` while any active book is open, `soft_closed` when none are open but at least one is not hard closed, and `hard_closed` only when all active books are hard closed).\n\nThe PostgreSQL journal-insert guard reads the book-period control first and falls back to the calendar period only for a book-period that predates control materialization. This prevents a statutory close from blocking a management book and prevents an open sibling book from bypassing a selected book's hard close.\n\nClose idempotency, command locking, and AIS closing-journal identity include the accounting-book scope. Trial-balance snapshots remain immutable evidence keyed by accounting book and fiscal period.\n\n## Consequences\nControllers may close statutory and management books on different schedules without changing shared calendar dates. Existing single-book behavior remains compatible through the aggregate calendar status. New book creation inside an already closed calendar remains fail-closed until its book-period state is explicitly established through the controlled close/open lifecycle.\n\nNo claim of statutory compliance is implied; this ADR defines the system-of-record isolation invariant and its database enforcement.\n''',
    encoding="utf-8",
)

validator = VALIDATOR.read_text(encoding="utf-8")
validator = replace_once(
    validator,
    '    "database/migrations/0008_fiscal_period_open_command.sql",\n',
    '    "database/migrations/0008_fiscal_period_open_command.sql",\n    "database/migrations/0009_accounting_book_period_control.sql",\n',
    "validator requires migration 0009",
)
validator = replace_once(
    validator,
    '    "docs/adr/0050-postgresql-concurrency-hot-partition.md",\n',
    '    "docs/adr/0050-postgresql-concurrency-hot-partition.md",\n    "docs/adr/0051-accounting-book-period-control.md",\n',
    "validator requires ADR 0051",
)
VALIDATOR.write_text(validator, encoding="utf-8")

for path, heading, paragraph in (
    (
        DATA_MODEL,
        "## Accounting-book period control",
        "`accounting_book_period_control` is the authoritative close-state intersection of one tenant accounting book and one fiscal period. `fiscal_period` retains shared calendar dates; its status is an aggregate compatibility projection and must not be used to infer that every sibling book has the same close state. Trial-balance snapshots and journals already carry `accounting_book_id`, so close admission now uses the same scope.",
    ),
    (
        ARCHITECTURE,
        "## Book-scoped close authority",
        "Shared fiscal-calendar dates do not collapse independent accounting books into one close state. PostgreSQL `accounting_book_period_control` is checked by the journal insert guard and by application admission; statutory and management books can therefore close independently while immutable snapshots remain book scoped.",
    ),
    (
        OPERABILITY,
        "## Accounting-book close isolation",
        "Apply `0009_accounting_book_period_control.sql` after `0008_fiscal_period_open_command.sql` before granting runtime access. After migration, verify that closing one book leaves an open sibling book postable and that direct SQL into the closed book fails at `guard_period_insert`. If a book-period control row is missing, repair catalog/control state before retrying close; do not edit posted journals.",
    ),
):
    text = path.read_text(encoding="utf-8")
    if heading not in text:
        text = text.rstrip() + f"\n\n{heading}\n\n{paragraph}\n"
        path.write_text(text, encoding="utf-8")

changelog = CHANGELOG.read_text(encoding="utf-8")
entry = "- Scope fiscal-period close authority, close identity, and PostgreSQL journal admission by accounting book so sibling statutory/management books can close independently.\n"
if entry not in changelog:
    marker = "## Unreleased\n"
    if marker not in changelog:
        raise SystemExit("CHANGELOG.md lacks ## Unreleased")
    changelog = changelog.replace(marker, marker + "\n" + entry, 1)
    CHANGELOG.write_text(changelog, encoding="utf-8")

print("book-period isolation repair applied")
