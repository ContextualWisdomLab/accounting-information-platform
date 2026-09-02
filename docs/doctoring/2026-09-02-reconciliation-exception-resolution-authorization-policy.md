# Reconciliation-exception resolution authorization policy expansion — 2026-09-02

## Scope

This note records the application-authorization vocabulary required before the reviewed reconciliation-exception resolution command may receive a buyer-facing transport. It does not consume implementation bytes from the stacked exception-resolution branch, does not create a route, and does not grant PostgreSQL capability or accounting posting authority.

## RED → GREEN sequence

1. Added `tests/test_reconciliation_exception_resolution_authorization_contract.py` requiring a dedicated `resolve_reconciliation_exception` operation mapped to `accounting.resolve_reconciliation_exception`.
2. The RED contract proves `accounting.complete_reconciliation`, `accounting.post_proposal`, `accounting.hard_close_period`, and `accounting.read_close` are non-equivalent authorities, and an `agent` principal remains denied even when its context contains the new permission string.
3. Registered `resolve_reconciliation_exception` in `_OPERATION_PERMISSIONS` and `_HIGH_IMPACT_OPERATIONS`.
4. Bumped `AUTHORIZATION_POLICY_VERSION` from `accounting-authorization-v2` to `accounting-authorization-v3`; immutable authorization-decision evidence must not reuse a policy identifier after the operation/permission vocabulary changes.
5. Advanced the existing reconciliation-completion contract to the same v3 vocabulary so both distinct operations are evaluated under one unambiguous policy version.
6. Updated ADR 0064 to preserve the separation between application authorization, reconciliation command authority, database capability, journal posting, and fiscal-period close.

The 2026-09-01 completion-policy note remains historical evidence for the v1 → v2 change. This note is its successor for the v2 → v3 policy expansion.

## Authority boundary

Exception resolution is a reviewed accounting-control decision, not reconciliation completion and not journal posting. A future transport must require all of the following independently:

- a trusted request-scoped identity adapter has validated issuer, audience, signature, expiry, and token binding before AIS receives the principal;
- the versioned application policy allows `resolve_reconciliation_exception` through `accounting.resolve_reconciliation_exception` for the requested tenant and purpose;
- the runtime database identity possesses only the separately owned purpose-limited capability required by the named exception-resolution command; and
- the command itself satisfies its maker-checker, immutable evidence, idempotency, tenant, run, and exception invariants.

A tenant header, a completion permission, posting or close permission, a database GUC, request-body text, Billing evidence, or model output cannot substitute for the exception-resolution permission. Agent/model principals are denied this high-impact operation by default even if an untrusted context copies the permission string.

## DDD and product effect

Within the Reconciliation Review bounded context, `resolve_reconciliation_exception` is the application policy name for invoking the separately owned exception-resolution command. The command's domain evidence remains authoritative for the resolution; the authorization decision is independent access-control evidence explaining why the caller was permitted or denied. Neither becomes a General Ledger journal fact or fiscal-period close command.

## Research and standards basis

NIST SP 800-162 models authorization from subject, target, operation, and environmental/context attributes, supporting an explicit exception-resolution operation rather than deriving authority from tenant identity. NIST SP 800-53 Rev. 5 AC-5 and AC-6 support separation of duties and least privilege, consistent with keeping exception review, reconciliation completion, posting, and close permissions distinct. Logrippo (2025) provides a current formal role/permission integrity and data-flow basis for treating permission-set changes as policy changes that require versioned evidence.

### APA 7th references

Hu, V. C., Ferraiolo, D., Kuhn, D. R., Schnitzer, A., Sandlin, K., Miller, R., & Scarfone, K. (2019). *Guide to attribute based access control (ABAC) definition and considerations* (NIST Special Publication 800-162, updated August 2, 2019). National Institute of Standards and Technology. https://doi.org/10.6028/NIST.SP.800-162

Joint Task Force. (2020). *Security and privacy controls for information systems and organizations* (NIST Special Publication 800-53 Revision 5, Release 5.2.0 current August 27, 2025). National Institute of Standards and Technology. https://doi.org/10.6028/NIST.SP.800-53r5

Logrippo, L. (2025). Data flow security in role-based access control. *Journal of Information Security and Applications*. https://doi.org/10.1016/j.jisa.2025.103997

These sources inform control design only. They do not grant accounting authority, establish SOC 2/CSAP certification, or replace exact-head PostgreSQL, authorization, security, review, and deployment evidence.

## Integration boundary

This authorization sibling remains blocked behind the reconciliation dependency root and must be restacked/revalidated against the exact protected integrated base. The exception-resolution transport itself remains later work: reserving the operation/permission vocabulary is intentionally independent of consuming the current stacked command implementation. No predecessor check, review, or release evidence transfers across the eventual restack.
