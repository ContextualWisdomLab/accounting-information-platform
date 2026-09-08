BEGIN;

-- Preserve the parent stack's 0020 migration identity without reinstalling its
-- superseded completion-command model. PR #43 replaces that pre-release model
-- with the stronger reconciliation_run_transition_command aggregate introduced
-- by 0019 and the database-owned snapshot authority applied by 0021.
--
-- This migration is intentionally a successor marker, not a second lifecycle
-- writer. Recreating reconciliation_run_completion_command or its status guard
-- here would make two incompatible commands authoritative for the same
-- evaluating/review_required -> reconciled transition.
DO $$
BEGIN
    IF to_regclass('accounting_core.reconciliation_run_transition_command') IS NULL THEN
        RAISE EXCEPTION
            'reconciliation lifecycle successor authority is missing before migration 0020 (reconciliation_lifecycle_successor_required)'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

COMMIT;
