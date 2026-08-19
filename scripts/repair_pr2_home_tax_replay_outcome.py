"""One-shot repair for HomeTax replay across changed runtime conditions."""

from __future__ import annotations

from pathlib import Path


def _read(path: str) -> str:
    """Return one UTF-8 repository file."""
    return Path(path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    """Replace one UTF-8 repository file."""
    Path(path).write_text(text, encoding="utf-8")


def preserve_stored_home_tax_outcome() -> None:
    """Treat rejection reason as stored outcome, not as command identity."""
    path = "src/accounting_information_platform/persistence.py"
    text = _read(path)
    old = '''            actual_identity = (
                row[1],
                row[2],
                row[3],
                str(row[4]),
                str(row[5]),
                row[6],
                Decimal(row[7]),
                str(row[8]),
            )
            expected_identity = (
                legal_entity_id,
                book_id,
                period_id,
                "rejected",
                rejection_reason_code,
                as_of_date,
                closing_amount,
                register_payload_hash,
            )
            if actual_identity != expected_identity:
                raise IdempotencyConflictError(
                    "idempotency key was already used with a different HomeTax command"
                )
        return _home_tax_submission_document(
            home_tax_submission_id=str(row[0]),
            tenant_reference=self._tenant_reference,
            legal_entity_reference=legal_entity_reference,
            book_reference=accounting_book_reference,
            period_code=period_code,
            vat_period_register=_home_tax_register_view(register_document),
            rejection_reason_code=rejection_reason_code,
        )
'''
    new = '''            actual_command_identity = (
                row[1],
                row[2],
                row[3],
                row[6],
                Decimal(row[7]),
                str(row[8]),
            )
            expected_command_identity = (
                legal_entity_id,
                book_id,
                period_id,
                as_of_date,
                closing_amount,
                register_payload_hash,
            )
            if actual_command_identity != expected_command_identity:
                raise IdempotencyConflictError(
                    "idempotency key was already used with a different HomeTax command"
                )
            stored_rejection_reason_code = str(row[5])
        return _home_tax_submission_document(
            home_tax_submission_id=str(row[0]),
            tenant_reference=self._tenant_reference,
            legal_entity_reference=legal_entity_reference,
            book_reference=accounting_book_reference,
            period_code=period_code,
            vat_period_register=_home_tax_register_view(register_document),
            rejection_reason_code=stored_rejection_reason_code,
        )
'''
    if old not in text:
        raise SystemExit("HomeTax stored-outcome replay anchor drifted")
    _write(path, text.replace(old, new, 1))


def add_runtime_change_regression() -> None:
    """Prove exact command replay is stable after credential state changes."""
    path = "tests/test_postgres_posting.py"
    tests = _read(path)
    if "test_home_tax_exact_replay_preserves_stored_outcome" in tests:
        return
    marker = "    def test_http_rejects_home_tax_submission_when_transport_unavailable(self) -> None:\n"
    regression = '''    def test_home_tax_exact_replay_preserves_stored_outcome(self) -> None:
        """Runtime credential changes do not change an already-recorded command receipt."""
        server = self._start_http_server()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        command_key = f"{self.policy.tenant_reference}:home_tax_submission:stable:v1"
        first_status, first = self._http_home_tax_submission(
            idempotency_key=command_key
        )
        self.assertEqual(first_status, 422)
        self.assertEqual(first["rejection_reason_code"], "hometax_credential_missing")

        with mock.patch.dict(
            os.environ,
            {"ACCOUNTING_HOMETAX_CREDENTIAL": "configured"},
            clear=False,
        ):
            replay_status, replay = self._http_home_tax_submission(
                idempotency_key=command_key
            )

        self.assertEqual(replay_status, 422)
        self.assertEqual(replay, first)
        self.assertEqual(
            self._count_table("accounting_integration.home_tax_submission"), 1
        )

'''
    if marker not in tests:
        raise SystemExit("HomeTax stored-outcome test insertion marker drifted")
    _write(path, tests.replace(marker, regression + marker, 1))


def update_docs() -> None:
    """Document that idempotent replay returns the original stored outcome."""
    changelog_path = "CHANGELOG.md"
    changelog = _read(changelog_path)
    entry = (
        "- Made exact HomeTax retries return the originally stored rejection receipt even "
        "when runtime credential availability changes; only command scope or immutable "
        "register evidence changes create an idempotency conflict.\n"
    )
    if entry not in changelog:
        marker = "### Changed\n"
        if marker not in changelog:
            raise SystemExit("CHANGELOG changed-section anchor drifted")
        changelog = changelog.replace(marker, marker + "\n" + entry, 1)
        _write(changelog_path, changelog)


def main() -> None:
    """Apply stable HomeTax replay semantics exactly once."""
    preserve_stored_home_tax_outcome()
    add_runtime_change_regression()
    update_docs()


if __name__ == "__main__":
    main()
