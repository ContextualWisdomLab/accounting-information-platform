BEGIN;

-- A retained trial-balance snapshot is created while the authoritative
-- book-period control is still soft_closed, then the canonical hard-close
-- command advances that same control to hard_closed before commit. The
-- immediate admission trigger therefore cannot prove the final pairing.
-- Enforce the invariant at transaction end so a purpose-limited closing
-- session cannot retain snapshot evidence while leaving book-period authority
-- soft-closed. This is a database consistency control, not an IFRS rule.
CREATE OR REPLACE FUNCTION accounting_reporting.require_trial_balance_snapshot_hard_close_pair()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    period_status_value text;
BEGIN
    SELECT accounting_book_period_control.period_status_code
      INTO period_status_value
      FROM accounting_core.accounting_book_period_control
     WHERE accounting_book_period_control.tenant_account_id = NEW.tenant_account_id
       AND accounting_book_period_control.accounting_book_id = NEW.accounting_book_id
       AND accounting_book_period_control.fiscal_period_id = NEW.fiscal_period_id;

    IF period_status_value IS DISTINCT FROM 'hard_closed' THEN
        RAISE EXCEPTION
            'retained trial balance snapshot must commit with hard-closed book-period authority (trial_balance_snapshot_hard_close_pair_required)'
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NULL;
END;
$$;

REVOKE ALL ON FUNCTION accounting_reporting.require_trial_balance_snapshot_hard_close_pair()
    FROM PUBLIC;

CREATE CONSTRAINT TRIGGER trial_balance_snapshot_hard_close_pair_guard
    AFTER INSERT
    ON accounting_reporting.trial_balance_snapshot
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION accounting_reporting.require_trial_balance_snapshot_hard_close_pair();

COMMIT;
