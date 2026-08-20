"""Normalize database-invariant documentation before final immutability repair."""

from __future__ import annotations

from pathlib import Path


def _read(path: str) -> str:
    """Return one UTF-8 repository file."""
    return Path(path).read_text(encoding="utf-8")


def _write(path: str, text: str) -> None:
    """Replace one UTF-8 repository file."""
    Path(path).write_text(text, encoding="utf-8")


def ensure_operability_anchor() -> None:
    """Materialize the database-invariant section required by the final repair."""
    path = "docs/OPERABILITY.md"
    text = _read(path).rstrip()
    anchor = (
        "PostgreSQL deferred constraint triggers verify the complete journal population "
        "when a transaction commits. Every durable `general_journal` must contain lines "
        "with exactly equal debit and credit totals. A direct-SQL mutation that leaves a "
        "journal empty or unbalanced fails with `journal_unbalanced`; repair the transaction "
        "before retrying rather than disabling the trigger."
    )
    if anchor in text:
        return
    section = (
        "\n\n## Database-owned journal write invariants\n\n"
        "Migration `0005_closed_period_guard.sql` creates the NOLOGIN group role "
        "`accounting_closing_writer`. Grant that role only to purpose-limited AIS database "
        "login roles that execute period close, adjusting journals, or reversals; do not grant "
        "it to Billing, reporting, or ordinary journal-ingestion roles. A runtime transaction "
        "must still set `accounting_core.journal_write_role` to `period_closing`, `adjusting`, "
        "or `reversal`. The GUC classifies the operation but never grants authority by itself.\n\n"
        + anchor
    )
    _write(path, text + section + "\n")


def ensure_postgres_references() -> None:
    """Keep the role and deferred-trigger decisions traceable to primary PostgreSQL docs."""
    path = "docs/doctoring/REFERENCES.md"
    text = _read(path)
    release_legacy = (
        "PostgreSQL Global Development Group. (2026). *PostgreSQL 18.4 release notes*."
    )
    release_current = (
        "PostgreSQL Global Development Group. (2026c). *PostgreSQL 18.4 release notes*."
    )
    if release_legacy in text:
        text = text.replace(release_legacy, release_current, 1)
    if "PostgreSQL Global Development Group. (2026a). *CREATE TRIGGER*." not in text:
        anchor = release_current
        if anchor not in text:
            raise SystemExit("PostgreSQL release reference anchor drifted")
        primary = (
            "PostgreSQL Global Development Group. (2026a). *CREATE TRIGGER*. "
            "https://www.postgresql.org/docs/18/sql-createtrigger.html\n\n"
            "PostgreSQL Global Development Group. (2026b). *Database roles*. "
            "https://www.postgresql.org/docs/18/user-manag.html\n\n"
        )
        text = text.replace(anchor, primary + anchor, 1)
    _write(path, text)


def verify_current_adr_contract() -> None:
    """Fail if the branch no longer contains the reviewed balance/role decision text."""
    text = _read("docs/adr/0003-append-only-journals.md")
    required = (
        "A soft-close exception requires both an AIS transaction classification",
        "membership of the session login in the NOLOGIN `accounting_closing_writer` database role",
        "deferred constraint triggers on `general_journal` and `journal_entry_line`",
        "`journal_unbalanced`",
    )
    missing = [fragment for fragment in required if fragment not in text]
    if missing:
        raise SystemExit(f"ADR 0003 database-invariant contract drifted: {missing[0]}")


def main() -> None:
    """Prepare reviewed doctoring anchors without changing accounting behavior."""
    verify_current_adr_contract()
    ensure_operability_anchor()
    ensure_postgres_references()


if __name__ == "__main__":
    main()
