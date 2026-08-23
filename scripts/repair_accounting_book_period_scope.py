"""Compatibility shim for the verified book-period normalization lane.

The one-shot repair is deliberately exact-match based. Its reviewed source lives
at the exact RED-repair head below; this shim patches only matcher vocabulary
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

patches = (
    (
        'connection, tenant_id, policy.legal_entity_reference, "the journal post"',
        "connection, tenant_id, proposal.legal_entity_reference",
        "legal-entity matcher",
    ),
    (
        "policy.accounting_book_reference,\\n                proposal.intended_book_role_code,",
        "policy.intended_book_role_code,\\n                policy.accounting_book_reference,",
        "accounting-book matcher",
    ),
    (
        "close_idempotency_key = idempotency_key or (",
        "close_idempotency_key = idempotency_key.strip() or (",
        "close-idempotency matcher",
    ),
    (
        '            self._acquire_command_lock(connection, f"period:{period_code}")\n',
        '            self._acquire_command_lock(\n'
        '                connection, f"period:{period_code}"\n'
        '            )\n',
        "close-lock matcher",
    ),
)

for old, new, label in patches:
    count = previous.count(old)
    if count == 0:
        raise SystemExit(f"{label}: reviewed repair source no longer contains expected anchor")
    previous = previous.replace(old, new)

namespace = {
    "__name__": "__main__",
    "__file__": str(SCRIPT_PATH),
}
exec(compile(previous, str(SCRIPT_PATH), "exec"), namespace)
