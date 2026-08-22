"""One-shot repair for validated close-status and Billing socket review findings."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    """Return one repository text file."""
    return (ROOT / path).read_text(encoding="utf-8")


def _write(path: str, content: str) -> None:
    """Replace one repository text file."""
    (ROOT / path).write_text(content.rstrip() + "\n", encoding="utf-8")


def _replace_once(path: str, old: str, new: str) -> None:
    """Replace exactly one expected source fragment."""
    text = _read(path)
    if text.count(old) != 1:
        raise SystemExit(f"{path}: expected exactly one repair anchor, found {text.count(old)}")
    _write(path, text.replace(old, new, 1))


def patch_period_close_status_allowlist() -> None:
    """Reject unknown close statuses before irreversible close processing."""
    _replace_once(
        "src/accounting_information_platform/accept.py",
        '''    if type(period_status_code) is not str or not period_status_code:
        raise AccountingValidationError(
            "period_status_code must be soft_closed or hard_closed. "
            "Omit the field to hard-close, or supply soft_closed or hard_closed, "
            "then retry the close."
        )
    return period_status_code
''',
        '''    if type(period_status_code) is not str or not period_status_code:
        raise AccountingValidationError(
            "period_status_code must be soft_closed or hard_closed. "
            "Omit the field to hard-close, or supply soft_closed or hard_closed, "
            "then retry the close."
        )
    if period_status_code not in {"soft_closed", "hard_closed"}:
        raise AccountingValidationError(
            "period_status_code must be soft_closed or hard_closed. "
            "Omit the field to hard-close, or supply soft_closed or hard_closed, "
            "then retry the close."
        )
    return period_status_code
''',
    )


def patch_billing_connection_cleanup() -> None:
    """Close partially opened HTTPS connections when connect or TLS wrapping fails."""
    _replace_once(
        "src/accounting_information_platform/billing_pull.py",
        '''def _open_billing_connection(parsed: ParseResult) -> http.client.HTTPConnection:
    port = parsed.port
    if parsed.scheme == "https" and port is None:
        port = 443
    connection = http.client.HTTPConnection(parsed.hostname, port, timeout=5)
    if parsed.scheme == "https":
        connection.connect()
        connection.sock = ssl.create_default_context().wrap_socket(
            connection.sock, server_hostname=parsed.hostname
        )
    return connection
''',
        '''def _open_billing_connection(parsed: ParseResult) -> http.client.HTTPConnection:
    port = parsed.port
    if parsed.scheme == "https" and port is None:
        port = 443
    connection = http.client.HTTPConnection(parsed.hostname, port, timeout=5)
    if parsed.scheme == "https":
        try:
            connection.connect()
            connection.sock = ssl.create_default_context().wrap_socket(
                connection.sock, server_hostname=parsed.hostname
            )
        except OSError:
            connection.close()
            raise
    return connection
''',
    )


def patch_changelog() -> None:
    """Record the buyer-visible reliability and command-validation corrections."""
    path = "CHANGELOG.md"
    text = _read(path)
    bullet = (
        "- Period-close commands now reject unknown non-empty status codes at the command "
        "boundary, and failed Billing HTTPS connect/TLS setup closes the partially opened "
        "connection before returning the existing fail-closed pull error."
    )
    if bullet in text:
        return
    marker = "### Fixed\n\n"
    if marker not in text:
        raise SystemExit("CHANGELOG.md: Fixed anchor drifted")
    _write(path, text.replace(marker, marker + bullet + "\n", 1))


def main() -> None:
    """Apply the two validated current-head repairs and update release notes."""
    patch_period_close_status_allowlist()
    patch_billing_connection_cleanup()
    patch_changelog()


if __name__ == "__main__":
    main()
