# Contributing

This repository is the accounting system of record. Changes that affect posting, receipts, amounts, period control, reconciliation evidence, or authority require a realistic failing test for the relevant accounting/idempotency invariant before behavior changes.

## Local validation

From the repository root:

```bash
python3 -m pip install --only-binary=:all: --require-hashes -r requirements-quality.txt
PYTHONPATH=src:. python3 -m unittest discover -s tests -p 'test_*.py' -v
PYTHONPATH=src:. python3 -m coverage run --branch -m unittest discover -s tests -p 'test_*.py'
python3 -m coverage report --fail-under=100 --show-missing
python3 scripts/validate_repository.py .
python3 -m compileall -q src scripts tests
```

The PostgreSQL integration suite also requires a reachable PostgreSQL 18 instance through `ACCOUNTING_DATABASE_URL`. Repository-owned CI is authoritative only when it verifies the exact current head; queued, skipped, cancelled, stale, predecessor, or aggregate-only evidence is non-passing.

## Repository guidance

- Canonical contributor and agent operations: [`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md)
- Durable implementation sequence: [`docs/doctoring/IMPLEMENTATION_SEQUENCE.md`](docs/doctoring/IMPLEMENTATION_SEQUENCE.md)
- Product authority and runtime boundaries: [`AGENTS.md`](AGENTS.md), [`CLAUDE.md`](CLAUDE.md), and [`docs/ACCOUNTING_BOUNDARY.md`](docs/ACCOUNTING_BOUNDARY.md)
- Standards bibliography and traceability: [`docs/doctoring/REFERENCES.md`](docs/doctoring/REFERENCES.md) and [`docs/doctoring/STANDARD_TRACEABILITY.md`](docs/doctoring/STANDARD_TRACEABILITY.md)

Do not use sibling repository worktrees as hidden runtime or verification dependencies. Integrate through published package/API/event contracts only.