"""One-shot normalization for PR #28 after the observed 0014 RED boundary."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0014_reconciliation_match_allocation.sql"


def replace_once(relative_path: str, old: str, new: str) -> None:
    path = ROOT / relative_path
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one repair anchor in {relative_path}, found {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def append_once(relative_path: str, marker: str, addition: str) -> None:
    path = ROOT / relative_path
    text = path.read_text(encoding="utf-8")
    if marker in text:
        return
    path.write_text(text.rstrip() + "\n\n" + addition.strip() + "\n", encoding="utf-8")


MIGRATION_SQL = r"""BEGIN;

ALTER TABLE accounting_core.reconciliation_run
    ADD CONSTRAINT reconciliation_run_currency_identity_unique
    UNIQUE (tenant_account_id, reconciliation_run_id, currency_code);

CREATE TABLE accounting_core.reconciliation_match (
    reconciliation_match_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    currency_code text NOT NULL CHECK (currency_code ~ '^[A-Z]{3}$'),
    matching_rule_code text NOT NULL CHECK (btrim(matching_rule_code) <> ''),
    proposed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, reconciliation_run_id, currency_code)
        REFERENCES accounting_core.reconciliation_run (
            tenant_account_id, reconciliation_run_id, currency_code
        ),
    UNIQUE (tenant_account_id, reconciliation_run_id, reconciliation_match_id),
    UNIQUE (
        tenant_account_id, reconciliation_run_id, reconciliation_match_id, currency_code
    )
);

CREATE TABLE accounting_core.statement_match_allocation (
    statement_match_allocation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    reconciliation_match_id uuid NOT NULL,
    bank_statement_entry_id uuid NOT NULL,
    allocated_amount numeric(38, 6) NOT NULL CHECK (allocated_amount > 0),
    currency_code text NOT NULL CHECK (currency_code ~ '^[A-Z]{3}$'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, reconciliation_run_id, reconciliation_match_id)
        REFERENCES accounting_core.reconciliation_match (
            tenant_account_id, reconciliation_run_id, reconciliation_match_id
        ),
    FOREIGN KEY (
        tenant_account_id, reconciliation_run_id, reconciliation_match_id, currency_code
    ) REFERENCES accounting_core.reconciliation_match (
        tenant_account_id, reconciliation_run_id, reconciliation_match_id, currency_code
    ),
    FOREIGN KEY (tenant_account_id, bank_statement_entry_id)
        REFERENCES accounting_integration.bank_statement_entry (
            tenant_account_id, bank_statement_entry_id
        ),
    UNIQUE (
        tenant_account_id, reconciliation_run_id, reconciliation_match_id,
        bank_statement_entry_id
    )
);

CREATE TABLE accounting_core.journal_match_allocation (
    journal_match_allocation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    reconciliation_match_id uuid NOT NULL,
    general_journal_id uuid NOT NULL,
    allocated_amount numeric(38, 6) NOT NULL CHECK (allocated_amount > 0),
    currency_code text NOT NULL CHECK (currency_code ~ '^[A-Z]{3}$'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, reconciliation_run_id, reconciliation_match_id)
        REFERENCES accounting_core.reconciliation_match (
            tenant_account_id, reconciliation_run_id, reconciliation_match_id
        ),
    FOREIGN KEY (
        tenant_account_id, reconciliation_run_id, reconciliation_match_id, currency_code
    ) REFERENCES accounting_core.reconciliation_match (
        tenant_account_id, reconciliation_run_id, reconciliation_match_id, currency_code
    ),
    FOREIGN KEY (tenant_account_id, general_journal_id)
        REFERENCES accounting_core.general_journal (tenant_account_id, general_journal_id),
    UNIQUE (
        tenant_account_id, reconciliation_run_id, reconciliation_match_id,
        general_journal_id
    )
);

CREATE INDEX reconciliation_match_run_order_index
    ON accounting_core.reconciliation_match (
        tenant_account_id, reconciliation_run_id, proposed_at, reconciliation_match_id
    );

CREATE INDEX statement_match_allocation_source_index
    ON accounting_core.statement_match_allocation (
        tenant_account_id, bank_statement_entry_id, reconciliation_run_id,
        reconciliation_match_id
    );

CREATE INDEX journal_match_allocation_source_index
    ON accounting_core.journal_match_allocation (
        tenant_account_id, general_journal_id, reconciliation_run_id,
        reconciliation_match_id
    );

CREATE OR REPLACE FUNCTION accounting_core.guard_statement_match_allocation_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    run_assignment_id uuid;
    run_currency_code text;
    assigned_bank_account_id uuid;
    statement_bank_account_id uuid;
    statement_currency_code text;
BEGIN
    SELECT bank_account_assignment_id, currency_code
    INTO run_assignment_id, run_currency_code
    FROM accounting_core.reconciliation_run
    WHERE tenant_account_id = NEW.tenant_account_id
      AND reconciliation_run_id = NEW.reconciliation_run_id;

    SELECT bank_account_record_id
    INTO assigned_bank_account_id
    FROM accounting_core.bank_account_assignment
    WHERE tenant_account_id = NEW.tenant_account_id
      AND bank_account_assignment_id = run_assignment_id;

    SELECT bank_statement_record.bank_account_record_id,
           bank_statement_entry.entry_currency_code
    INTO statement_bank_account_id, statement_currency_code
    FROM accounting_integration.bank_statement_entry
    JOIN accounting_integration.bank_statement_record
      ON bank_statement_record.tenant_account_id
         = bank_statement_entry.tenant_account_id
     AND bank_statement_record.bank_statement_record_id
         = bank_statement_entry.bank_statement_record_id
    WHERE bank_statement_entry.tenant_account_id = NEW.tenant_account_id
      AND bank_statement_entry.bank_statement_entry_id = NEW.bank_statement_entry_id;

    IF assigned_bank_account_id IS NULL
       OR statement_bank_account_id IS NULL
       OR assigned_bank_account_id <> statement_bank_account_id
       OR run_currency_code <> NEW.currency_code
       OR statement_currency_code <> NEW.currency_code THEN
        RAISE EXCEPTION
            'statement allocation must belong to the reconciliation run bank account and currency (reconciliation_scope_mismatch)'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION accounting_core.guard_journal_match_allocation_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    run_legal_entity_id uuid;
    run_accounting_book_id uuid;
    run_currency_code text;
    journal_legal_entity_id uuid;
    journal_accounting_book_id uuid;
    journal_currency_code text;
BEGIN
    SELECT legal_entity_id, accounting_book_id, currency_code
    INTO run_legal_entity_id, run_accounting_book_id, run_currency_code
    FROM accounting_core.reconciliation_run
    WHERE tenant_account_id = NEW.tenant_account_id
      AND reconciliation_run_id = NEW.reconciliation_run_id;

    SELECT legal_entity_id, accounting_book_id, transaction_currency_code
    INTO journal_legal_entity_id, journal_accounting_book_id, journal_currency_code
    FROM accounting_core.general_journal
    WHERE tenant_account_id = NEW.tenant_account_id
      AND general_journal_id = NEW.general_journal_id;

    IF journal_legal_entity_id IS NULL
       OR run_legal_entity_id <> journal_legal_entity_id
       OR run_accounting_book_id <> journal_accounting_book_id
       OR run_currency_code <> NEW.currency_code
       OR journal_currency_code <> NEW.currency_code THEN
        RAISE EXCEPTION
            'journal allocation must belong to the reconciliation run legal entity, book, and currency (reconciliation_scope_mismatch)'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER statement_match_allocation_scope_guard
BEFORE INSERT ON accounting_core.statement_match_allocation
FOR EACH ROW EXECUTE FUNCTION accounting_core.guard_statement_match_allocation_scope();

CREATE TRIGGER journal_match_allocation_scope_guard
BEFORE INSERT ON accounting_core.journal_match_allocation
FOR EACH ROW EXECUTE FUNCTION accounting_core.guard_journal_match_allocation_scope();

CREATE OR REPLACE FUNCTION accounting_core.assert_reconciliation_match_conservation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    statement_count bigint;
    journal_count bigint;
    statement_total numeric(38, 6);
    journal_total numeric(38, 6);
BEGIN
    SELECT COUNT(*), COALESCE(SUM(allocated_amount), 0)
    INTO statement_count, statement_total
    FROM accounting_core.statement_match_allocation
    WHERE tenant_account_id = NEW.tenant_account_id
      AND reconciliation_run_id = NEW.reconciliation_run_id
      AND reconciliation_match_id = NEW.reconciliation_match_id;

    SELECT COUNT(*), COALESCE(SUM(allocated_amount), 0)
    INTO journal_count, journal_total
    FROM accounting_core.journal_match_allocation
    WHERE tenant_account_id = NEW.tenant_account_id
      AND reconciliation_run_id = NEW.reconciliation_run_id
      AND reconciliation_match_id = NEW.reconciliation_match_id;

    IF statement_count = 0
       OR journal_count = 0
       OR statement_total <> journal_total THEN
        RAISE EXCEPTION
            'reconciliation match allocations must conserve exact statement and journal totals (reconciliation_allocation_unbalanced)'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER reconciliation_match_conservation_guard
AFTER INSERT ON accounting_core.reconciliation_match
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION accounting_core.assert_reconciliation_match_conservation();

CREATE CONSTRAINT TRIGGER statement_match_conservation_guard
AFTER INSERT ON accounting_core.statement_match_allocation
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION accounting_core.assert_reconciliation_match_conservation();

CREATE CONSTRAINT TRIGGER journal_match_conservation_guard
AFTER INSERT ON accounting_core.journal_match_allocation
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION accounting_core.assert_reconciliation_match_conservation();

CREATE OR REPLACE FUNCTION accounting_core.reject_reconciliation_allocation_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'reconciliation match and allocation evidence is append-only; record superseding evidence instead'
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE TRIGGER reconciliation_match_immutable_guard
BEFORE UPDATE OR DELETE ON accounting_core.reconciliation_match
FOR EACH ROW EXECUTE FUNCTION accounting_core.reject_reconciliation_allocation_mutation();

CREATE TRIGGER statement_match_allocation_immutable_guard
BEFORE UPDATE OR DELETE ON accounting_core.statement_match_allocation
FOR EACH ROW EXECUTE FUNCTION accounting_core.reject_reconciliation_allocation_mutation();

CREATE TRIGGER journal_match_allocation_immutable_guard
BEFORE UPDATE OR DELETE ON accounting_core.journal_match_allocation
FOR EACH ROW EXECUTE FUNCTION accounting_core.reject_reconciliation_allocation_mutation();

ALTER TABLE accounting_core.reconciliation_match ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.statement_match_allocation ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.journal_match_allocation ENABLE ROW LEVEL SECURITY;

ALTER TABLE accounting_core.reconciliation_match FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.statement_match_allocation FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.journal_match_allocation FORCE ROW LEVEL SECURITY;

CREATE POLICY reconciliation_match_tenant_isolation
ON accounting_core.reconciliation_match
USING (
    tenant_account_id = accounting_core.current_tenant_account_id()
)
WITH CHECK (
    tenant_account_id = accounting_core.current_tenant_account_id()
);

CREATE POLICY statement_match_allocation_tenant_isolation
ON accounting_core.statement_match_allocation
USING (
    tenant_account_id = accounting_core.current_tenant_account_id()
)
WITH CHECK (
    tenant_account_id = accounting_core.current_tenant_account_id()
);

CREATE POLICY journal_match_allocation_tenant_isolation
ON accounting_core.journal_match_allocation
USING (
    tenant_account_id = accounting_core.current_tenant_account_id()
)
WITH CHECK (
    tenant_account_id = accounting_core.current_tenant_account_id()
);

REVOKE ALL ON accounting_core.reconciliation_match FROM PUBLIC;
REVOKE ALL ON accounting_core.statement_match_allocation FROM PUBLIC;
REVOKE ALL ON accounting_core.journal_match_allocation FROM PUBLIC;

COMMIT;
"""


if MIGRATION.exists():
    raise SystemExit(f"refusing to overwrite existing {MIGRATION}")
MIGRATION.write_text(MIGRATION_SQL, encoding="utf-8")

replace_once(
    "src/accounting_information_platform/persistence.py",
    '''    if not reconciliation_control_migration_path.is_file():
        raise AccountingValidationError(
            "Reconciliation run/exception evidence migration is missing at "
            f"{reconciliation_control_migration_path}. Restore "
            "database/migrations/0013_reconciliation_run_exception_evidence.sql, then retry."
        )
    psycopg = _import_psycopg()
''',
    '''    if not reconciliation_control_migration_path.is_file():
        raise AccountingValidationError(
            "Reconciliation run/exception evidence migration is missing at "
            f"{reconciliation_control_migration_path}. Restore "
            "database/migrations/0013_reconciliation_run_exception_evidence.sql, then retry."
        )
    reconciliation_allocation_migration_path = (
        migration_path.parent / "0014_reconciliation_match_allocation.sql"
    )
    if not reconciliation_allocation_migration_path.is_file():
        raise AccountingValidationError(
            "Reconciliation allocation evidence migration is missing at "
            f"{reconciliation_allocation_migration_path}. Restore "
            "database/migrations/0014_reconciliation_match_allocation.sql, then retry."
        )
    psycopg = _import_psycopg()
''',
)
replace_once(
    "src/accounting_information_platform/persistence.py",
    '''            connection.execute(
                reconciliation_control_migration_path.read_text(encoding="utf-8")
            )
''',
    '''            connection.execute(
                reconciliation_control_migration_path.read_text(encoding="utf-8")
            )
            connection.execute(
                reconciliation_allocation_migration_path.read_text(encoding="utf-8")
            )
''',
)

replace_once(
    "scripts/validate_repository.py",
    '    "database/migrations/0013_reconciliation_run_exception_evidence.sql",\n',
    '    "database/migrations/0013_reconciliation_run_exception_evidence.sql",\n'
    '    "database/migrations/0014_reconciliation_match_allocation.sql",\n',
)

replace_once(
    "README.md",
    "the checked-in migration chain through `database/migrations/0013_reconciliation_run_exception_evidence.sql`",
    "the checked-in migration chain through `database/migrations/0014_reconciliation_match_allocation.sql`",
)
replace_once(
    "README.md",
    "foreign exchange, revenue schedules, bank-statement ingestion and\nreconciliation, consolidation, tax calculation, or live HomeTax/NTS\ntransmission.",
    "foreign exchange, revenue schedules, reconciliation approval and allocation-consumption\nworkflow, consolidation, tax calculation, or live HomeTax/NTS transmission.",
)

replace_once(
    "docs/OPERABILITY.md",
    "Apply migrations in numeric order through `0013_reconciliation_run_exception_evidence.sql` before starting the service.",
    "Apply migrations in numeric order through `0014_reconciliation_match_allocation.sql` before starting the service.",
)
replace_once(
    "docs/OPERABILITY.md",
    "database/migrations/0013_reconciliation_run_exception_evidence.sql\n```",
    "database/migrations/0013_reconciliation_run_exception_evidence.sql\n"
    "database/migrations/0014_reconciliation_match_allocation.sql\n```",
)
append_once(
    "docs/OPERABILITY.md",
    "## Reconciliation allocation evidence",
    '''## Reconciliation allocation evidence

Migration `0014_reconciliation_match_allocation.sql` persists proposed reconciliation matches as normalized append-only rows. Insert the match plus all statement-side and journal-side allocations in one transaction because the database defers exact conservation until commit and rejects a match with an empty side or unequal exact totals. The database also proves that statement rows belong to the run's assigned bank account and currency, and that journal rows belong to the run's legal entity, accounting book, and currency. These rows are proposal evidence only: operators may not update/delete them, and they do not approve reconciliation or post/reverse a journal. Record a later superseding run for corrections.''',
)

replace_once(
    "docs/ARCHITECTURE.md",
    "| `bank_statement_registry` | Immutable camt.053.001.14 statement/entry evidence, bank-account-to-book mapping, and host artifact locators |\n",
    "| `bank_statement_registry` | Immutable camt.053.001.14 statement/entry evidence, bank-account-to-book mapping, and host artifact locators |\n"
    "| `reconciliation_control` | Deterministic proposal evidence, durable run/exception lineage, normalized many-to-many match allocations, and exact book-to-bank close evidence without posting authority |\n",
)
replace_once(
    "docs/ARCHITECTURE.md",
    "13. `database/migrations/0013_reconciliation_run_exception_evidence.sql` — durable reconciliation-run and exception evidence required by the installed bank-reconciliation control chain.\n",
    "13. `database/migrations/0013_reconciliation_run_exception_evidence.sql` — durable reconciliation-run and exception evidence required by the installed bank-reconciliation control chain.\n"
    "14. `database/migrations/0014_reconciliation_match_allocation.sql` — normalized append-only statement/journal allocation evidence, forced tenant isolation, same-scope guards, and deferred exact allocation conservation.\n",
)

replace_once(
    "tests/test_foundation_install_manifest_contract.py",
    '''    def test_install_fails_closed_when_reconciliation_control_migration_is_missing(self) -> None:
''',
    '''    def test_required_files_and_install_docs_include_reconciliation_allocation(self) -> None:
        """Allocation evidence follows run/exception evidence in install contracts."""
        migration_thirteen = "database/migrations/0013_reconciliation_run_exception_evidence.sql"
        migration_fourteen = "database/migrations/0014_reconciliation_match_allocation.sql"
        self.assertIn(migration_fourteen, set(REQUIRED_FILES))
        for relative_path in ("docs/OPERABILITY.md", "docs/ARCHITECTURE.md"):
            with self.subTest(relative_path=relative_path):
                text = (ROOT / relative_path).read_text(encoding="utf-8")
                self.assertIn(migration_thirteen, text)
                self.assertIn(migration_fourteen, text)
                self.assertLess(text.index(migration_thirteen), text.index(migration_fourteen))

    def test_install_fails_closed_when_reconciliation_allocation_migration_is_missing(self) -> None:
        """The public foundation loader may not silently omit migration 0014."""
        original_is_file = Path.is_file

        def is_file(path: Path) -> bool:
            if path.name == "0014_reconciliation_match_allocation.sql":
                return False
            return original_is_file(path)

        with patch.object(Path, "is_file", is_file):
            with self.assertRaises(AccountingValidationError):
                apply_foundation_migration(
                    "postgresql://unused",
                    ROOT / "database/migrations/0001_accounting_foundation.sql",
                )

    def test_install_fails_closed_when_reconciliation_control_migration_is_missing(self) -> None:
''',
)

replace_once(
    "tests/test_reconciliation_allocation_persistence_red.py",
    "from pathlib import Path\n\n\nROOT",
    "from pathlib import Path\n\nimport psycopg\n\nfrom tests import test_postgres_posting as posting\n\n\nROOT",
)
replace_once(
    "tests/test_reconciliation_allocation_persistence_red.py",
    '''\n\nif __name__ == "__main__":
    unittest.main()
''',
    '''\n\n@unittest.skipUnless(MIGRATION.exists(), "RED until durable allocation migration exists")
class PostgresReconciliationAllocationPersistenceTests(unittest.TestCase):
    """Verify allocation isolation and deferred conservation on PostgreSQL 18."""

    @classmethod
    def setUpClass(cls) -> None:
        posting.PostgresPostingTests.setUpClass()

    def test_allocation_tables_force_rls(self) -> None:
        """Every durable match/allocation relation is forced through tenant RLS."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            rows = connection.execute(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class AS c
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = 'accounting_core'
                  AND c.relname = ANY(%s)
                ORDER BY c.relname
                """,
                ([
                    "journal_match_allocation",
                    "reconciliation_match",
                    "statement_match_allocation",
                ],),
            ).fetchall()
        self.assertEqual(
            rows,
            [
                ("journal_match_allocation", True, True),
                ("reconciliation_match", True, True),
                ("statement_match_allocation", True, True),
            ],
        )

    def test_conservation_guards_are_deferred_constraint_triggers(self) -> None:
        """Partial match writes can exist inside one transaction but not at commit."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            rows = connection.execute(
                """
                SELECT c.relname, t.tgname, t.tgdeferrable, t.tginitdeferred
                FROM pg_trigger AS t
                JOIN pg_class AS c ON c.oid = t.tgrelid
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = 'accounting_core'
                  AND t.tgname = ANY(%s)
                  AND NOT t.tgisinternal
                ORDER BY c.relname, t.tgname
                """,
                ([
                    "journal_match_conservation_guard",
                    "reconciliation_match_conservation_guard",
                    "statement_match_conservation_guard",
                ],),
            ).fetchall()
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row[2] and row[3] for row in rows))

    def test_scope_and_immutability_guards_exist(self) -> None:
        """Database guardrails own same-scope admission and append-only evidence."""
        expected = {
            "journal_match_allocation_immutable_guard",
            "journal_match_allocation_scope_guard",
            "reconciliation_match_immutable_guard",
            "statement_match_allocation_immutable_guard",
            "statement_match_allocation_scope_guard",
        }
        with psycopg.connect(posting.DATABASE_URL) as connection:
            names = {
                row[0]
                for row in connection.execute(
                    """
                    SELECT t.tgname
                    FROM pg_trigger AS t
                    JOIN pg_class AS c ON c.oid = t.tgrelid
                    JOIN pg_namespace AS n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'accounting_core'
                      AND c.relname = ANY(%s)
                      AND NOT t.tgisinternal
                    """,
                    ([
                        "journal_match_allocation",
                        "reconciliation_match",
                        "statement_match_allocation",
                    ],),
                ).fetchall()
            }
        self.assertTrue(expected <= names)


if __name__ == "__main__":
    unittest.main()
''',
)

replace_once(
    "docs/adr/0054-deterministic-bank-reconciliation-proposals.md",
    "Allocation plans are still evidence: they never post, reverse, approve, or adjust a journal, and a later relational slice persists them with concurrency and double-consumption controls.",
    "Allocation plans are still evidence: they never post, reverse, approve, or adjust a journal. Migration `0014_reconciliation_match_allocation.sql` persists the proposed match and normalized statement/journal allocation rows with forced tenant isolation, same-scope guards, append-only mutation guards, and deferred exact conservation; approval and double-consumption concurrency remain later bounded controls.",
)
replace_once(
    "docs/adr/0054-deterministic-bank-reconciliation-proposals.md",
    "This slice still does not claim the complete issue #8 reconciliation vertical. Persistence of immutable reconciliation runs/candidates, many-to-many allocation conservation, explicit approval/exception records, concurrency protection, temporal knowledge cutoffs, close-package integration, and durable approval evidence remain later bounded work and must be test-first before they can be treated as integrated capability. The close-review projection is a read model/export surface over current immutable evidence, not persistence for those missing controls.",
    "This slice still does not claim the complete issue #8 reconciliation vertical. Reconciliation runs/exceptions and normalized many-to-many match allocations are now durable proposal evidence, but explicit approval, double-consumption concurrency, close-package admission, and durable approval evidence remain later bounded work and must be test-first before they can be treated as integrated capability. The close-review projection remains a read model/export surface and cannot grant those missing authorities.",
)
replace_once(
    "docs/adr/0054-deterministic-bank-reconciliation-proposals.md",
    "## References\n",
    "Exact RED head `99ded882084e8481e06eef762afe1d8ea0a078b6` then ran 458 PostgreSQL-backed behavior/repository tests in Accounting Foundation CI `33070284545`. Exactly the three new allocation-persistence contracts failed at the first causal boundary because `database/migrations/0014_reconciliation_match_allocation.sql` did not exist; later complete-coverage and package/SBOM/provenance steps did not become passing evidence. The narrow repair adds only normalized durable proposal evidence and database-owned scope/conservation/immutability controls; reconciliation approval and journal authority remain unchanged.\n\n## References\n",
)

replace_once(
    "CHANGELOG.md",
    "## [Unreleased]\n\n",
    "## [Unreleased]\n\n- Added normalized durable reconciliation match/allocation proposal evidence in `0014_reconciliation_match_allocation.sql`: statement-side and journal-side rows are tenant/run bound, forced-RLS and append-only, source scope is checked against the run's bank account/entity/book/currency, and deferred database constraints require exact equal allocation totals on both sides before commit. This evidence cannot approve reconciliation or post/reverse a journal; corrections use superseding reconciliation evidence. ADR 0054 records the boundary.\n",
)

append_once(
    "docs/DATA_MODEL.md",
    "## Durable reconciliation allocation evidence",
    '''## Durable reconciliation allocation evidence

`reconciliation_run` remains the evaluated tenant/entity/book/bank-assignment/currency scope. Migration `0014_reconciliation_match_allocation.sql` adds `reconciliation_match` as one proposed match identity, `statement_match_allocation` as its normalized links to immutable `bank_statement_entry` rows, and `journal_match_allocation` as its normalized links to immutable `general_journal` rows. Allocation money is `numeric(38, 6)` and strictly positive; currency is bound to the parent match/run. Deferred database constraints require at least one row on both sides and exact equality of the two allocation sums at commit. Scope triggers reject a statement from another assigned bank account or a journal from another legal entity/book/currency. All three relations use forced tenant RLS and reject `UPDATE`/`DELETE`; later corrections use superseding evidence rather than rewriting a recorded proposal.''',
)
append_once(
    "docs/ERD.md",
    "## Reconciliation allocation extension",
    '''## Reconciliation allocation extension

```text
reconciliation_run
    1 ──< reconciliation_match
              1 ──< statement_match_allocation >── 1 bank_statement_entry
              1 ──< journal_match_allocation   >── 1 general_journal
```

The two allocation sides are normalized rather than stored as JSON. `(tenant_account_id, reconciliation_run_id, reconciliation_match_id)` is the shared match identity; statement and journal source foreign keys are tenant-scoped, and database guards additionally prove bank-account/entity/book/currency scope. Exact allocation conservation is deferred to transaction commit so a match and all of its rows can be inserted atomically.''',
)
append_once(
    "docs/doctoring/STANDARD_TRACEABILITY.md",
    "### Durable reconciliation allocation evidence",
    '''### Durable reconciliation allocation evidence

- **Decision:** ADR 0054 / migration `0014_reconciliation_match_allocation.sql` persist normalized many-to-many proposal evidence under forced tenant RLS, append-only mutation guards, same-scope source checks, and deferred exact Decimal-compatible SQL conservation.
- **External authority boundary:** ISO 20022 remains the authority for the bank-statement source-message vocabulary already retained by the statement registry. It does not prescribe AIS matching precedence, allocation conservation, approval, or journal posting; those remain internal reviewed accounting controls.
- **Claim limit:** this is evidence readiness only. It does not claim ISO conformance for reconciliation behavior, SOC 2/CSAP certification, reconciliation approval, or statutory posting from statement lines.''',
)

print("normalized reconciliation allocation persistence slice")
