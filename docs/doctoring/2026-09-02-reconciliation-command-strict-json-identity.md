# Reconciliation exception-resolution strict JSON source identity

**Date:** 2026-09-02  
**Owning boundary:** Reconciliation Review / exception-resolution command admission

## Problem

`reconciliation_exception_resolution.source_payload_hash` is the immutable identity of the complete incoming command used for idempotent replay/conflict detection. Python's standard `json.dumps()` has two behaviors that are too permissive for that durable authority boundary: it can emit IEEE-754 non-finite values such as `NaN`/`Infinity` unless explicitly forbidden, and it can normalize some Python-only structures in ways that are not the value domain produced by a strict RFC 8259 JSON command. In particular, tuples can serialize as arrays and non-string mapping keys can be coerced to JSON object-member names.

Durable command identity must therefore be defined over one explicit JSON value domain rather than over whatever Python objects the encoder happens to accept.

## Decision

Before hashing, `_require_strict_json_value()` recursively admits only JSON-shaped values: `null`, booleans, strings, integers, finite-or-encoder-validated numbers, lists, and dictionaries whose every key is a string. Tuples, sets, non-string mapping keys, custom containers, and other Python-only structures fail closed with `AccountingValidationError` before persistence or idempotency identity is produced.

Canonical serialization then uses `json.dumps(..., allow_nan=False, separators=(",", ":"), sort_keys=True)`, so `NaN`, positive infinity, and negative infinity also fail rather than entering durable evidence. Valid JSON values retain deterministic canonical hashing. This is an admission/identity constraint only; it grants no posting, reversal, reconciliation-completion, period-close, tax, or accounting-policy authority.

The HTTP JSON decoder remains a transport adapter. The authoritative exception-resolution command boundary independently validates the value domain before hashing, so a permissive caller or alternate in-process adapter cannot smuggle Python-specific values into audit identity.

## RED → GREEN evidence

`tests/test_reconciliation_exception_resolution_json_identity.py` first records the non-finite-number boundary. `tests/test_reconciliation_exception_resolution_strict_json_red.py` then records the broader RED contract: tuples, sets, and non-string mapping keys must be rejected, while nested RFC-8259-shaped scalars, arrays, and objects retain one stable `sha256:` identity. Production now validates that domain recursively before invoking the canonical encoder.

Exact-head repository/PostgreSQL/security/supply-chain/review gates remain the integration evidence boundary. This source decision is not a claim that a queued or predecessor workflow passed.

## References

Bray, T. (2017). *The JavaScript Object Notation (JSON) Data Interchange Format* (RFC 8259). RFC Editor. https://www.rfc-editor.org/rfc/rfc8259

Python Software Foundation. (2026). *json — JSON encoder and decoder* (Python 3.14 documentation). https://docs.python.org/3.14/library/json.html
