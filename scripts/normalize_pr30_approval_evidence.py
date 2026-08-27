"""One-shot normalization for PR #30 durable reconciliation approval evidence.

This helper is intentionally deleted by its own successful repair commit.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise SystemExit(f"expected exactly one repair anchor in {path}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, text_to_append: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if marker in text:
        return
    if not text.endswith("\n"):
        text += "\n"
    target.write_text(text + text_to_append.rstrip() + "\n", encoding="utf-8")


MIGRATION = """BEGIN;

-- Durable human reconciliation approval evidence.
--
-- An approval is an immutable accounting-control fact bound to one tenant/run/match
-- and one immutable command identity.  Approval evidence never posts, reverses,
-- closes, or changes accounting policy.  A match may become approved only after
-- the corresponding durable approved decision exists.

CREATE TABLE accounting_core.reconciliation_approval (
    reconciliation_approval_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    reconciliation_match_id uuid NOT NULL,
    approval_command_key text NOT NULL
        CHECK (btrim(approval_command_key) <> ''),
    source_payload_hash text NOT NULL
        CHECK (source_payload_hash ~ '^sha256:[0-9a-f]{64}$'),
    approver_reference text NOT NULL
        CHECK (btrim(approver_reference) <> ''),
    approval_purpose_code text NOT NULL
        CHECK (btrim(approval_purpose_code) <> ''),
    approval_decision_code text NOT NULL
        CHECK (approval_decision_code IN ('approved', 'rejected')),
    effective_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (
        tenant_account_id,
        reconciliation_run_id,
        reconciliation_match_id
    )
        REFERENCES accounting_core.reconciliation_match (
            tenant_account_id,
            reconciliation_run_id,
            reconciliation_match_id
        ),
    UNIQUE (tenant_account_id, approval_command_key),
    UNIQUE (
        tenant_account_id,
        reconciliation_run_id,
        reconciliation_match_id
    )
);

CREATE INDEX reconciliation_approval_run_index
    ON accounting_core.reconciliation_approval (
        tenant_account_id,
        reconciliation_run_id,
        approval_decision_code,
        recorded_at,
        reconciliation_approval_id
    );

ALTER TABLE accounting_core.reconciliation_approval ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.reconciliation_approval FORCE ROW LEVEL SECURITY;

CREATE POLICY reconciliation_approval_isolation
    ON accounting_core.reconciliation_approval
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

REVOKE ALL ON accounting_core.reconciliation_approval FROM PUBLIC;

CREATE OR REPLACE FUNCTION accounting_core.reject_reconciliation_approval_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'recorded reconciliation approval evidence is immutable; create a new reviewed match instead (reconciliation_approval_immutable)'
        USING ERRCODE = '23514';
END;
$$;

CREATE TRIGGER reconciliation_approval_immutability_guard
BEFORE UPDATE OR DELETE
ON accounting_core.reconciliation_approval
FOR EACH ROW EXECUTE FUNCTION accounting_core.reject_reconciliation_approval_mutation();

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_approval_insert_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    current_status text;
BEGIN
    SELECT match_status_code
    INTO current_status
    FROM accounting_core.reconciliation_match
    WHERE tenant_account_id = NEW.tenant_account_id
      AND reconciliation_run_id = NEW.reconciliation_run_id
      AND reconciliation_match_id = NEW.reconciliation_match_id;

    IF NOT FOUND OR current_status <> 'proposed' THEN
        RAISE EXCEPTION
            'reconciliation approval evidence requires a proposed match in the same tenant/run scope (reconciliation_approval_scope_mismatch)'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER reconciliation_approval_insert_guard
BEFORE INSERT
ON accounting_core.reconciliation_approval
FOR EACH ROW EXECUTE FUNCTION accounting_core.reconciliation_approval_insert_guard();

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_match_requires_approval()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.match_status_code <> 'approved'
       OR (TG_OP = 'UPDATE' AND OLD.match_status_code = 'approved') THEN
        RETURN NEW;
    END IF;

    IF NEW.approved_at IS NULL THEN
        RAISE EXCEPTION
            'approved reconciliation match requires approved_at and durable approval evidence (reconciliation_approval_required)'
            USING ERRCODE = '23514';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_approval AS approval
        WHERE approval.tenant_account_id = NEW.tenant_account_id
          AND approval.reconciliation_run_id = NEW.reconciliation_run_id
          AND approval.reconciliation_match_id = NEW.reconciliation_match_id
          AND approval.approval_decision_code = 'approved'
    ) THEN
        RAISE EXCEPTION
            'approved reconciliation match requires durable approved evidence (reconciliation_approval_required)'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER reconciliation_match_requires_approval_guard
BEFORE INSERT OR UPDATE
ON accounting_core.reconciliation_match
FOR EACH ROW EXECUTE FUNCTION accounting_core.reconciliation_match_requires_approval();

COMMIT;
"""

(ROOT / "database/migrations/0016_reconciliation_approval_evidence.sql").write_text(
    MIGRATION, encoding="utf-8"
)

# One canonical executable migration chain: persistence owns ordering.
replace_once(
    "src/accounting_information_platform/persistence.py",
    '''    if not conservation_migration_path.is_file():\n        raise AccountingValidationError(\n            "Reconciliation multi-match conservation migration is missing at "\n            f"{conservation_migration_path}. Restore "\n            "database/migrations/0015_reconciliation_multi_match_conservation.sql, then retry."\n        )\n    psycopg = _import_psycopg()\n''',
    '''    if not conservation_migration_path.is_file():\n        raise AccountingValidationError(\n            "Reconciliation multi-match conservation migration is missing at "\n            f"{conservation_migration_path}. Restore "\n            "database/migrations/0015_reconciliation_multi_match_conservation.sql, then retry."\n        )\n    approval_evidence_migration_path = (\n        migration_path.parent / "0016_reconciliation_approval_evidence.sql"\n    )\n    if not approval_evidence_migration_path.is_file():\n        raise AccountingValidationError(\n            "Reconciliation approval-evidence migration is missing at "\n            f"{approval_evidence_migration_path}. Restore "\n            "database/migrations/0016_reconciliation_approval_evidence.sql, then retry."\n        )\n    psycopg = _import_psycopg()\n''',
)
replace_once(
    "src/accounting_information_platform/persistence.py",
    '''            connection.execute(\n                conservation_migration_path.read_text(encoding="utf-8")\n            )\n    except Exception as error:\n''',
    '''            connection.execute(\n                conservation_migration_path.read_text(encoding="utf-8")\n            )\n            connection.execute(\n                approval_evidence_migration_path.read_text(encoding="utf-8")\n            )\n    except Exception as error:\n''',
)

# Repository/install contracts.
replace_once(
    "scripts/validate_repository.py",
    '    "database/migrations/0015_reconciliation_multi_match_conservation.sql",\n',
    '    "database/migrations/0015_reconciliation_multi_match_conservation.sql",\n    "database/migrations/0016_reconciliation_approval_evidence.sql",\n',
)
replace_once(
    "docs/OPERABILITY.md",
    "Apply migrations in numeric order through `0015_reconciliation_multi_match_conservation.sql` before starting the service.",
    "Apply migrations in numeric order through `0016_reconciliation_approval_evidence.sql` before starting the service.",
)
replace_once(
    "docs/OPERABILITY.md",
    "database/migrations/0015_reconciliation_multi_match_conservation.sql\n```",
    "database/migrations/0015_reconciliation_multi_match_conservation.sql\ndatabase/migrations/0016_reconciliation_approval_evidence.sql\n```",
)
replace_once(
    "docs/OPERABILITY.md",
    "Migration `0015_reconciliation_multi_match_conservation.sql` replaces the run-wide single-approved-match shortcut from `0014` with tenant/run-scoped match identity plus exact statement/journal allocation conservation. It permits multiple independently approved matches only when no authoritative source amount is over-consumed and grants no journal-posting authority.\n",
    "Migration `0015_reconciliation_multi_match_conservation.sql` replaces the run-wide single-approved-match shortcut from `0014` with tenant/run-scoped match identity plus exact statement/journal allocation conservation. It permits multiple independently approved matches only when no authoritative source amount is over-consumed and grants no journal-posting authority.\n\nMigration `0016_reconciliation_approval_evidence.sql` adds immutable tenant/run/match-scoped human approval evidence. Operators first record the reviewed approval command identity, immutable source hash, approver and purpose, then transition the proposed match to `approved`; PostgreSQL rejects a status-only approval. Approval evidence grants no journal-posting, reversal, close, or policy authority.\n",
)
replace_once(
    "docs/ARCHITECTURE.md",
    "15. `database/migrations/0015_reconciliation_multi_match_conservation.sql` — replaces the run-wide single-approved-match shortcut with tenant/run-scoped match identity and exact statement/journal source-allocation conservation so independent matches may be approved without double-consuming source evidence.\n",
    "15. `database/migrations/0015_reconciliation_multi_match_conservation.sql` — replaces the run-wide single-approved-match shortcut with tenant/run-scoped match identity and exact statement/journal source-allocation conservation so independent matches may be approved without double-consuming source evidence.\n16. `database/migrations/0016_reconciliation_approval_evidence.sql` — immutable tenant/run/match-scoped human approval evidence and a database-owned guard that forbids status-only match approval; approval remains non-posting control evidence.\n",
)

# Changelog and ADR authority text.
replace_once(
    "CHANGELOG.md",
    "## [Unreleased]\n\n",
    "## [Unreleased]\n\n- Added durable reconciliation approval evidence (`reconciliation_approval`) with tenant/run/match scope, tenant-scoped command identity, immutable source hash, approver/purpose/decision/effective/system time, forced RLS, and append-only history. PostgreSQL now rejects a proposed-to-approved `reconciliation_match` transition unless a separately durable approved decision exists; the approval fact grants no journal posting, reversal, period-close, or accounting-policy authority.\n",
)
replace_once(
    "docs/adr/0054-deterministic-bank-reconciliation-proposals.md",
    "## Consequences and limits\n",
    "### Durable approval evidence\n\nA persisted reconciliation match is not human-approved merely because application code writes `match_status_code='approved'`. Migration 0016 records one immutable `reconciliation_approval` control fact per tenant/run/match with a tenant-scoped command key, immutable source hash, approver reference, purpose, decision, effective time, and recorded time. PostgreSQL permits the proposed-to-approved transition only after an `approved` control fact exists. A rejected approval is terminal for that match; corrections create a new reviewed match/control fact rather than rewriting approval history. Approval evidence remains non-posting authority and cannot post, reverse, close, choose accounting policy, or mutate source statement/journal facts.\n\n## Consequences and limits\n",
)

# Install-manifest tests for 0016.
anchor = '''    def test_canonical_persistence_loader_fails_closed_when_conservation_is_missing(self) -> None:\n'''
addition = '''    def test_required_files_and_install_docs_include_reconciliation_approval_evidence(self) -> None:\n        """Migration 0016 follows conservation and is required by operator/install contracts."""\n        migration_fifteen = "database/migrations/0015_reconciliation_multi_match_conservation.sql"\n        migration_sixteen = "database/migrations/0016_reconciliation_approval_evidence.sql"\n        self.assertIn(migration_sixteen, set(REQUIRED_FILES))\n        for relative_path in ("docs/OPERABILITY.md", "docs/ARCHITECTURE.md"):\n            with self.subTest(relative_path=relative_path):\n                text = (ROOT / relative_path).read_text(encoding="utf-8")\n                self.assertIn(migration_fifteen, text)\n                self.assertIn(migration_sixteen, text)\n                self.assertLess(text.index(migration_fifteen), text.index(migration_sixteen))\n\n    def test_canonical_persistence_loader_fails_closed_when_approval_evidence_is_missing(self) -> None:\n        """Real PostgreSQL fixtures may not silently stop the authoritative chain at 0015."""\n        from accounting_information_platform.persistence import (\n            apply_foundation_migration as apply_persistence_foundation_migration,\n        )\n\n        original_is_file = Path.is_file\n\n        def is_file(path: Path) -> bool:\n            if path.name == "0016_reconciliation_approval_evidence.sql":\n                return False\n            return original_is_file(path)\n\n        with patch.object(Path, "is_file", is_file):\n            with self.assertRaises(AccountingValidationError):\n                apply_persistence_foundation_migration(\n                    "postgresql://unused",\n                    ROOT / "database/migrations/0001_accounting_foundation.sql",\n                )\n\n    def test_public_loader_fails_closed_when_approval_evidence_is_missing(self) -> None:\n        """The exported install boundary must delegate to the same complete chain through 0016."""\n        original_is_file = Path.is_file\n\n        def is_file(path: Path) -> bool:\n            if path.name == "0016_reconciliation_approval_evidence.sql":\n                return False\n            return original_is_file(path)\n\n        with patch.object(Path, "is_file", is_file):\n            with self.assertRaises(AccountingValidationError):\n                apply_foundation_migration(\n                    "postgresql://unused",\n                    ROOT / "database/migrations/0001_accounting_foundation.sql",\n                )\n\n'''
replace_once(
    "tests/test_foundation_install_manifest_contract.py",
    anchor,
    addition + anchor,
)

# Existing reconciliation fixtures must themselves obey the new approval boundary.
old_insert_match = '''    def _insert_match(self, candidate_id: uuid.UUID, status: str = "approved") -> uuid.UUID:\n        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:\n            row = connection.execute(\n                """\n                INSERT INTO accounting_core.reconciliation_match (\n                    reconciliation_match_id, tenant_account_id, reconciliation_run_id,\n                    reconciliation_candidate_id, match_status_code, approved_at\n                )\n                VALUES (%s, %s, %s, %s, %s,\n                        CASE WHEN %s = 'approved' THEN clock_timestamp() ELSE NULL END)\n                RETURNING reconciliation_match_id\n                """,\n                (\n                    uuid.uuid4(),\n                    self.scope["tenant_account_id"],\n                    self.run_reference,\n                    candidate_id,\n                    status,\n                    status,\n                ),\n            ).fetchone()\n        return row[0]\n'''
new_insert_match = '''    def _insert_match(self, candidate_id: uuid.UUID, status: str = "approved") -> uuid.UUID:\n        match_id = uuid.uuid4()\n        initial_status = "proposed" if status == "approved" else status\n        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:\n            connection.execute(\n                """\n                INSERT INTO accounting_core.reconciliation_match (\n                    reconciliation_match_id, tenant_account_id, reconciliation_run_id,\n                    reconciliation_candidate_id, match_status_code, approved_at\n                )\n                VALUES (%s, %s, %s, %s, %s, NULL)\n                """,\n                (\n                    match_id,\n                    self.scope["tenant_account_id"],\n                    self.run_reference,\n                    candidate_id,\n                    initial_status,\n                ),\n            )\n            if status == "approved":\n                connection.execute(\n                    """\n                    INSERT INTO accounting_core.reconciliation_approval (\n                        tenant_account_id, reconciliation_run_id, reconciliation_match_id,\n                        approval_command_key, source_payload_hash, approver_reference,\n                        approval_purpose_code, approval_decision_code, effective_at\n                    )\n                    VALUES (%s, %s, %s, %s, %s, 'reviewer-fixture',\n                            'reconciliation_review', 'approved', %s)\n                    """,\n                    (\n                        self.scope["tenant_account_id"],\n                        self.run_reference,\n                        match_id,\n                        f"approve-{match_id}",\n                        f"sha256:{match_id.hex}{match_id.hex}",\n                        VALID_FROM,\n                    ),\n                )\n                connection.execute(\n                    """\n                    UPDATE accounting_core.reconciliation_match\n                    SET match_status_code = 'approved', approved_at = clock_timestamp()\n                    WHERE tenant_account_id = %s\n                      AND reconciliation_run_id = %s\n                      AND reconciliation_match_id = %s\n                    """,\n                    (self.scope["tenant_account_id"], self.run_reference, match_id),\n                )\n        return match_id\n'''
replace_once(
    "tests/test_reconciliation_candidate_allocation_persistence_red.py",
    old_insert_match,
    new_insert_match,
)

old_cross_match = '''    def _insert_match(self, run_reference: uuid.UUID, candidate_id: uuid.UUID) -> uuid.UUID:\n        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:\n            row = connection.execute(\n                """\n                INSERT INTO accounting_core.reconciliation_match (\n                    reconciliation_match_id, tenant_account_id, reconciliation_run_id,\n                    reconciliation_candidate_id, match_status_code, approved_at\n                )\n                VALUES (%s, %s, %s, %s, 'approved', clock_timestamp())\n                RETURNING reconciliation_match_id\n                """,\n                (\n                    uuid.uuid4(),\n                    self.case.scope["tenant_account_id"],\n                    run_reference,\n                    candidate_id,\n                ),\n            ).fetchone()\n        return row[0]\n'''
new_cross_match = '''    def _insert_match(self, run_reference: uuid.UUID, candidate_id: uuid.UUID) -> uuid.UUID:\n        match_id = uuid.uuid4()\n        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:\n            connection.execute(\n                """\n                INSERT INTO accounting_core.reconciliation_match (\n                    reconciliation_match_id, tenant_account_id, reconciliation_run_id,\n                    reconciliation_candidate_id, match_status_code, approved_at\n                )\n                VALUES (%s, %s, %s, %s, 'proposed', NULL)\n                """,\n                (\n                    match_id,\n                    self.case.scope["tenant_account_id"],\n                    run_reference,\n                    candidate_id,\n                ),\n            )\n            connection.execute(\n                """\n                INSERT INTO accounting_core.reconciliation_approval (\n                    tenant_account_id, reconciliation_run_id, reconciliation_match_id,\n                    approval_command_key, source_payload_hash, approver_reference,\n                    approval_purpose_code, approval_decision_code, effective_at\n                )\n                VALUES (%s, %s, %s, %s, %s, 'reviewer-fixture',\n                        'reconciliation_review', 'approved', %s)\n                """,\n                (\n                    self.case.scope["tenant_account_id"],\n                    run_reference,\n                    match_id,\n                    f"approve-{match_id}",\n                    f"sha256:{match_id.hex}{match_id.hex}",\n                    allocation.VALID_FROM,\n                ),\n            )\n            connection.execute(\n                """\n                UPDATE accounting_core.reconciliation_match\n                SET match_status_code = 'approved', approved_at = clock_timestamp()\n                WHERE tenant_account_id = %s\n                  AND reconciliation_run_id = %s\n                  AND reconciliation_match_id = %s\n                """,\n                (self.case.scope["tenant_account_id"], run_reference, match_id),\n            )\n        return match_id\n'''
replace_once(
    "tests/test_reconciliation_cross_run_conservation_red.py",
    old_cross_match,
    new_cross_match,
)

# Strengthen the RED contract into real transition/immutability behavior.
approval_test = ROOT / "tests/test_reconciliation_approval_evidence_red.py"
text = approval_test.read_text(encoding="utf-8")
text = text.replace(
    "from tests import test_postgres_posting as posting\n",
    "from tests import test_postgres_posting as posting\nfrom tests import test_reconciliation_candidate_allocation_persistence_red as allocation\n",
    1,
)
text = text.replace(
    '''    @classmethod\n    def setUpClass(cls) -> None:\n        posting.PostgresPostingTests.setUpClass()\n\n    def setUp(self) -> None:\n        self.case = posting.PostgresPostingTests("setUp")\n        self.case.setUp()\n        self.addCleanup(self.case.doCleanups)\n        self.addCleanup(self.case.tearDown)\n''',
    '''    @classmethod\n    def setUpClass(cls) -> None:\n        allocation.PostgresReconciliationAllocationRedTests.setUpClass()\n\n    def setUp(self) -> None:\n        self.case = allocation.PostgresReconciliationAllocationRedTests("setUp")\n        self.case.setUp()\n        self.addCleanup(self.case.doCleanups)\n        self.addCleanup(self.case.tearDown)\n\n    def _proposed_match(self) -> tuple[object, object]:\n        candidate_id = self.case._insert_candidate("stmt-approval", "journal-approval")\n        match_id = __import__("uuid").uuid4()\n        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:\n            connection.execute(\n                """\n                INSERT INTO accounting_core.reconciliation_match (\n                    reconciliation_match_id, tenant_account_id, reconciliation_run_id,\n                    reconciliation_candidate_id, match_status_code, approved_at\n                )\n                VALUES (%s, %s, %s, %s, 'proposed', NULL)\n                """,\n                (\n                    match_id,\n                    self.case.scope["tenant_account_id"],\n                    self.case.run_reference,\n                    candidate_id,\n                ),\n            )\n        return candidate_id, match_id\n''',
    1,
)
behavior_tests = '''\n    def test_status_only_approval_fails_closed(self) -> None:\n        """A proposed match cannot become approved without durable human approval evidence."""\n        _candidate_id, match_id = self._proposed_match()\n        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:\n            with self.assertRaises(psycopg.errors.CheckViolation):\n                connection.execute(\n                    """\n                    UPDATE accounting_core.reconciliation_match\n                    SET match_status_code = 'approved', approved_at = clock_timestamp()\n                    WHERE tenant_account_id = %s\n                      AND reconciliation_run_id = %s\n                      AND reconciliation_match_id = %s\n                    """,\n                    (\n                        self.case.scope["tenant_account_id"],\n                        self.case.run_reference,\n                        match_id,\n                    ),\n                )\n\n    def test_durable_approval_enables_transition_and_remains_immutable(self) -> None:\n        """One immutable approved control fact enables, but cannot later rewrite, the match approval."""\n        _candidate_id, match_id = self._proposed_match()\n        approval_id = __import__("uuid").uuid4()\n        with psycopg.connect(posting.DATABASE_URL, autocommit=True) as connection:\n            connection.execute(\n                """\n                INSERT INTO accounting_core.reconciliation_approval (\n                    reconciliation_approval_id, tenant_account_id, reconciliation_run_id,\n                    reconciliation_match_id, approval_command_key, source_payload_hash,\n                    approver_reference, approval_purpose_code, approval_decision_code,\n                    effective_at\n                )\n                VALUES (%s, %s, %s, %s, %s, %s, 'controller-001',\n                        'reconciliation_review', 'approved', %s)\n                """,\n                (\n                    approval_id,\n                    self.case.scope["tenant_account_id"],\n                    self.case.run_reference,\n                    match_id,\n                    f"approve-{match_id}",\n                    f"sha256:{match_id.hex}{match_id.hex}",\n                    allocation.VALID_FROM,\n                ),\n            )\n            connection.execute(\n                """\n                UPDATE accounting_core.reconciliation_match\n                SET match_status_code = 'approved', approved_at = clock_timestamp()\n                WHERE tenant_account_id = %s\n                  AND reconciliation_run_id = %s\n                  AND reconciliation_match_id = %s\n                """,\n                (\n                    self.case.scope["tenant_account_id"],\n                    self.case.run_reference,\n                    match_id,\n                ),\n            )\n            status = connection.execute(\n                """\n                SELECT match_status_code\n                FROM accounting_core.reconciliation_match\n                WHERE tenant_account_id = %s\n                  AND reconciliation_run_id = %s\n                  AND reconciliation_match_id = %s\n                """,\n                (\n                    self.case.scope["tenant_account_id"],\n                    self.case.run_reference,\n                    match_id,\n                ),\n            ).fetchone()[0]\n            self.assertEqual(status, "approved")\n            with self.assertRaises(psycopg.errors.CheckViolation):\n                connection.execute(\n                    """\n                    UPDATE accounting_core.reconciliation_approval\n                    SET approver_reference = 'rewritten-controller'\n                    WHERE reconciliation_approval_id = %s\n                    """,\n                    (approval_id,),\n                )\n            with self.assertRaises(psycopg.errors.CheckViolation):\n                connection.execute(\n                    "DELETE FROM accounting_core.reconciliation_approval WHERE reconciliation_approval_id = %s",\n                    (approval_id,),\n                )\n'''
if behavior_tests.strip() not in text:
    text = text.replace("\n\nif __name__ == \"__main__\":\n", behavior_tests + "\n\nif __name__ == \"__main__\":\n", 1)
approval_test.write_text(text, encoding="utf-8")

print("PR #30 durable approval repair materialized")
