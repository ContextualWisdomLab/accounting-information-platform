# Reconciliation completion status-authority hardening — 2026-09-01

## Scope and observed gap

The stacked reconciliation-completion slice grants the `accounting_reconciliation_completer` NOLOGIN capability `UPDATE (run_status_code)` on `accounting_core.reconciliation_run` because PostgreSQL needs a column-level mutation privilege for the evidence-backed transition to `reconciled`.

Review of exact branch source found that the first version of `accounting_core.reconciliation_run_reconciled_guard()` enforced evidence, exception, pending-match, source-state, and capability checks only when `NEW.run_status_code = 'reconciled'`. PostgreSQL's existing check constraint also admits `review_required`, `not_reconciled`, and `superseded`. Consequently, a session that legitimately held the completion capability could have used direct SQL to set one of those other values even though ADR 0059 explicitly reserves those lifecycle edges for separately governed owner commands and evidence.

That was a database-authority defect, not merely a missing API validation. The column privilege was narrower than table-wide UPDATE but still broader than the business transition it was intended to authorize.

## RED → GREEN repair

1. RED contract: `tests/test_reconciliation_completion_contract_red.py` now requires the migration to fail closed on every changed target other than `reconciled`, with stable diagnostic `reconciliation_completion_target_forbidden`.
2. GREEN database guard: migration `0020_reconciliation_completion_command.sql` now returns early only for a no-op status assignment, then rejects every changed target other than `reconciled` before evaluating the completion edge's role/evidence conditions.
3. Installed-state proof: `tests/test_postgres_reconciliation_completion_migration.py` installs the public migration chain into an isolated real PostgreSQL database and inspects the installed trigger function with `pg_get_functiondef(...)`, proving that the target restriction and diagnostic survived installation rather than existing only in source text.
4. ADR alignment: ADR 0059 now states that the completion capability owns only the transition into `reconciled`; future `review_required`, `not_reconciled`, or `superseded` commands must deliberately evolve the state-machine guard with their own evidence and authority model.

The repair does not invent the missing lifecycle commands, grant journal/reversal/period-close/tax authority, or weaken tenant RLS. It narrows the currently granted capability to the business transition it was created for.

## DDD / authority interpretation

`reconciliation_run` remains the aggregate whose lifecycle state changes atomically with command evidence. `reconciliation_completion_command` remains immutable command/audit evidence, not a second mutable aggregate. The PostgreSQL role is an infrastructure capability implementing one domain command; possession of that role is not equivalent to general authority over the aggregate's state machine.

This distinction keeps application/API naming, database privilege, trigger invariants, and the ADR's ubiquitous language aligned: **Reconciliation Completion** means evidence-backed transition to `reconciled`, not arbitrary reconciliation-status mutation.

## Research basis

Cai et al. (2025) motivates treating transaction/isolation behavior as an integrity property that should be explicitly verified rather than assumed. Logrippo (2025) formalizes integrity reasoning over RBAC role/permission assignments and state reconfiguration; the result is consistent with constraining the effective operation set of a role rather than inferring business authority from a coarse underlying privilege. PostgreSQL 18's role membership and trigger semantics are the implementation authority for this repository.

### APA 7th references

Cai, Z., Liu, S., Wei, H., Chen, Y., & Pan, A. (2025). Fast verification of strong database isolation (Extended Version). *Proceedings of the VLDB Endowment, 19*, 563–575. https://consensus.app/papers/fast-verification-of-strong-database-isolation-extended-cai-liu/e7131ce449515d41ab6104cc32c3e2b7/

Logrippo, L. (2025). Data flow security in role-based access control. *Journal of Information Security and Applications*. https://consensus.app/papers/data-flow-security-in-rolebased-access-control-logrippo/95874bd5d780530a8e80eece583cda0e/?utm_source=chatgpt

PostgreSQL Global Development Group. (2026). *PostgreSQL 18 documentation: Database roles and transaction isolation*. https://www.postgresql.org/docs/18/user-manag.html ; https://www.postgresql.org/docs/18/transaction-iso.html

## Verification boundary

No exact-head CI result is claimed by this note. Any head movement invalidates predecessor checks. Integration remains blocked behind PR #29 and later restack/revalidation; queued or skipped workflow evidence is non-passing. This doctoring record documents the causal defect and source-level repair so later exact-head review can verify the same invariant against the protected integrated base.
