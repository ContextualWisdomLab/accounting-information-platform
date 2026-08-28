# ADR 0055: Purpose-bound application authorization

## Status

Accepted

## Context

Tenant authentication identifies the accounting scope but does not establish that a caller may
read a report, post a proposal, reverse a journal, close a period, publish an outbox event, or
submit tax evidence. PostgreSQL role and forced-RLS controls remain necessary database defenses,
but they do not replace an application decision made before a route invokes domain work.

## Decision

The trusted host identity adapter supplies an immutable `AuthenticatedPrincipal` containing only
validated opaque principal, tenant, authentication-context, purpose, permission, and credential-
evidence references. It does not pass bearer tokens, policy documents, or model output into AIS.

The HTTP boundary maps every accounting route to a stable operation code before invoking `accept`
or `lookup`. A missing, unknown, tenant-mismatched, agent-originated high-impact, or insufficient
permission decision fails closed with HTTP 403. Soft-close and hard-close have independent
permissions. Request-body fields, tenant headers, database GUCs, Billing documents, and model
text cannot grant authority.

Every routed decision is appended to the tenant-scoped, forced-RLS
`accounting_integration.authorization_decision_record` table. The record keeps the policy version,
decision, principal/purpose evidence, operation, required permission, request tenant, and bounded
correlation identity. It never stores raw credentials. Database mutation triggers make the evidence
append-only.

The standalone runner has no authenticated principal by default and therefore exposes only health
status until a trusted host adapter supplies a validated context to
`create_journal_proposal_server` or `run_journal_proposal_server`.

## Consequences

- Catalog readers do not implicitly receive posting or close authority.
- A service or human principal can receive explicit permissions through the same host-neutral port.
- Agent/model contexts are denied high-impact operations by default.
- Authorization evidence is durable and tenant isolated, while journal and command evidence keeps
  its existing transaction boundaries.
- Deployment must grant the runtime login INSERT access to the authorization evidence table and
  provision the host adapter before enabling accounting routes.

## Alternatives rejected

- Treating `X-CWL-Tenant-Reference` as a bearer credential would make tenant identity equal to
  authority.
- Reading permission claims from request JSON or model text would let an untrusted caller promote
  itself.
- Storing raw JWTs or full policy documents would add unnecessary secret and PII exposure.

## Evidence

`src/accounting_information_platform/authorization.py` owns the immutable decision contract and
`http_api.py` performs route mapping before domain dispatch. Migration
`0015_authorization_decision_evidence.sql` owns tenant isolation and append-only audit evidence.
