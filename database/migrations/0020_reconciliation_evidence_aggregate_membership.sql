BEGIN;

-- Reconciliation review evidence belongs to one reconciliation_run aggregate for
-- its entire lifetime. A privileged caller must not be able to evade a finalized
-- run's lifecycle freeze by rewriting tenant/run foreign keys so an existing row
-- appears to belong to another evaluating run. Corrections are append/supersede
-- operations in a new run, never cross-aggregate row reassignment.
--
-- The lifecycle triggers installed by migration 0019 already call this function
-- for candidate, match, statement/journal allocation, approval, and exception
-- INSERT/UPDATE/DELETE operations. Replacing the function strengthens those
-- existing triggers without changing their ordering or lock contract.
CREATE OR REPLACE FUNCTION accounting_core.guard_reconciled_run_evidence_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    lifecycle_tenant_account_id uuid;
    lifecycle_reconciliation_run_id uuid;
    current_status text;
    transition_exists boolean;
BEGIN
    IF TG_OP = 'UPDATE'
       AND (
           NEW.tenant_account_id IS DISTINCT FROM OLD.tenant_account_id
           OR NEW.reconciliation_run_id IS DISTINCT FROM OLD.reconciliation_run_id
       ) THEN
        RAISE EXCEPTION
            'reconciliation evidence aggregate membership is immutable; create evidence in the destination run instead (reconciliation_lifecycle_scope_immutable)'
            USING ERRCODE = '23514';
    END IF;

    IF TG_OP = 'DELETE' THEN
        lifecycle_tenant_account_id := OLD.tenant_account_id;
        lifecycle_reconciliation_run_id := OLD.reconciliation_run_id;
    ELSE
        lifecycle_tenant_account_id := NEW.tenant_account_id;
        lifecycle_reconciliation_run_id := NEW.reconciliation_run_id;
    END IF;

    PERFORM accounting_core.acquire_reconciliation_run_lifecycle_lock(
        lifecycle_tenant_account_id,
        lifecycle_reconciliation_run_id
    );

    SELECT run_status_code
    INTO current_status
    FROM accounting_core.reconciliation_run
    WHERE tenant_account_id = lifecycle_tenant_account_id
      AND reconciliation_run_id = lifecycle_reconciliation_run_id;

    SELECT EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_run_transition_command AS transition
        WHERE transition.tenant_account_id = lifecycle_tenant_account_id
          AND transition.reconciliation_run_id = lifecycle_reconciliation_run_id
          AND transition.target_run_status_code = 'reconciled'
    )
    INTO transition_exists;

    IF current_status = 'reconciled' OR transition_exists THEN
        RAISE EXCEPTION
            'reconciliation transition/reconciled run evidence is frozen; create a new reconciliation run instead (reconciliation_lifecycle_frozen)'
            USING ERRCODE = '23514';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

COMMIT;
