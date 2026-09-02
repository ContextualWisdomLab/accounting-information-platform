#!/usr/bin/env python3
"""Apply bounded current-review repairs to stacked reconciliation exception resolution PR #47."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "database/migrations/0020_reconciliation_exception_resolution_command.sql"
RESOLUTION = ROOT / "src/accounting_information_platform/reconciliation_exception_resolution.py"
INIT = ROOT / "src/accounting_information_platform/__init__.py"
OPERABILITY = ROOT / "docs/OPERABILITY.md"
ADR = ROOT / "docs/adr/0062-reconciliation-exception-resolution-command-authority.md"
STRICT_DOC = ROOT / "docs/doctoring/2026-09-02-reconciliation-command-strict-json-identity.md"
CHANGELOG = ROOT / "CHANGELOG.md"
JSON_TESTS = ROOT / "tests/test_reconciliation_exception_resolution_json_identity.py"
POSTGRES_TESTS = ROOT / "tests/test_reconciliation_exception_resolution_postgres.py"
LOCK_TEST = ROOT / "tests/test_reconciliation_lifecycle_lock_wait_postgres.py"
WORKFLOW = ROOT / ".github/workflows/_temp_pr47_review_repair.yml"
SELF = Path(__file__).resolve()


def once(text: str, old: str, new: str, label: str) -> str:
    """Replace exactly one guarded fragment."""
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_migration() -> None:
    """Enforce unique retained evidence and command/status/outbox commit pairing in PostgreSQL."""
    text = MIGRATION.read_text(encoding="utf-8")
    text = once(
        text,
        "    retained_evidence_recorded_at timestamptz;\n    canonical_command jsonb;",
        "    retained_evidence_recorded_at timestamptz;\n    matching_evidence_count integer;\n    canonical_command jsonb;",
        "evidence-count declaration",
    )
    evidence_select = '''    SELECT evidence.reconciliation_evidence_id,
           evidence.effective_at,
           evidence.recorded_at
    INTO retained_evidence_id,
         retained_evidence_effective_at,
         retained_evidence_recorded_at
    FROM accounting_core.reconciliation_evidence AS evidence
    WHERE evidence.tenant_account_id = NEW.tenant_account_id
      AND evidence.reconciliation_run_id = NEW.reconciliation_run_id
      AND evidence.reconciliation_exception_id = NEW.reconciliation_exception_id
      AND evidence.evidence_type_code = 'exception_resolution_review'
      AND evidence.evidence_reference = NEW.resolution_evidence_reference
      AND evidence.evidence_payload_hash = NEW.resolution_evidence_hash;

    IF retained_evidence_id IS NULL THEN
        RAISE EXCEPTION
            'exception resolution requires one retained exception-scoped reviewed artifact whose reference and digest match the command (reconciliation_exception_resolution_evidence_required)'
            USING ERRCODE = '23514';
    END IF;
'''
    evidence_replace = '''    SELECT count(*)
    INTO matching_evidence_count
    FROM accounting_core.reconciliation_evidence AS evidence
    WHERE evidence.tenant_account_id = NEW.tenant_account_id
      AND evidence.reconciliation_run_id = NEW.reconciliation_run_id
      AND evidence.reconciliation_exception_id = NEW.reconciliation_exception_id
      AND evidence.evidence_type_code = 'exception_resolution_review'
      AND evidence.evidence_reference = NEW.resolution_evidence_reference
      AND evidence.evidence_payload_hash = NEW.resolution_evidence_hash;

    IF matching_evidence_count <> 1 THEN
        RAISE EXCEPTION
            'exception resolution requires exactly one retained exception-scoped reviewed artifact whose reference and digest match the command (reconciliation_exception_resolution_evidence_required)'
            USING ERRCODE = '23514';
    END IF;

    SELECT evidence.reconciliation_evidence_id,
           evidence.effective_at,
           evidence.recorded_at
    INTO retained_evidence_id,
         retained_evidence_effective_at,
         retained_evidence_recorded_at
    FROM accounting_core.reconciliation_evidence AS evidence
    WHERE evidence.tenant_account_id = NEW.tenant_account_id
      AND evidence.reconciliation_run_id = NEW.reconciliation_run_id
      AND evidence.reconciliation_exception_id = NEW.reconciliation_exception_id
      AND evidence.evidence_type_code = 'exception_resolution_review'
      AND evidence.evidence_reference = NEW.resolution_evidence_reference
      AND evidence.evidence_payload_hash = NEW.resolution_evidence_hash;
'''
    text = once(text, evidence_select, evidence_replace, "retained evidence lookup")
    old_pair = '''CREATE OR REPLACE FUNCTION accounting_core.enforce_reconciliation_exception_resolution_pair()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    paired_status text;
BEGIN
    SELECT resolution_status_code
    INTO paired_status
    FROM accounting_core.reconciliation_exception
    WHERE tenant_account_id = NEW.tenant_account_id
      AND reconciliation_run_id = NEW.reconciliation_run_id
      AND reconciliation_exception_id = NEW.reconciliation_exception_id;

    IF paired_status IS DISTINCT FROM NEW.target_resolution_status_code THEN
        RAISE EXCEPTION
            'reconciliation exception resolution command and terminal status must commit atomically (reconciliation_exception_resolution_atomic_pair)'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;
'''
    new_pair = '''CREATE OR REPLACE FUNCTION accounting_core.enforce_reconciliation_exception_resolution_pair()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    paired_status text;
    paired_outbox_count integer;
    expected_event_type text;
BEGIN
    SELECT resolution_status_code
    INTO paired_status
    FROM accounting_core.reconciliation_exception
    WHERE tenant_account_id = NEW.tenant_account_id
      AND reconciliation_run_id = NEW.reconciliation_run_id
      AND reconciliation_exception_id = NEW.reconciliation_exception_id;

    expected_event_type := CASE NEW.target_resolution_status_code
        WHEN 'resolved' THEN 'reconciliation_exception_resolved'
        ELSE 'reconciliation_exception_superseded'
    END;

    SELECT count(*)
    INTO paired_outbox_count
    FROM accounting_integration.outbox_event AS event
    WHERE event.tenant_account_id = NEW.tenant_account_id
      AND event.event_type_code = expected_event_type
      AND event.aggregate_reference =
          'urn:cwl:accounting:reconciliation_exception:' || NEW.reconciliation_exception_id::text
      AND event.payload_reference =
          'urn:cwl:accounting:reconciliation_exception_resolution:'
          || NEW.reconciliation_exception_resolution_command_id::text
      AND event.payload_hash = NEW.reconciliation_exception_resolution_command_hash;

    IF paired_status IS DISTINCT FROM NEW.target_resolution_status_code
       OR paired_outbox_count <> 1 THEN
        RAISE EXCEPTION
            'reconciliation exception resolution command, terminal status, and matching outbox event must commit atomically (reconciliation_exception_resolution_atomic_pair)'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;
'''
    text = once(text, old_pair, new_pair, "deferred status/outbox pair guard")
    MIGRATION.write_text(text, encoding="utf-8")


def patch_strict_json() -> None:
    """Reject Python-only value shapes before canonical JSON hashing."""
    text = RESOLUTION.read_text(encoding="utf-8")
    text = once(text, "import json\nfrom typing import Mapping", "import json\nimport math\nfrom typing import Mapping", "math import")
    old = '''def _source_payload_hash(command: Mapping[str, object]) -> str:
    """Hash the complete strict-JSON command so idempotency binds every received member."""
    try:
        canonical = json.dumps(
            command,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise AccountingValidationError(
            "reconciliation exception resolution payload must contain JSON-compatible values. "
            "Supply the exact JSON command, then retry."
        ) from error
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
'''
    new = '''def _require_strict_json_value(value: object) -> None:
    """Reject Python-only shapes so command identity is one RFC 8259 JSON value domain."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise AccountingValidationError(
            "reconciliation exception resolution payload must contain finite JSON numbers. "
            "Supply the exact JSON command, then retry."
        )
    if isinstance(value, list):
        for item in value:
            _require_strict_json_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise AccountingValidationError(
                    "reconciliation exception resolution JSON object keys must be strings. "
                    "Supply the exact JSON command, then retry."
                )
            _require_strict_json_value(item)
        return
    raise AccountingValidationError(
        "reconciliation exception resolution payload must contain strict JSON values; "
        "tuples, sets, custom mappings, and other Python-only values are not accepted. "
        "Supply the exact JSON command, then retry."
    )


def _source_payload_hash(command: Mapping[str, object]) -> str:
    """Hash the complete strict-JSON command so idempotency binds every received member."""
    _require_strict_json_value(command)
    try:
        canonical = json.dumps(
            command,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise AccountingValidationError(
            "reconciliation exception resolution payload must contain JSON-compatible values. "
            "Supply the exact JSON command, then retry."
        ) from error
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
'''
    text = once(text, old, new, "strict JSON hash")
    RESOLUTION.write_text(text, encoding="utf-8")


def patch_small_contracts() -> None:
    """Fix export ordering, scoped lock-wait probing, and operator documentation."""
    text = INIT.read_text(encoding="utf-8")
    text = once(
        text,
        '    "reconcile_reconciliation_run",\n    "resolve_reconciliation_exception",\n    "render_reconciliation_close_package_json",\n    "render_reconciliation_close_review_csv",\n    "render_reconciliation_close_review_json",',
        '    "reconcile_reconciliation_run",\n    "render_reconciliation_close_package_json",\n    "render_reconciliation_close_review_csv",\n    "render_reconciliation_close_review_json",\n    "resolve_reconciliation_exception",',
        "sorted __all__",
    )
    INIT.write_text(text, encoding="utf-8")

    text = LOCK_TEST.read_text(encoding="utf-8")
    old = '''    @staticmethod
    def _waiting_advisory_lock_count() -> int:
        """Return the server-wide number of sessions currently waiting on advisory locks."""
        with psycopg.connect(posting.DATABASE_URL) as monitor:
            return int(
                monitor.execute(
                    """
                    SELECT count(*)
                    FROM pg_locks
                    WHERE locktype = 'advisory' AND NOT granted
                    """
                ).fetchone()[0]
            )
'''
    new = '''    @staticmethod
    def _waiting_advisory_lock_count(tenant_reference: str, lifecycle_scope: str) -> int:
        """Return waiters only for this tenant/run two-key lifecycle advisory lock."""
        with psycopg.connect(posting.DATABASE_URL) as monitor:
            return int(
                monitor.execute(
                    """
                    SELECT count(*)
                    FROM pg_locks
                    WHERE locktype = 'advisory'
                      AND NOT granted
                      AND objsubid = 2
                      AND classid::bigint =
                          ((hashtext(%s)::bigint + 4294967296) %% 4294967296)
                      AND objid::bigint =
                          ((hashtext(%s)::bigint + 4294967296) %% 4294967296)
                    """,
                    (tenant_reference, lifecycle_scope),
                ).fetchone()[0]
            )
'''
    text = once(text, old, new, "scoped advisory lock helper")
    text = text.replace(
        "baseline_waiters = self._waiting_advisory_lock_count()",
        "baseline_waiters = self._waiting_advisory_lock_count(\n                self.fixture.case.policy.tenant_reference, lifecycle_scope\n            )",
    )
    text = text.replace(
        "self._waiting_advisory_lock_count() > baseline_waiters",
        "self._waiting_advisory_lock_count(\n                            self.fixture.case.policy.tenant_reference, lifecycle_scope\n                        ) > baseline_waiters",
    )
    LOCK_TEST.write_text(text, encoding="utf-8")

    text = OPERABILITY.read_text(encoding="utf-8")
    text = once(
        text,
        "exception-resolution commands acquire tenant-scoped transaction advisory locks.\nReconciliation run finalization and exception resolution use the same run-lifecycle\nserialization scope before evidence reads.",
        "exception-resolution commands use tenant-scoped advisory locks. The command-level\n`_acquire_command_lock` boundary is transaction-scoped (`pg_advisory_xact_lock`).\nReconciliation run finalization additionally acquires a session-scoped\n`pg_advisory_lock` on the same tenant/run lifecycle scope before opening the fresh\n`REPEATABLE READ` authority transaction; that lock is owned by the PostgreSQL\nconnection and is explicitly released with `pg_advisory_unlock` in `finally`.\nException resolution and finalization therefore serialize on the same run-lifecycle\nscope before authority-bearing evidence reads.",
        "operability lock lifetime",
    )
    text = once(
        text,
        "Stop the upgrade/release path and reconstruct the original review provenance through an explicitly reviewed audited migration or create a new reconciliation run when historical command evidence cannot be proven.",
        "Stop the upgrade/release path. Before retrying migration 0020, execute an explicitly reviewed and auditable pre-0020 remediation that removes or lawfully reconstructs every legacy terminal exception row; merely creating a new run cannot satisfy the preflight while those historical terminal rows remain. Only after migration 0020 is installed may creating a new reconciliation run be used as the forward operational alternative when historical review provenance cannot be proven.",
        "legacy remediation guidance",
    )
    OPERABILITY.write_text(text, encoding="utf-8")


def patch_docs() -> None:
    """Align authority claims with strict JSON and database-enforced outbox atomicity."""
    text = ADR.read_text(encoding="utf-8")
    text = once(
        text,
        "A raw `UPDATE reconciliation_exception SET resolution_status_code = ...` is not authority. The database permits the `open` to terminal transition only when exactly one matching resolution command already exists in the same transaction. A deferred constraint requires the command and terminal status to commit as a pair. Tenant row-level security is forced and `PUBLIC` has no table privilege.",
        "A raw `UPDATE reconciliation_exception SET resolution_status_code = ...` is not authority. The database permits the `open` to terminal transition only when exactly one matching resolution command already exists in the same transaction. A deferred constraint independently requires the command, matching terminal status, and exactly one matching outbox event—same tenant, event type, exception aggregate reference, resolution-command payload reference, and command hash—to exist together at commit. Tenant row-level security is forced and `PUBLIC` has no table privilege.",
        "ADR outbox atomicity",
    )
    text = once(
        text,
        "a separate SHA-256 identity of the complete incoming JSON command, distinct reviewer actor",
        "a separate SHA-256 identity of the complete incoming strict-JSON command (only JSON null/boolean/string/number/array/object values, finite numbers, and string object keys; Python tuples, sets, custom mappings, and non-string keys are rejected), distinct reviewer actor",
        "ADR strict JSON domain",
    )
    ADR.write_text(text, encoding="utf-8")

    text = STRICT_DOC.read_text(encoding="utf-8")
    marker = "`allow_nan=False`"
    if marker not in text:
        raise RuntimeError("strict JSON doctoring anchor missing")
    if "Python tuples" not in text:
        text += "\n\nThe command identity boundary also recursively admits only RFC 8259-shaped values: JSON null/boolean/string/number/array/object, finite numbers, and string object keys. Python tuples, sets, custom mappings, non-string keys, and other Python-only values are rejected before serialization so distinct Python inputs cannot collapse onto one JSON hash.\n"
    STRICT_DOC.write_text(text, encoding="utf-8")

    text = CHANGELOG.read_text(encoding="utf-8")
    if "strict JSON value domain" not in text:
        anchor = "## Unreleased\n"
        text = once(
            text,
            anchor,
            anchor + "\n- Hardened reconciliation exception-resolution evidence: PostgreSQL now requires exactly one retained reviewed artifact and exactly one matching transactional outbox event at deferred commit, the source-payload identity validates the full strict JSON value domain before hashing, lifecycle lock-wait acceptance is scoped to the exact tenant/run advisory key, and operator guidance distinguishes session- from transaction-scoped advisory locks and pre-0020 legacy remediation.\n",
            "changelog unreleased",
        )
    CHANGELOG.write_text(text, encoding="utf-8")


def patch_json_tests() -> None:
    """Cover all accepted strict JSON scalar/container shapes and rejected Python-only shapes."""
    text = JSON_TESTS.read_text(encoding="utf-8")
    anchor = "\n    def test_finite_json_number_identity_remains_deterministic(self) -> None:\n"
    addition = '''
    def test_python_only_shapes_and_non_string_object_keys_fail_before_hashing(self) -> None:
        """Tuple/set/custom-key shapes cannot collapse into a valid JSON command identity."""
        invalid = (
            {"request_context": ("tuple",)},
            {"request_context": {"set"}},
            {"request_context": {1: "non-string-key"}},
        )
        for command in invalid:
            with self.subTest(command=repr(command)):
                with self.assertRaises(AccountingValidationError):
                    resolution._source_payload_hash(command)

    def test_complete_rfc8259_value_domain_hashes_deterministically(self) -> None:
        """Null, booleans, strings, finite numbers, arrays, and objects retain one identity."""
        command = {
            "null_value": None,
            "boolean_value": True,
            "string_value": "reviewed",
            "integer_value": 7,
            "float_value": 1.25,
            "array_value": [None, False, "x", 2, 3.5, {"nested": "value"}],
            "object_value": {"key": [1, 2, 3]},
        }
        self.assertEqual(
            resolution._source_payload_hash(command),
            resolution._source_payload_hash(command),
        )
'''
    text = once(text, anchor, addition + anchor, "strict JSON tests")
    JSON_TESTS.write_text(text, encoding="utf-8")


def patch_postgres_tests() -> None:
    """Add real PostgreSQL regressions for duplicate evidence and missing outbox pairing."""
    text = POSTGRES_TESTS.read_text(encoding="utf-8")
    anchor = "\n    def test_named_command_resolves_exception_and_emits_atomic_outbox(self) -> None:\n"
    addition = r'''
    def test_database_rejects_ambiguous_duplicate_retained_resolution_evidence(self) -> None:
        """Two retained artifacts with the same authority identity cannot select an arbitrary id."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = self._tenant_id(connection)
            self._retain_resolution_evidence(
                connection,
                tenant_id=tenant_id,
                exception_id=self.exception_id,
                evidence_reference=self.evidence_reference,
                evidence_hash=_EVIDENCE_HASH,
            )
            connection.commit()
            with self.assertRaisesRegex(
                psycopg.Error,
                "reconciliation_exception_resolution_evidence_required",
            ):
                connection.execute(
                    """
                    INSERT INTO accounting_core.reconciliation_exception_resolution_command (
                        tenant_account_id, reconciliation_run_id, reconciliation_exception_id,
                        reconciliation_resolution_idempotency_key,
                        target_resolution_status_code, resolution_evidence_reference,
                        resolution_evidence_hash, source_payload_hash,
                        reconciliation_exception_resolution_command_hash, actor_reference,
                        purpose_code, effective_at
                    )
                    VALUES (%s, %s, %s, %s, 'resolved', %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        tenant_id,
                        self.opened["reconciliation_run_id"],
                        self.exception_id,
                        f"duplicate-evidence-{self.exception_id}",
                        self.evidence_reference,
                        _EVIDENCE_HASH,
                        "sha256:" + "c" * 64,
                        "sha256:" + "0" * 64,
                        "urn:cwl:principal:independent_reviewer",
                        "bank_reconciliation_exception_review",
                        datetime(2026, 9, 2, 0, 20, tzinfo=timezone.utc),
                    ),
                )
            connection.rollback()
        self._assert_no_resolution_side_effects()

    def test_database_rejects_command_status_commit_without_matching_outbox(self) -> None:
        """Deferred authority requires command, terminal status, and matching outbox together."""
        with psycopg.connect(posting.DATABASE_URL) as connection:
            tenant_id = self._tenant_id(connection)
            connection.execute(
                """
                INSERT INTO accounting_core.reconciliation_exception_resolution_command (
                    tenant_account_id, reconciliation_run_id, reconciliation_exception_id,
                    reconciliation_resolution_idempotency_key,
                    target_resolution_status_code, resolution_evidence_reference,
                    resolution_evidence_hash, source_payload_hash,
                    reconciliation_exception_resolution_command_hash, actor_reference,
                    purpose_code, effective_at
                )
                VALUES (%s, %s, %s, %s, 'resolved', %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_id,
                    self.opened["reconciliation_run_id"],
                    self.exception_id,
                    f"missing-outbox-{self.exception_id}",
                    self.evidence_reference,
                    _EVIDENCE_HASH,
                    "sha256:" + "d" * 64,
                    "sha256:" + "0" * 64,
                    "urn:cwl:principal:independent_reviewer",
                    "bank_reconciliation_exception_review",
                    datetime(2026, 9, 2, 0, 20, tzinfo=timezone.utc),
                ),
            )
            connection.execute(
                """
                UPDATE accounting_core.reconciliation_exception
                SET resolution_status_code = 'resolved'
                WHERE tenant_account_id = %s
                  AND reconciliation_exception_id = %s
                """,
                (tenant_id, self.exception_id),
            )
            with self.assertRaisesRegex(
                psycopg.Error,
                "reconciliation_exception_resolution_atomic_pair",
            ):
                connection.commit()
            connection.rollback()
        self._assert_no_resolution_side_effects()
'''
    text = once(text, anchor, addition + anchor, "PostgreSQL review regressions")
    POSTGRES_TESTS.write_text(text, encoding="utf-8")


def remove_temporary_files() -> None:
    """Delete bounded repair machinery from the publishable successor."""
    WORKFLOW.unlink()
    SELF.unlink()


def main() -> int:
    """Apply all guarded current-review repairs."""
    patch_migration()
    patch_strict_json()
    patch_small_contracts()
    patch_docs()
    patch_json_tests()
    patch_postgres_tests()
    remove_temporary_files()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
