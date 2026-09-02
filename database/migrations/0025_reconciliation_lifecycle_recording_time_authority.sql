BEGIN;

-- A reconciled lifecycle command is authority-bearing accounting-control evidence.
-- Its business-valid time cannot be later than the database recording time at
-- which the command becomes durable. PostgreSQL owns recorded_at for every new
-- transition after this migration; a caller may not future-date that system
-- timestamp to make a future-effective decision appear current.
--
-- Migration 0019 allowed callers to supply recorded_at explicitly. Existing
-- transition rows therefore remain immutable audit evidence, but their system
-- timestamp cannot be proven to have come from the database clock. Preserve
-- those rows and timestamps exactly and mark them legacy_unverified. Only rows
-- inserted after this migration receive database_clock authority. Downstream
-- close/replay boundaries must fail closed on legacy_unverified transition time.
-- This repair changes no journal, period-close, or accounting-policy authority.
ALTER TABLE accounting_core.reconciliation_run_transition_command
    ADD COLUMN recording_time_authority_code text NOT NULL DEFAULT 'legacy_unverified'
        CHECK (recording_time_authority_code IN ('legacy_unverified', 'database_clock'));
ALTER TABLE accounting_core.reconciliation_run_transition_command
    ALTER COLUMN recording_time_authority_code DROP DEFAULT;

CREATE OR REPLACE FUNCTION accounting_core.assign_reconciliation_lifecycle_recorded_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.recorded_at := clock_timestamp();
    NEW.recording_time_authority_code := 'database_clock';

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
