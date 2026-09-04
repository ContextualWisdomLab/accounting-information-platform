BEGIN;

CREATE OR REPLACE FUNCTION accounting_reporting.reject_trial_balance_snapshot_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'hard-close trial balance evidence is immutable (trial_balance_snapshot_immutable)'
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE TRIGGER trial_balance_snapshot_immutable_guard
    BEFORE UPDATE OR DELETE
    ON accounting_reporting.trial_balance_snapshot
    FOR EACH ROW
    EXECUTE FUNCTION accounting_reporting.reject_trial_balance_snapshot_mutation();

CREATE OR REPLACE FUNCTION accounting_reporting.reject_trial_balance_line_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'hard-close trial balance evidence is immutable (trial_balance_snapshot_immutable)'
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE TRIGGER trial_balance_line_immutable_guard
    BEFORE UPDATE OR DELETE
    ON accounting_reporting.trial_balance_line
    FOR EACH ROW
    EXECUTE FUNCTION accounting_reporting.reject_trial_balance_line_mutation();

CREATE OR REPLACE FUNCTION accounting_reporting.guard_trial_balance_snapshot_insert()
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
       AND accounting_book_period_control.fiscal_period_id = NEW.fiscal_period_id
     FOR UPDATE;

    IF period_status_value IS NULL THEN
        RAISE EXCEPTION
            'trial balance snapshot has no matching book-period authority (trial_balance_snapshot_scope_missing)'
            USING ERRCODE = 'check_violation';
    END IF;

    IF period_status_value = 'hard_closed' THEN
        RAISE EXCEPTION
            'hard-close trial balance evidence is immutable (trial_balance_snapshot_immutable)'
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION accounting_reporting.guard_trial_balance_snapshot_insert()
    FROM PUBLIC;

CREATE TRIGGER trial_balance_snapshot_population_guard
    BEFORE INSERT
    ON accounting_reporting.trial_balance_snapshot
    FOR EACH ROW
    EXECUTE FUNCTION accounting_reporting.guard_trial_balance_snapshot_insert();

CREATE OR REPLACE FUNCTION accounting_reporting.guard_trial_balance_line_insert()
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
      FROM accounting_reporting.trial_balance_snapshot
      JOIN accounting_core.accounting_book_period_control
        ON accounting_book_period_control.tenant_account_id
           = trial_balance_snapshot.tenant_account_id
       AND accounting_book_period_control.accounting_book_id
           = trial_balance_snapshot.accounting_book_id
       AND accounting_book_period_control.fiscal_period_id
           = trial_balance_snapshot.fiscal_period_id
     WHERE trial_balance_snapshot.tenant_account_id = NEW.tenant_account_id
       AND trial_balance_snapshot.trial_balance_snapshot_id
           = NEW.trial_balance_snapshot_id
     FOR UPDATE OF accounting_book_period_control;

    IF period_status_value IS NULL THEN
        RAISE EXCEPTION
            'trial balance snapshot has no matching book-period authority (trial_balance_snapshot_scope_missing)'
            USING ERRCODE = 'check_violation';
    END IF;

    IF period_status_value = 'hard_closed' THEN
        RAISE EXCEPTION
            'hard-close trial balance evidence is immutable (trial_balance_snapshot_immutable)'
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION accounting_reporting.guard_trial_balance_line_insert()
    FROM PUBLIC;

CREATE TRIGGER trial_balance_line_population_guard
    BEFORE INSERT
    ON accounting_reporting.trial_balance_line
    FOR EACH ROW
    EXECUTE FUNCTION accounting_reporting.guard_trial_balance_line_insert();

COMMIT;
