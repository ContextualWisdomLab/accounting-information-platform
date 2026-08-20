"""One-shot compatibility normalization for direct durable reversal callers."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _replace(path: str, old: str, new: str) -> None:
    """Replace one exact generated block and fail closed on drift."""
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        raise SystemExit(f"{path}: compatibility anchor drifted")
    target.write_text(text, encoding="utf-8")


def update_persistence() -> None:
    """Use a reserved deterministic key only for internal calls that omit an explicit key."""
    _replace(
        "src/accounting_information_platform/persistence.py",
        '''        *,
        reversal_idempotency_key: str,
    ) -> PostingReceipt:
        """Append or exactly replay one immutable reversal command."""
        _require_code(reversal_reason_code, "reversal reason code")
        command_key = reversal_idempotency_key.strip()
        if not command_key:
            raise AccountingValidationError(
                "reversal_idempotency_key is required. "
                "Supply the reversal command idempotency key, then retry reversal."
            )
''',
        '''        *,
        reversal_idempotency_key: str | None = None,
    ) -> PostingReceipt:
        """Append or exactly replay one immutable reversal command."""
        _require_code(reversal_reason_code, "reversal reason code")
        if reversal_idempotency_key is None:
            command_key = f"reversal:{journal_reference}"
        else:
            command_key = reversal_idempotency_key.strip()
            if not command_key:
                raise AccountingValidationError(
                    "reversal_idempotency_key must not be empty. "
                    "Supply the reversal command idempotency key, then retry reversal."
                )
''',
    )


def update_adr() -> None:
    """Document explicit public identity and deterministic internal compatibility separately."""
    _replace(
        "docs/adr/0012-http-append-only-reversal.md",
        "Every public reversal command requires a distinct tenant-scoped `reversal_idempotency_key`; the optional Billing `idempotency_key` remains only an original-journal locator and is never reused as the reversal command identity. Internal persistence paths require the same explicit reversal command key. Its immutable command hash binds all of the following together:",
        "Every public reversal command requires a distinct tenant-scoped `reversal_idempotency_key`; the optional Billing `idempotency_key` remains only an original-journal locator and is never reused as the reversal command identity. Direct internal persistence callers that omit an explicit key use the reserved deterministic identity `reversal:{journal_reference}` for backward-compatible orchestration, but replay still compares the immutable command hash so a changed date or reason conflicts instead of replaying. Its immutable command hash binds all of the following together:",
    )


def main() -> None:
    """Apply deterministic internal compatibility without weakening public command identity."""
    update_persistence()
    update_adr()


if __name__ == "__main__":
    main()
