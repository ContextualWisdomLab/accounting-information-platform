BEGIN;

-- Migration 0035 proves snapshot -> hard_closed at commit. The inverse also
-- matters: accounting_book_period_control is the authoritative close fact, so a
-- hard_closed transition must not commit without the retained trial-balance
-- evidence that the supported close command promises to preserve. Keep this
-- check deferred because the canonical command inserts the snapshot before it
-- advances the book-period control in the same transaction.
CREATE OR REPLACE FUNCTION accounting_reporting.require_hard_close_trial_balance_snapshot_pair()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM accounting_reporting.trial_balance_snapshot
        WHERE trial_balance_snapshot.tenant_account_id = NEW.tenant_account_id
          AND trial_balance_snapshot.accounting_book_id = NEW.accounting_book_id
          AND trial_balance_snapshot.fiscal_period_id = NEW.fiscal_period_id
    ) THEN
        RAISE EXCEPTION
            'hard-closed book-period authority must commit with retained trial balance evidence (hard_close_snapshot_pair_required)'
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NULL;
END;
$$;

REVOKE ALL ON FUNCTION accounting_reporting.require_hard_close_trial_balance_snapshot_pair()
    FROM PUBLIC;

CREATE CONSTRAINT TRIGGER hard_close_trial_balance_snapshot_pair_guard
    AFTER UPDATE OF period_status_code
    ON accounting_core.accounting_book_period_control
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    WHEN (
        OLD.period_status_code IS DISTINCT FROM 'hard_closed'
        AND NEW.period_status_code = 'hard_closed'
    )
    EXECUTE FUNCTION accounting_reporting.require_hard_close_trial_balance_snapshot_pair();

COMMIT;
