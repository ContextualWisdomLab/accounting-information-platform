"""Compatibility shim for the verified book-period normalization lane.

The one-shot repair is deliberately exact-match based. Its reviewed source lives
at the immutable RED-repair head below; this shim patches only matcher vocabulary
that drifted before the RED defect itself changed. The normalization workflow
still removes this temporary file before product validation and publication.
"""

from __future__ import annotations

from pathlib import Path
import subprocess


SCRIPT_PATH = Path(__file__).resolve()
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
previous = replace_known_source_form(
    previous,
    (
        r'            self._acquire_command_lock(connection, f"period:{period_code}")\n',
        '            self._acquire_command_lock(connection, f"period:{period_code}")\n',
    ),
    r'            self._acquire_command_lock(\n                connection, f"period:{period_code}"\n            )\n',
    "close-lock matcher",
)

namespace = {
    "__name__": "__main__",
    "__file__": str(SCRIPT_PATH),
}
exec(compile(previous, str(SCRIPT_PATH), "exec"), namespace)
