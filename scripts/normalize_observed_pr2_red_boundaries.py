"""One-shot normalization for the four exact-head PR #2 RED boundaries."""

from __future__ import annotations

from pathlib import Path


def read(path: str) -> str:
    """Read one repository UTF-8 file."""
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    """Write one repository UTF-8 file."""
    Path(path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    """Replace one exact source anchor or fail closed on drift."""
    text = read(path)
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one repair anchor, found {count}")
    write(path, text.replace(old, new, 1))


def repair_adjusting_zero_boundary() -> None:
    """Reject zero-valued AIS adjusting lines before any durable write."""
    path = "src/accounting_information_platform/accept.py"
    old = '''        amount = _parse_amount(str(raw_line.get("amount") or ""))
        debit_amount = amount if debit_credit_code == "debit" else Decimal("0")
'''
    new = '''        amount = _parse_amount(str(raw_line.get("amount") or ""))
        if amount <= 0:
            raise AccountingValidationError(
                "amount must be greater than zero. "
                "Supply a positive exact decimal amount, then retry the journal post."
            )
        debit_amount = amount if debit_credit_code == "debit" else Decimal("0")
'''
    replace_once(path, old, new)


def repair_home_tax_http_status_boundary() -> None:
    """Give malformed HomeTax commands a stable 422 exception type."""
    accept_path = "src/accounting_information_platform/accept.py"
    class_anchor = '''_ALLOWED_OUTBOX_EVENT_TYPE_CODES = frozenset(
    {"posting_receipt", "period_close", "journal_reversal"}
)


def accept_journal_proposal(
'''
    class_new = '''_ALLOWED_OUTBOX_EVENT_TYPE_CODES = frozenset(
    {"posting_receipt", "period_close", "journal_reversal"}
)


class HomeTaxRequestValidationError(AccountingValidationError):
    """Raised when a HomeTax command is malformed before catalog lookup."""


def accept_journal_proposal(
'''
    replace_once(accept_path, class_anchor, class_new)
    text = read(accept_path)
    start = text.index("def accept_home_tax_submission(")
    end = text.index("\ndef lookup_home_tax_submissions(", start)
    method = text[start:end]
    method = method.replace(
        'raise AccountingValidationError(\n            "home tax submission idempotency_key is required.',
        'raise HomeTaxRequestValidationError(\n            "home tax submission idempotency_key is required.',
    )
    method = method.replace(
        'raise AccountingValidationError(\n            "home tax submission source_payload_hash is required',
        'raise HomeTaxRequestValidationError(\n            "home tax submission source_payload_hash is required',
    )
    method = method.replace(
        'raise AccountingValidationError(\n            "home tax submission source_payload_reference is required.',
        'raise HomeTaxRequestValidationError(\n            "home tax submission source_payload_reference is required.',
    )
    if method.count("HomeTaxRequestValidationError") != 5:
        raise SystemExit("accept.py: HomeTax command-validation replacement drifted")
    write(accept_path, text[:start] + method + text[end:])

    http_path = "src/accounting_information_platform/http_api.py"
    replace_once(
        http_path,
        '''from .accept import (
    accept_adjusting_journal,
''',
        '''from .accept import (
    HomeTaxRequestValidationError,
    accept_adjusting_journal,
''',
    )
    replace_once(
        http_path,
        '''        except AccountingValidationError as error:
            self._write_error(404, str(error))
            return
        self._write_json(422, document)

    def _post_billing_proposal_pull''',
        '''        except HomeTaxRequestValidationError as error:
            self._write_error(422, str(error))
            return
        except AccountingValidationError as error:
            self._write_error(404, str(error))
            return
        self._write_json(422, document)

    def _post_billing_proposal_pull''',
    )


def repair_outbox_tenant_invariant() -> None:
    """Make tenant identity a database-owned outbox invariant on upgrade and clean install."""
    path = "database/migrations/0006_concurrency_hot_partition.sql"
    replace_once(
        path,
        '''BEGIN;

-- tenant-leading indexes keep high-write scans bounded while the normalized
''',
        '''BEGIN;

ALTER TABLE accounting_integration.outbox_event
    ALTER COLUMN tenant_account_id SET NOT NULL;

-- tenant-leading indexes keep high-write scans bounded while the normalized
''',
    )


def repair_statement_package_snapshot() -> None:
    """Assemble all four statements inside one REPEATABLE READ ledger snapshot."""
    persistence_path = "src/accounting_information_platform/persistence.py"
    method_anchor = '''    def load_period_close_package(
        self,
        legal_entity_reference: str,
'''
    package_methods = '''    def load_financial_statement_package(
        self,
        legal_entity_reference: str,
        accounting_book_reference: str,
        period_code: str,
        comparison_period_code: str = "",
        statement_scope_code: str = "",
    ) -> dict[str, object]:
        """Return all four financial statements from one REPEATABLE READ snapshot."""
        with self._consistent_read_session():
            return self._assemble_financial_statement_package(
                legal_entity_reference,
                accounting_book_reference,
                period_code,
                comparison_period_code=comparison_period_code,
                statement_scope_code=statement_scope_code,
            )

    def _assemble_financial_statement_package(
        self,
        legal_entity_reference: str,
        accounting_book_reference: str,
        period_code: str,
        comparison_period_code: str = "",
        statement_scope_code: str = "",
    ) -> dict[str, object]:
        income_statement = self.load_financial_statement(
            legal_entity_reference,
            accounting_book_reference,
            period_code,
            "income_statement",
            comparison_period_code,
            statement_scope_code,
        )
        balance_sheet = self.load_financial_statement(
            legal_entity_reference,
            accounting_book_reference,
            period_code,
            "balance_sheet",
            comparison_period_code,
            statement_scope_code,
        )
        changes_in_equity = self.load_financial_statement(
            legal_entity_reference,
            accounting_book_reference,
            period_code,
            "changes_in_equity",
            comparison_period_code,
            statement_scope_code,
        )
        cash_flow = self.load_financial_statement(
            legal_entity_reference,
            accounting_book_reference,
            period_code,
            "cash_flow",
            comparison_period_code,
            statement_scope_code,
        )
        document: dict[str, object] = {
            "tenant_reference": income_statement["tenant_reference"],
            "legal_entity_reference": income_statement["legal_entity_reference"],
            "accounting_book_reference": income_statement["accounting_book_reference"],
            "book_reference": income_statement["book_reference"],
            "fiscal_period_reference": income_statement["fiscal_period_reference"],
            "income_statement": income_statement,
            "balance_sheet": balance_sheet,
            "changes_in_equity": changes_in_equity,
            "cash_flow": cash_flow,
        }
        if statement_scope_code == "year_to_date":
            document["statement_scope_code"] = "year_to_date"
        return document

''' + method_anchor
    replace_once(persistence_path, method_anchor, package_methods)

    accept_path = "src/accounting_information_platform/accept.py"
    text = read(accept_path)
    start = text.index("def lookup_financial_statement_package(")
    body_start = text.index("    income_statement = lookup_financial_statement(\n", start)
    return_line = text.index("\n    return document\n", body_start) + len("\n    return document\n")
    replacement = '''    ledger = PostgresPostingLedger(database_url, tenant_reference)
    return ledger.load_financial_statement_package(
        legal_entity_reference,
        book_reference,
        _period_code_from_reference(fiscal_period_reference),
        comparison_period_code=_period_code_from_reference(
            comparison_fiscal_period_reference
        ),
        statement_scope_code=statement_scope_code,
    )
'''
    write(accept_path, text[:body_start] + replacement + text[return_line:])

    test_path = "tests/test_financial_statement_package_snapshot.py"
    replace_once(
        test_path,
        '''import accounting_information_platform.accept as accept_module
from tests import test_postgres_posting as posting
''',
        '''import accounting_information_platform.accept as accept_module
from accounting_information_platform.persistence import PostgresPostingLedger
from tests import test_postgres_posting as posting
''',
    )
    text = read(test_path)
    old_block = '''        original_lookup = accept_module.lookup_financial_statement
        lookup_count = 0

        def interleaved_lookup(*args: object, **kwargs: object) -> dict[str, object]:
            nonlocal lookup_count
            document = original_lookup(*args, **kwargs)
            lookup_count += 1
            if lookup_count == 1:
                self.case.ledger.post(self.case._two_line_proposal(), self.case.policy)
            return document

        with mock.patch.object(
            accept_module,
            "lookup_financial_statement",
            side_effect=interleaved_lookup,
        ):
'''
    new_block = '''        original_lookup = PostgresPostingLedger.load_financial_statement
        lookup_count = 0

        def interleaved_lookup(
            ledger: PostgresPostingLedger, *args: object, **kwargs: object
        ) -> dict[str, object]:
            nonlocal lookup_count
            document = original_lookup(ledger, *args, **kwargs)
            lookup_count += 1
            if lookup_count == 1:
                self.case.ledger.post(self.case._two_line_proposal(), self.case.policy)
            return document

        with mock.patch.object(
            PostgresPostingLedger,
            "load_financial_statement",
            autospec=True,
            side_effect=interleaved_lookup,
        ):
'''
    replace_once(test_path, old_block, new_block)


def update_docs() -> None:
    """Keep code-current close-package and changelog contracts."""
    adr_path = "docs/adr/0037-http-financial-statement-package.md"
    replace_once(
        adr_path,
        "The document envelope repeats the tenant, legal entity, book, and period identity. Nested objects `income_statement`, `balance_sheet`, `changes_in_equity`, and `cash_flow` are the exact documents already returned by `lookup_financial_statement` / `GET /financial-statements` for those types and the same scope. The package calls that existing lookup; it does not reimplement statement math.\n",
        "The document envelope repeats the tenant, legal entity, book, and period identity. Nested objects `income_statement`, `balance_sheet`, `changes_in_equity`, and `cash_flow` use the same `PostgresPostingLedger.load_financial_statement` projection for those types and the same scope; the package does not reimplement statement math. All four projections execute inside one PostgreSQL `REPEATABLE READ` snapshot so a concurrent posting cannot tear one statutory package across pre- and post-commit book states.\n",
    )
    changelog_path = "CHANGELOG.md"
    replace_once(
        changelog_path,
        "### Fixed\n\n",
        "### Fixed\n\n- Foundation invariants now reject tenantless transactional-outbox rows at the PostgreSQL column boundary, reject zero-valued AIS adjusting-journal lines before persistence, return HTTP 422 for malformed HomeTax command provenance while preserving 404 for missing accounting catalog scope, and assemble the four-statement financial package from one PostgreSQL `REPEATABLE READ` snapshot.\n",
    )


def main() -> None:
    """Apply the four observed RED repairs and their code-current documentation."""
    repair_adjusting_zero_boundary()
    repair_home_tax_http_status_boundary()
    repair_outbox_tenant_invariant()
    repair_statement_package_snapshot()
    update_docs()


if __name__ == "__main__":
    main()
