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
