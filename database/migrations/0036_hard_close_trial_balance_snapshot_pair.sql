BEGIN;

-- A deferred trigger protects future transitions but cannot certify rows that
-- already existed before this migration. Both participating relations are FORCE
-- RLS, so give only the current migration role transaction-scoped SELECT
-- visibility for the preflight. The policies disappear before durable trigger
-- installation; an aborted migration rolls them back with the transaction.
CREATE POLICY hard_close_snapshot_pair_control_upgrade_visibility
    ON accounting_core.accounting_book_period_control
    FOR SELECT
    TO current_user
    USING (true);

CREATE POLICY hard_close_snapshot_pair_snapshot_upgrade_visibility
    ON accounting_reporting.trial_balance_snapshot
    FOR SELECT
    TO current_user
    USING (true);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM accounting_core.accounting_book_period_control AS period_control
        WHERE period_control.period_status_code = 'hard_closed'
          AND NOT EXISTS (
              SELECT 1
              FROM accounting_reporting.trial_balance_snapshot AS snapshot
              WHERE snapshot.tenant_account_id = period_control.tenant_account_id
                AND snapshot.accounting_book_id = period_control.accounting_book_id
                AND snapshot.fiscal_period_id = period_control.fiscal_period_id
          )
    ) THEN
        RAISE EXCEPTION
            'pre-0036 hard-closed book-period authority has no retained trial balance; perform audited remediation before migration 0036 (hard_close_snapshot_pair_legacy_preflight)'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM accounting_reporting.trial_balance_snapshot AS snapshot
        LEFT JOIN accounting_core.accounting_book_period_control AS period_control
          ON period_control.tenant_account_id = snapshot.tenant_account_id
         AND period_control.accounting_book_id = snapshot.accounting_book_id
         AND period_control.fiscal_period_id = snapshot.fiscal_period_id
        WHERE period_control.fiscal_period_id IS NULL
           OR period_control.period_status_code IS DISTINCT FROM 'hard_closed'
    ) THEN
        RAISE EXCEPTION
            'pre-0036 retained trial balance lacks matching hard-closed book-period authority; perform audited remediation before migration 0036 (trial_balance_snapshot_hard_close_pair_legacy_preflight)'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

DROP POLICY hard_close_snapshot_pair_snapshot_upgrade_visibility
    ON accounting_reporting.trial_balance_snapshot;
DROP POLICY hard_close_snapshot_pair_control_upgrade_visibility
    ON accounting_core.accounting_book_period_control;

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
