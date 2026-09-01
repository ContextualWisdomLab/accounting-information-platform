# Doctoring record: request-scoped principal authority

**Date:** 2026-09-01
**Scope:** purpose-bound accounting HTTP authorization boundary

## Research question

Can a multithreaded accounting HTTP server safely keep one validated `AuthenticatedPrincipal` on the server object and reuse that principal for every request, or must authenticated identity be resolved independently for each request before purpose-bound authorization?

## Finding

A server-wide authenticated principal collapses authentication and transport-session boundaries. Any client able to reach the server and satisfy the tenant route binding inherits the permissions, purpose, principal provenance, and credential evidence of the principal cached on the shared server object. `ThreadingHTTPServer` makes the defect especially explicit: multiple independent requests are handled through one server instance, so server-owned caller identity is not request-owned security context.

The repaired contract therefore accepts a trusted `request_principal_resolver` rather than a reusable `authorization_context`. `_authorize_request()` invokes that resolver for every request after tenant binding and before the authorization decision is evaluated. The trusted host adapter remains responsible for validating signature, issuer, audience, expiry, token binding, and any deployment-specific credential/session evidence. AIS receives only the resulting host-neutral `AuthenticatedPrincipal` and never promotes request-body, document, header, or model content into authority.

If the trusted identity adapter is unavailable or raises unexpectedly, the route fails closed with a generic 503 before any allow-shaped authorization evidence is recorded. A missing resolver produces an ordinary denied authorization decision rather than inheriting a previous caller. Authorization-decision persistence remains fail-closed as well.

## RED → GREEN traceability

| Requirement | Evidence |
| --- | --- |
| Public server construction cannot install one principal for all callers | `tests/test_request_scoped_authorization_context.py::test_public_server_factory_requires_a_request_principal_resolver` |
| Independent requests on one server obtain independent principals and permissions | `tests/test_request_scoped_authorization_context.py::test_each_request_resolves_its_own_validated_principal` |
| Identity-adapter outage cannot fall back to cached/shared authority | `tests/test_request_scoped_authorization_context.py::test_identity_adapter_failure_is_fail_closed_before_audit_allow` |
| Existing purpose/permission and audit behavior remains intact | `tests/test_authorization.py` plus the real PostgreSQL authorization tests exercised by Accounting Foundation CI |
| Repository documentation exposes the same boundary | `docs/ARCHITECTURE.md`, `docs/SECURITY.md`, `docs/OPERABILITY.md`, ADR 0055, and `CHANGELOG.md` |

Focused exact-source verification during the bounded repair passed 25 authorization tests, repository validation, and Python compilation with `PYTHONPATH=src:.`. The normalized connector commit that adds this doctoring record is intentionally separate so ordinary synchronize-triggered exact-head workflows can validate the final source after the temporary repair workflow self-deleted.

## DDD and authority boundary

Identity/Policy remains a foreign bounded context supplied by Keyverse or another trusted host adapter. Accounting is the policy-enforcement point for accounting operations. `AuthenticatedPrincipal` is an anti-corruption value object carrying only already-validated authority attributes needed by AIS. It is request-scoped evidence, not a singleton, server configuration, tenant identity, or domain aggregate.

This repair does not grant posting, reversal, reconciliation approval, period-close, tax-submission, outbox-publication, or policy-change authority. It prevents one validated caller from becoming the implicit authority of unrelated requests. High-impact operations still require their explicit purpose-bound permission and immutable allow/deny evidence.

## Research basis

Logrippo, L. (2025). Data flow security in role-based access control. *Journal of Information Security and Applications, 91*, 103997. https://doi.org/10.1016/j.jisa.2025.103997

National Institute of Standards and Technology. (2014). *Guide to attribute based access control (ABAC) definition and considerations (NIST Special Publication 800-162)*. https://doi.org/10.6028/NIST.SP.800-162

Python Software Foundation. (2026). *http.server — HTTP servers: ThreadingHTTPServer*. https://docs.python.org/3/library/http.server.html

## Evidence rule

The focused repair result proves the source transformation only. Merge or release authority requires the final unchanged head to reacquire every then-applicable repository and organization check, dependency/security evidence, coverage and repository contracts, and qualifying current-head review. No predecessor workflow result is transferred.
