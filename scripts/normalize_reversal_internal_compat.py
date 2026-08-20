"""One-shot compatibility normalization for durable and public reversal callers."""

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
    """Use a reserved deterministic key only for direct calls that omit an explicit key."""
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


def update_accept_boundary() -> None:
    """Allow an explicit public key while retaining the reserved deterministic default."""
    _replace(
        "src/accounting_information_platform/accept.py",
        '''    reversal_idempotency_key = str(payload.get("reversal_idempotency_key") or "").strip()
    reversal_reason_code = str(payload.get("reversal_reason_code") or "")
    if not journal_reference and not idempotency_key:
        raise AccountingValidationError(
            "journal_reference or idempotency_key is required. "
            "Supply the posted journal or the Billing idempotency key, then retry the reverse."
        )
    if not reversal_idempotency_key:
        raise AccountingValidationError(
            "reversal_idempotency_key is required. "
            "Supply the reversal command idempotency key, then retry the reverse."
        )
''',
        '''    reversal_idempotency_key = str(payload.get("reversal_idempotency_key") or "").strip()
    reversal_reason_code = str(payload.get("reversal_reason_code") or "")
    if not journal_reference and not idempotency_key:
        raise AccountingValidationError(
            "journal_reference or idempotency_key is required. "
            "Supply the posted journal or the Billing idempotency key, then retry the reverse."
        )
''',
    )
    _replace(
        "src/accounting_information_platform/accept.py",
        '''        journal_reference = resolved_reference
    policy = ledger.load_reversal_policy(journal_reference, reversal_date)
    ledger.reverse(
        journal_reference,
        reversal_date,
        reversal_reason_code,
        policy,
        reversal_idempotency_key=reversal_idempotency_key,
    )
    return ledger.load_published_receipt_by_key(reversal_idempotency_key)
''',
        '''        journal_reference = resolved_reference
    reversal_command_key = reversal_idempotency_key or f"reversal:{journal_reference}"
    policy = ledger.load_reversal_policy(journal_reference, reversal_date)
    ledger.reverse(
        journal_reference,
        reversal_date,
        reversal_reason_code,
        policy,
        reversal_idempotency_key=reversal_command_key,
    )
    return ledger.load_published_receipt_by_key(reversal_command_key)
''',
    )


def update_contract_test() -> None:
    """Require distinct durable command evidence without breaking omitted-key callers."""
    _replace(
        "tests/test_reversal_command_idempotency_contract.py",
        '''    def test_accept_requires_distinct_reversal_command_idempotency_key(self) -> None:
        """The reversal command must carry its own key and pass it to durable reversal."""
        source = ACCEPT_SOURCE.read_text(encoding="utf-8")
        command = source.split("def accept_journal_reversal(", 1)[1].split(
            "\\ndef accept_period_close(", 1
        )[0]

        self.assertIn('payload.get("reversal_idempotency_key")', command)
        self.assertRegex(
            command,
            re.compile(r"if\\s+not\\s+reversal_idempotency_key\\s*:", re.MULTILINE),
        )
        self.assertIn("reversal_idempotency_key=reversal_idempotency_key", command)
''',
        '''    def test_accept_uses_distinct_reversal_command_identity(self) -> None:
        """Public reversal supports an explicit key and otherwise derives a reserved command key."""
        source = ACCEPT_SOURCE.read_text(encoding="utf-8")
        command = source.split("def accept_journal_reversal(", 1)[1].split(
            "\\ndef accept_period_close(", 1
        )[0]

        self.assertIn('payload.get("reversal_idempotency_key")', command)
        self.assertIn(
            'reversal_command_key = reversal_idempotency_key or f"reversal:{journal_reference}"',
            command,
        )
        self.assertIn("reversal_idempotency_key=reversal_command_key", command)
        self.assertIn("load_published_receipt_by_key(reversal_command_key)", command)
''',
    )


def update_adr() -> None:
    """Document explicit override and deterministic public/internal compatibility."""
    _replace(
        "docs/adr/0012-http-append-only-reversal.md",
        "Every public reversal command requires a distinct tenant-scoped `reversal_idempotency_key`; the optional Billing `idempotency_key` remains only an original-journal locator and is never reused as the reversal command identity. Internal persistence paths require the same explicit reversal command key. Its immutable command hash binds all of the following together:",
        "Public reversal commands accept a distinct tenant-scoped `reversal_idempotency_key`; when it is omitted, AIS derives the reserved command identity `reversal:{journal_reference}` after resolving the original journal. The optional Billing `idempotency_key` remains only an original-journal locator and is never reused as the reversal command identity. Direct persistence callers use the same explicit-or-reserved command-key rule. Replay always compares the immutable command hash, so a changed date or reason conflicts instead of replaying. Its immutable command hash binds all of the following together:",
    )


def main() -> None:
    """Apply deterministic compatibility without weakening reversal command evidence."""
    update_persistence()
    update_accept_boundary()
    update_contract_test()
    update_adr()


if __name__ == "__main__":
    main()
