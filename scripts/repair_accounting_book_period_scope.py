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
ROOT = SCRIPT_PATH.parents[1]
PERSISTENCE_PATH = ROOT / "src/accounting_information_platform/persistence.py"
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

# The reviewed repair predates Black's multiline rendering of this exact close
# lock. Normalize only that one current product spelling to the reviewed RED
# matcher; the repair immediately replaces it with the book-scoped lock.
persistence = PERSISTENCE_PATH.read_text(encoding="utf-8")
multiline_close_lock = '''            self._acquire_command_lock(
                connection, f"period:{period_code}"
            )
'''
one_line_close_lock = (
    '            self._acquire_command_lock(connection, f"period:{period_code}")\n'
)
if persistence.count(multiline_close_lock) != 1:
    raise SystemExit(
        "close-lock compatibility: expected one current multiline close lock"
    )
PERSISTENCE_PATH.write_text(
    persistence.replace(multiline_close_lock, one_line_close_lock, 1),
    encoding="utf-8",
)

namespace = {
    "__name__": "__main__",
    "__file__": str(SCRIPT_PATH),
}
exec(compile(previous, str(SCRIPT_PATH), "exec"), namespace)
