# Accounting Information Platform Development Context

This repository is CWL's accounting system of record. It is downstream of operational and commercial systems and upstream of trial balance, close, consolidation, and financial reporting.

Before changing behavior:

1. Identify whether the source is authoritative, observed, or proposed.
2. Add a failing test for the accounting and idempotency invariant.
3. Keep original source evidence and payload hashes immutable.
4. Resolve legal entity, book, fiscal period, currency, chart account, and policy in this boundary.
5. Correct posted facts with reversal and replacement records.
6. Return a posting receipt without leaking source-system secrets or customer PII into event broadcasts.
