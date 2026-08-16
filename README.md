# Accounting Information Platform

The **Accounting Information Platform** is CWL's authoritative accounting and financial-reporting control plane. It receives evidence-backed accounting proposals from billing and other source systems, resolves legal entity, accounting book, chart accounts, fiscal period, currency, and policy, and returns authoritative posting receipts.

## Authority boundary

```text
CWL products
  -> source facts
  -> Metering Billing Platform
  -> accounting_journal_proposal
  -> Accounting Information Platform
  -> posted journal / hold / rejection / reversal
  -> accounting_posting_receipt
  -> trial balance and financial reporting
```

The Metering Billing Platform owns usage, pricing, invoice intent, collections, refunds, provider settlement, and commercial reconciliation. This repository owns legal books, posted journals, fiscal-period control, chart-account resolution, reversals, trial balances, and future financial-statement projections. A balanced proposal is not a posted journal.

## Initial executable vertical

The first milestone provides a dependency-free Python reference core that proves the accounting invariants before API and PostgreSQL adapters are added:

- exact decimal parsing with no binary floating-point arithmetic;
- balanced journal proposals with unique line numbers;
- tenant, legal-entity, book-role, period, currency, and account-role checks;
- idempotent replay and payload-conflict rejection;
- append-only posting and reversal lineage;
- deterministic trial-balance aggregation;
- PostgreSQL 18.4 normalized schema with tenant-scoped foreign keys and row-level security;
- versioned proposal, posting-receipt, and accounting-policy schemas.

## Run validation

```bash
python3 -m pip install --only-binary=:all: --require-hashes -r requirements-quality.txt
PYTHONPATH=src:. python3 -m unittest discover -s tests -p 'test_*.py' -v
PYTHONPATH=src:. python3 -m coverage run --branch -m unittest discover -s tests -p 'test_*.py'
python3 -m coverage report --fail-under=100 --show-missing
python3 scripts/validate_repository.py .
python3 -m compileall -q src scripts tests
```

## Next customer-visible step

After the foundation merges, implement the durable PostgreSQL proposal-intake transaction: idempotent receipt, source-hash conflict detection, open-period resolution, journal and lines, posting receipt, and transactional outbox in one commit boundary.
