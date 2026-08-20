"""One-shot normalization removing the HomeTax date.min sentinel from durable receipts."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    """Return one repository UTF-8 file."""
    return (ROOT / path).read_text(encoding="utf-8")


def _write(path: str, content: str) -> None:
    """Replace one repository UTF-8 file."""
    (ROOT / path).write_text(content, encoding="utf-8")


def update_persistence() -> None:
    """Use the resolved fiscal-period end when an incomplete register has no date."""
    path = "src/accounting_information_platform/persistence.py"
    text = _read(path)
    before, remainder = text.split("    def persist_home_tax_submission(\n", 1)
    method, after = remainder.split("\n    def load_home_tax_submissions(\n", 1)

    old_date = (
        "        as_of_date = date.fromisoformat(raw_as_of_date) "
        "if raw_as_of_date else date.min\n"
    )
    new_date = (
        "        as_of_date = date.fromisoformat(raw_as_of_date) "
        "if raw_as_of_date else None\n"
    )
    if old_date in method:
        method = method.replace(old_date, new_date, 1)
    elif new_date not in method:
        raise SystemExit("HomeTax as_of_date fallback anchor drifted")

    old_period = '''            period_id, _period_status, _period_end_date = self._require_fiscal_period(
                connection,
                tenant_id,
                period_code,
                next_action="the home-tax-submission",
            )
            row = connection.execute(
'''
    new_period = '''            period_id, _period_status, period_end_date = self._require_fiscal_period(
                connection,
                tenant_id,
                period_code,
                next_action="the home-tax-submission",
            )
            if as_of_date is None:
                as_of_date = period_end_date
            row = connection.execute(
'''
    if old_period in method:
        method = method.replace(old_period, new_period, 1)
    elif new_period not in method:
        raise SystemExit("HomeTax fiscal-period fallback anchor drifted")

    _write(
        path,
        before
        + "    def persist_home_tax_submission(\n"
        + method
        + "\n    def load_home_tax_submissions(\n"
        + after,
    )


def update_adr() -> None:
    """Record the non-sentinel date rule for incomplete-register rejected receipts."""
    path = "docs/adr/0046-http-home-tax-submission.md"
    text = _read(path)
    old = (
        "Rejected receipts that have resolved catalog foreign keys are persisted on "
        "`accounting_integration.home_tax_submission`. The command must carry a non-empty "
        "tenant-scoped `idempotency_key`; the database makes that key unique per tenant "
        "and stores `as_of_date`, `closing_amount`, and `register_payload_hash`."
    )
    new = (
        "Rejected receipts that have resolved catalog foreign keys are persisted on "
        "`accounting_integration.home_tax_submission`. The command must carry a non-empty "
        "tenant-scoped `idempotency_key`; the database makes that key unique per tenant "
        "and stores `as_of_date`, `closing_amount`, and `register_payload_hash`. When an "
        "incomplete register has no `as_of_date`, the durable rejected receipt uses the "
        "resolved fiscal-period end as scope evidence; AIS never persists `0001-01-01` or "
        "another sentinel date as accounting evidence."
    )
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        raise SystemExit("ADR 0046 HomeTax receipt paragraph drifted")
    _write(path, text)


def update_changelog() -> None:
    """Keep the unreleased HomeTax fix record aligned with durable receipt behavior."""
    path = "CHANGELOG.md"
    text = _read(path)
    old = (
        "The command remains fail-closed and does not transmit to NTS/HomeTax. "
        "ADR 0046 records the boundary."
    )
    new = (
        "The command remains fail-closed and does not transmit to NTS/HomeTax. "
        "An incomplete register rejection with resolved accounting scope now persists the "
        "fiscal-period end instead of `date.min`/`0001-01-01`, so durable tax evidence "
        "never exposes a fabricated sentinel accounting date. ADR 0046 records the boundary."
    )
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        raise SystemExit("CHANGELOG HomeTax fixed entry drifted")
    _write(path, text)


def main() -> None:
    """Apply the HomeTax period-end persistence normalization."""
    update_persistence()
    update_adr()
    update_changelog()


if __name__ == "__main__":
    main()
