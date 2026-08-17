# Contributing

This repository is the accounting system of record. Changes that affect posting, receipts, amounts, or authority require a failing test for the accounting and idempotency invariant before behavior changes.

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

The validator checks required files, public docstrings, closed JSON Schema contracts, two-word SQL names, hash-locked quality dependencies, and unresolved placeholders. Quality dependencies include `coverage` and the `setuptools` build backend required for offline editable and wheel installs.

## Documentation

Product architecture and authority live in `docs/`. Accepted decisions are in `docs/adr/`. The standards bibliography and traceability matrix are in `docs/doctoring/`. The next durable intake increment is described in `docs/doctoring/IMPLEMENTATION_SEQUENCE.md`.
