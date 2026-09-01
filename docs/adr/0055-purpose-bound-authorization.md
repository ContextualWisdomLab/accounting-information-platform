# ADR 0055: Purpose-bound application authorization

## Status

Accepted

## Context

Tenant authentication identifies the accounting scope but does not establish that a caller may
read a report, post a proposal, reverse a journal, complete reconciliation review, close a period,
publish an outbox event, or submit tax evidence. PostgreSQL role and forced-RLS controls remain
necessary database defenses, but they do not replace an application decision made before a route
invokes domain work.

The authorization contract must also evolve as a versioned policy. Adding a new high-impact
operation while continuing to emit the predecessor policy identifier would make immutable audit
evidence ambiguous: two different operation/permission sets would both claim the same policy
version.

## Decision

The trusted host identity adapter supplies an immutable `AuthenticatedPrincipal` containing only
validated opaque principal, tenant, authentication-context, purpose, permission, and credential-
evidence references plus an explicit `principal_kind`. `principal_kind` must be one of `human`,
`service`, or `agent`; it has no implicit default, so an adapter omission fails before route
authorization. It does not pass bearer tokens, policy documents, or model output into AIS.

The HTTP boundary maps every accounting route to a stable operation code before invoking `accept`
or `lookup`. A missing, unknown, tenant-mismatched, agent-originated high-impact, or insufficient
permission decision fails closed with HTTP 403. Soft-close and hard-close have independent
permissions. Request-body fields, tenant headers, database GUCs, Billing documents, and model
text cannot grant authority.

Malformed or non-object `/period-closes` bodies are conservatively classified as
`hard_close_period` for the authorization step instead of returning `None`. Therefore an
unauthorized caller receives 403 without reaching the close handler, while a caller that actually
holds hard-close authority receives the caller-useful 400 validation response only after durable
authorization-decision evidence has been written. Invalid structure never bypasses authorization
or writes journal/close/outbox facts.

Authorization policy `accounting-authorization-v2` adds the high-impact operation
`complete_reconciliation` with the distinct permission `accounting.complete_reconciliation`.
Posting, read, soft/hard-close, bank-ingest, outbox and tax permissions do not imply this permission.
An `agent` principal is denied this operation by the same default high-impact restriction even if
its untrusted context contains a copied permission string. This policy entry is deliberately
reserved before a buyer-facing reconciliation-completion transport is introduced; registering the
operation grants no route and no database capability by itself.

The reconciliation-completion application permission remains separate from the database
`accounting_reconciliation_completer` capability that is owned by the later reconciliation
completion migration. A trusted application allow decision and a tenant-bound runtime connection
with the purpose-limited database capability are both required once the route exists. Neither
control substitutes for the other, and reconciliation completion remains separate from fiscal-
period close authority.

Every routed decision is appended to the tenant-scoped, forced-RLS
`accounting_integration.authorization_decision_record` table. The record keeps the policy version,
decision, principal/purpose evidence, principal tenant, requested tenant, operation, required
permission, and bounded correlation identity. It never stores raw credentials. Database mutation
triggers make the evidence append-only, and the persistence boundary rejects evidence whose
requested tenant differs from the tenant scope used to store it. The persistence boundary also
accepts only unchanged decisions issued by the `authorize` evaluator, so a caller cannot
construct or mutate an `allowed` decision and promote it into durable evidence; copying an
evaluator decision retains its provenance.

The standalone runner has no authenticated principal by default and therefore exposes only health
status until a trusted host adapter supplies a validated context to
`create_journal_proposal_server` or `run_journal_proposal_server`.

## Consequences

- Catalog readers do not implicitly receive posting, reconciliation-completion, or close authority.
- Reconciliation completion has its own permission and remains a high-impact operation denied to
  model/agent principals by default.
- Extending the operation/permission registry changes the durable policy version, so audit rows can
  identify which exact authorization vocabulary was evaluated.
- A service or human principal can receive explicit permissions through the same host-neutral port.
- Agent/model contexts are denied high-impact operations by default.
- Authorization evidence is durable and tenant isolated, while journal and command evidence keeps
  its existing transaction boundaries.
- Deployment must grant the runtime login INSERT access to the authorization evidence table and
  provision the host adapter before enabling accounting routes.
- The future reconciliation-completion transport must require both
  `accounting.complete_reconciliation` and the separately provisioned database completion
  capability; neither tenant authentication nor one of the other accounting permissions is enough.

## Alternatives rejected

- Treating `X-CWL-Tenant-Reference` as a bearer credential would make tenant identity equal to
  authority.
- Reading permission claims from request JSON or model text would let an untrusted caller promote
  itself.
- Reusing `accounting.hard_close_period`, `accounting.post_proposal`, or a generic writer grant for
  reconciliation completion would collapse distinct business authorities and weaken audit meaning.
- Adding the reconciliation operation without bumping `AUTHORIZATION_POLICY_VERSION` would make
  immutable decision evidence unable to distinguish the predecessor and expanded policy sets.
- Storing raw JWTs or full policy documents would add unnecessary secret and PII exposure.

## Evidence

`src/accounting_information_platform/authorization.py` owns the immutable decision contract and
`http_api.py` performs route mapping before domain dispatch. Migration
`0015_authorization_decision_evidence.sql` owns tenant isolation and append-only audit evidence.
`tests/test_reconciliation_completion_authorization_contract.py` proves that reconciliation
completion has one explicit versioned permission, does not inherit posting/close/read authority,
and remains denied to agent principals by default.

## Research and standards traceability

Hu, V. C., Ferraiolo, D., Kuhn, D. R., Schnitzer, A., Sandlin, K., Miller, R., & Scarfone, K.
(2019). *Guide to attribute based access control (ABAC) definition and considerations* (NIST
Special Publication 800-162, updated August 2, 2019). National Institute of Standards and
Technology. https://doi.org/10.6028/NIST.SP.800-162

Joint Task Force. (2020). *Security and privacy controls for information systems and organizations*
(NIST Special Publication 800-53 Revision 5, Release 5.2.0 current as of August 27, 2025).
National Institute of Standards and Technology. https://doi.org/10.6028/NIST.SP.800-53r5

Logrippo, L. (2025). Data flow security in role-based access control. *Journal of Information
Security and Applications*.
https://consensus.app/papers/data-flow-security-in-rolebased-access-control-logrippo/95874bd5d780530a8e80eece583cda0e/?utm_source=chatgpt

NIST SP 800-162 defines authorization in terms of subject/object/operation/environment attributes,
which supports keeping tenant identity, requested operation, purpose and principal kind as distinct
decision inputs. NIST SP 800-53 Rev. 5 AC-6 supports least privilege and purpose-limited roles and
process privileges. Logrippo (2025) provides a current formal RBAC integrity/data-flow basis for
reasoning about role/permission assignments and reconfiguration. These sources inform control
design only; they do not grant accounting authority or replace exact-head tests and deployment
evidence.
