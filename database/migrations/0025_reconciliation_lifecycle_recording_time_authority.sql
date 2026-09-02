BEGIN;

-- A reconciled lifecycle command is authority-bearing accounting-control evidence.
-- Its business-valid time cannot be later than the database recording time at
-- which the command becomes durable. PostgreSQL owns recorded_at; a caller may
-- not future-date that system timestamp to make a future-effective decision
-- appear current. This forward repair changes no journal, period-close, or
-- accounting-policy authority.
CREATE OR REPLACE FUNCTION accounting_core.assign_reconciliation_lifecycle_recorded_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.recorded_at := clock_timestamp();

    IF NEW.effective_at > NEW.recorded_at THEN
        RAISE EXCEPTION
            'reconciliation lifecycle effective time cannot be in the future relative to database recording time (reconciliation_lifecycle_future_time)'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER accounting_reconciliation_transition_recording_time_guard
    BEFORE INSERT ON accounting_core.reconciliation_run_transition_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.assign_reconciliation_lifecycle_recorded_at();

COMMIT;
