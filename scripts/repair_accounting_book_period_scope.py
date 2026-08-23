"""Compatibility shim for the verified book-period normalization lane.

The prior one-shot repair was intentionally exact-match based, but its first
matcher drifted from the current persistence API before the RED defect changed.
This shim reuses that reviewed repair program from the immediate parent commit,
patches only the stale matcher vocabulary to the exact current call signature,
and then executes it.  The normalization workflow still removes this temporary
file before product validation and publication.
"""

from __future__ import annotations

from pathlib import Path
import subprocess


SCRIPT_PATH = Path(__file__).resolve()
previous = subprocess.run(
    ["git", "show", "HEAD^:scripts/repair_accounting_book_period_scope.py"],
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
)

for old, new, label in patches:
    count = previous.count(old)
    if count == 0:
        raise SystemExit(f"{label}: prior repair source no longer contains expected anchor")
    previous = previous.replace(old, new)

namespace = {
    "__name__": "__main__",
    "__file__": str(SCRIPT_PATH),
}
exec(compile(previous, str(SCRIPT_PATH), "exec"), namespace)
