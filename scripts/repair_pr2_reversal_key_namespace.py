"""Align the in-memory reversal command namespace with PostgreSQL semantics."""

from __future__ import annotations

from pathlib import Path


def _read(path: str) -> str:
    """Return one UTF-8 repository file."""
    return Path(path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    """Replace one UTF-8 repository file."""
    Path(path).write_text(text, encoding="utf-8")


def align_in_memory_command_namespace() -> None:
    """Use one tenant-wide idempotency-key namespace for posts and reversals."""
    path = "src/accounting_information_platform/core.py"
    text = _read(path)

    idempotency_anchor = '''        prior = self._receipts_by_idempotency.get(
            self._tenant_cache_key(tenant_reference, idempotency_key)
        )
'''
    idempotency_replacement = '''        command_cache_key = self._tenant_cache_key(
            tenant_reference, idempotency_key
        )
        if command_cache_key in self._reversal_receipts:
            raise IdempotencyConflictError(
                "idempotency key was already used by a reversal command"
            )
        prior = self._receipts_by_idempotency.get(command_cache_key)
'''
    if idempotency_replacement not in text:
        if idempotency_anchor not in text:
            raise SystemExit("posting idempotency namespace anchor drifted")
        text = text.replace(idempotency_anchor, idempotency_replacement, 1)

    reversal_lookup_anchor = '''        prior = self._reversal_receipts.get(
            self._tenant_cache_key(tenant_reference, journal_reference)
        )
        if prior is None:
            return None
'''
    reversal_lookup_replacement = '''        command_cache_key = self._tenant_cache_key(
            tenant_reference, reversal_idempotency_key
        )
        if command_cache_key in self._receipts_by_idempotency:
            raise IdempotencyConflictError(
                "reversal idempotency key was already used by a posting command"
            )
        prior = self._reversal_receipts.get(command_cache_key)
        if prior is None:
            return None
'''
    if reversal_lookup_replacement not in text:
        if reversal_lookup_anchor not in text:
            raise SystemExit("reversal idempotency namespace lookup anchor drifted")
        text = text.replace(reversal_lookup_anchor, reversal_lookup_replacement, 1)

    store_anchor = '''                self._reversal_receipts[reversal_key] = (
                    command_key,
                    journal_reference,
                    command_hash,
                    receipt,
                )
'''
    store_replacement = '''                self._reversal_receipts[
                    self._tenant_cache_key(original.tenant_reference, command_key)
                ] = (
                    command_key,
                    journal_reference,
                    command_hash,
                    receipt,
                )
'''
    if store_replacement not in text:
        if store_anchor not in text:
            raise SystemExit("reversal occupant cache-store anchor drifted")
        text = text.replace(store_anchor, store_replacement, 1)

    final_store_anchor = '''        self._reversal_receipts[reversal_key] = (
            command_key,
            journal_reference,
            command_hash,
            receipt,
        )
'''
    final_store_replacement = '''        self._reversal_receipts[
            self._tenant_cache_key(original.tenant_reference, command_key)
        ] = (
            command_key,
            journal_reference,
            command_hash,
            receipt,
        )
'''
    if final_store_replacement not in text:
        if final_store_anchor not in text:
            raise SystemExit("reversal final cache-store anchor drifted")
        text = text.replace(final_store_anchor, final_store_replacement, 1)

    _write(path, text)


def strengthen_in_memory_regressions() -> None:
    """Prove key reuse across originals and command families fails closed."""
    path = "tests/test_accounting_core.py"
    tests = _read(path)

    cache_key_anchor = '''        reversal_key = self.ledger._tenant_cache_key(
            self.policy.tenant_reference,
            first.journal_reference,
        )
'''
    cache_key_replacement = '''        reversal_key = self.ledger._tenant_cache_key(
            self.policy.tenant_reference,
            f"reversal:{first.journal_reference}",
        )
'''
    if cache_key_replacement not in tests:
        if cache_key_anchor not in tests:
            raise SystemExit("tenant-defense reversal cache-key anchor drifted")
        tests = tests.replace(cache_key_anchor, cache_key_replacement, 1)

    cross_original_anchor = '''        with self.assertRaises(IdempotencyConflictError):
            self.ledger.reverse(
                first.journal_reference,
                date(2026, 8, 31),
                "duplicate_charge",
                self.policy,
                reversal_idempotency_key=command_key,
            )
        del self.ledger._reversal_receipts[
'''
    cross_original_replacement = '''        with self.assertRaises(IdempotencyConflictError):
            self.ledger.reverse(
                first.journal_reference,
                date(2026, 8, 31),
                "duplicate_charge",
                self.policy,
                reversal_idempotency_key=command_key,
            )
        second = self.ledger.post(
            self._invoice_proposal(
                proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf616",
                idempotency_key="invoice-2-issued-v1",
                source_payload_hash="sha256:" + "f" * 64,
                source_event_references=("urn:cwl:billing:invoice:4",),
            ),
            self.policy,
        )
        with self.assertRaises(IdempotencyConflictError):
            self.ledger.reverse(
                second.journal_reference,
                date(2026, 8, 31),
                "billing_correction",
                self.policy,
                reversal_idempotency_key=command_key,
            )
        del self.ledger._reversal_receipts[
'''
    if cross_original_replacement not in tests:
        if cross_original_anchor not in tests:
            raise SystemExit("cross-original reversal-key regression anchor drifted")
        tests = tests.replace(cross_original_anchor, cross_original_replacement, 1)

    delete_anchor = '''        del self.ledger._reversal_receipts[
            self.ledger._tenant_cache_key(
                self.policy.tenant_reference, first.journal_reference
            )
        ]
'''
    delete_replacement = '''        del self.ledger._reversal_receipts[
            self.ledger._tenant_cache_key(
                self.policy.tenant_reference, command_key
            )
        ]
'''
    if delete_replacement not in tests:
        if delete_anchor not in tests:
            raise SystemExit("reversal command cache-delete anchor drifted")
        tests = tests.replace(delete_anchor, delete_replacement, 1)

    marker = "    def test_same_proposal_id_posts_independently_per_tenant(self) -> None:\n"
    cross_family_test = '''    def test_post_and_reversal_share_one_tenant_idempotency_namespace(self) -> None:
        """A key used by one command family cannot be reused by the other."""
        first_proposal = self._invoice_proposal()
        first = self.ledger.post(first_proposal, self.policy)
        with self.assertRaises(IdempotencyConflictError):
            self.ledger.reverse(
                first.journal_reference,
                date(2026, 8, 31),
                "billing_correction",
                self.policy,
                reversal_idempotency_key=first_proposal.idempotency_key,
            )

        reversal_key = "shared-reversal-command-v1"
        self.ledger.reverse(
            first.journal_reference,
            date(2026, 8, 31),
            "billing_correction",
            self.policy,
            reversal_idempotency_key=reversal_key,
        )
        with self.assertRaises(IdempotencyConflictError):
            self.ledger.post(
                self._invoice_proposal(
                    proposal_id="019d7b92-1aa0-7a7f-b61c-962c0f4bf617",
                    idempotency_key=reversal_key,
                    source_payload_hash="sha256:" + "8" * 64,
                    source_event_references=("urn:cwl:billing:invoice:5",),
                ),
                self.policy,
            )

'''
    if "test_post_and_reversal_share_one_tenant_idempotency_namespace" not in tests:
        if marker not in tests:
            raise SystemExit("cross-family idempotency regression marker drifted")
        tests = tests.replace(marker, cross_family_test + marker, 1)

    _write(path, tests)


def update_contract_docs() -> None:
    """Document the tenant-wide accounting command key namespace."""
    path = "docs/adr/0003-append-only-journals.md"
    text = _read(path)
    sentence = (
        "Posting and reversal commands share one tenant-scoped idempotency-key namespace; "
        "a key previously used by either command family cannot be reused by the other."
    )
    if sentence not in text:
        marker = "\n\n## Consequences"
        if marker not in text:
            raise SystemExit("append-only ADR consequence anchor drifted")
        text = text.replace(marker, f"\n\n{sentence}{marker}", 1)
        _write(path, text)


def main() -> None:
    """Apply in-memory/PostgreSQL idempotency-parity repairs."""
    align_in_memory_command_namespace()
    strengthen_in_memory_regressions()
    update_contract_docs()


if __name__ == "__main__":
    main()
