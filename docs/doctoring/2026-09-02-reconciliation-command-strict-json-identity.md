# Reconciliation exception-resolution strict JSON source identity

**Date:** 2026-09-02  
**Owning boundary:** Reconciliation Review / exception-resolution command admission

## Problem

`reconciliation_exception_resolution.source_payload_hash` is the immutable identity of the complete incoming command used for idempotent replay/conflict detection. Python's standard `json.dumps()` accepts IEEE-754 non-finite values by default and serializes them as `NaN`, `Infinity`, and `-Infinity`. Those tokens are not JSON numbers under RFC 8259, so accepting them would allow a value described as a JSON command identity to depend on a Python-specific extension and reduce interoperability of durable audit evidence.

## Decision

Canonical command hashing uses `json.dumps(..., allow_nan=False, separators=(",", ":"), sort_keys=True)`. `NaN`, positive infinity, and negative infinity therefore fail before database work with the existing caller-facing `AccountingValidationError`. Finite JSON numbers keep the existing deterministic canonical-hash behavior. This is an admission/identity constraint only; it grants no posting, reversal, reconciliation-completion, period-close, tax, or accounting-policy authority.

The HTTP JSON decoder remains a transport adapter and may be more permissive than RFC 8259. The authoritative exception-resolution command boundary nevertheless rejects a non-finite value before persistence or idempotency identity is produced. Any future repository-wide strict-parser change should be a separate transport-owned slice with all endpoint compatibility tests.

## RED → GREEN evidence

`tests/test_reconciliation_exception_resolution_json_identity.py` first records the failing boundary: all three non-finite Python float values must be rejected, while a finite numeric member must retain a stable `sha256:` identity. Production then opts out of Python's permissive encoder extension by setting `allow_nan=False` in `_source_payload_hash()`.

Exact-head repository/PostgreSQL/security/supply-chain/review gates remain the integration evidence boundary. This source decision is not a claim that a queued or predecessor workflow passed.

## References

Bray, T. (2017). *The JavaScript Object Notation (JSON) Data Interchange Format* (RFC 8259). RFC Editor. https://www.rfc-editor.org/rfc/rfc8259

Python Software Foundation. (2026). *json — JSON encoder and decoder* (Python 3.14 documentation). https://docs.python.org/3.14/library/json.html
