# Reconciliation-completion authorization policy expansion — 2026-09-01

## Scope

This note records the application-authorization work needed before a buyer-facing reconciliation-completion route can be exposed. It does not consume mutable source from the stacked reconciliation-completion implementation and does not grant database authority.

## RED → GREEN sequence

1. Added `tests/test_reconciliation_completion_authorization_contract.py` requiring a dedicated `complete_reconciliation` operation mapped to `accounting.complete_reconciliation`.
2. The RED contract also proves `accounting.post_proposal`, `accounting.hard_close_period`, and `accounting.read_close` are non-equivalent permissions, and that an `agent` principal remains denied even if its context contains the new permission string.
3. Registered `complete_reconciliation` in `_OPERATION_PERMISSIONS` and `_HIGH_IMPACT_OPERATIONS`.
4. Strengthened the contract to require a new durable authorization policy identifier because the operation/permission vocabulary changed.
5. Bumped `AUTHORIZATION_POLICY_VERSION` to `accounting-authorization-v2` so immutable authorization-decision evidence does not claim the predecessor policy version for an expanded policy set.
6. Corrected ADR 0055's stale malformed-period-close prose: current source conservatively classifies malformed/non-object `/period-closes` bodies as `hard_close_period`, records authorization evidence, then returns either 403 before domain work or the caller-useful 400 validation response for a genuinely hard-close-authorized principal. Invalid structure cannot bypass authorization.

## Authority boundary

The application permission is not the PostgreSQL capability. In the later integrated product, reconciliation completion requires both:

- an application allow decision for `accounting.complete_reconciliation` under the current versioned authorization policy; and
- a tenant-bound runtime identity possessing the separately owned purpose-limited PostgreSQL reconciliation-completion capability.

Neither tenant authentication, a close/posting permission, a database GUC, a copied model/agent permission string, nor possession of only one layer is sufficient. The future HTTP route must invoke authorization before the reconciliation-completion command and retain the decision evidence independently of the command result.

## Research and standards basis

Hu et al.'s NIST SP 800-162 ABAC guidance models authorization as evaluation of subject, object/target, requested operation and contextual/environmental attributes against policy; this supports keeping principal kind, tenant target, purpose and requested accounting operation separate. NIST SP 800-53 Rev. 5 AC-6 requires least privilege for users and processes, supporting a distinct reconciliation-completion permission rather than reusing posting or close authority. Logrippo (2025) provides a current formal RBAC integrity/data-flow basis for reasoning about role/permission assignments and policy reconfiguration.

### APA 7th references

Hu, V. C., Ferraiolo, D., Kuhn, D. R., Schnitzer, A., Sandlin, K., Miller, R., & Scarfone, K. (2019). *Guide to attribute based access control (ABAC) definition and considerations* (NIST Special Publication 800-162, updated August 2, 2019). National Institute of Standards and Technology. https://doi.org/10.6028/NIST.SP.800-162

Joint Task Force. (2020). *Security and privacy controls for information systems and organizations* (NIST Special Publication 800-53 Revision 5; Release 5.2.0 current August 27, 2025). National Institute of Standards and Technology. https://doi.org/10.6028/NIST.SP.800-53r5

Logrippo, L. (2025). Data flow security in role-based access control. *Journal of Information Security and Applications*. https://doi.org/10.1016/j.jisa.2025.103997

## Integration boundary

This sibling authorization branch remains blocked behind the reconciliation dependency root and must be restacked/revalidated against the exact protected integrated base. The documentation-owner branch also carries canonical release-history corrections that must be reconciled before merge. No predecessor check, review, or release claim transfers across that restack.
