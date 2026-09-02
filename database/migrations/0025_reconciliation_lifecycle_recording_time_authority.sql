BEGIN;

-- A reconciled lifecycle command is authority-bearing accounting-control evidence.
-- Its business-valid time cannot be later than the database recording time at
-- which the command becomes durable. PostgreSQL owns recorded_at for every new
-- transition after this migration; a caller may not future-date that system
-- timestamp to make a future-effective decision appear current.
--
-- Migration 0019 allowed callers to supply recorded_at explicitly. Existing
-- lifecycle transition rows are immutable, and the database cannot prove after
-- the fact whether their recorded_at value came from the column default or from
-- a caller. Unlike ordinary historical observations, those rows already make a
-- run `reconciled` and can feed close evidence. Silently relabelling them would
-- therefore promote unverifiable chronology into financial-control authority.
-- Fail the upgrade instead. An operator must use an explicit audited remediation
-- migration backed by the original transition/outbox evidence, or retain the old
-- release until that provenance is resolved. Never manufacture database-clock
-- provenance for a pre-0025 transition.
--
-- The transition table is FORCE RLS. Give only the current migration role
-- transaction-scoped all-tenant SELECT visibility for this preflight and remove
-- it before installing durable authority changes. A failed migration rolls the
-- temporary policy back with the transaction.
CREATE POLICY reconciliation_lifecycle_recording_time_upgrade_visibility
    ON accounting_core.reconciliation_run_transition_command
    FOR SELECT
    TO current_user
    USING (true);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_run_transition_command
    ) THEN
        RAISE EXCEPTION
            'pre-0025 reconciliation lifecycle transitions have unverifiable recording-time authority; perform audited remediation before migration 0025 (reconciliation_lifecycle_legacy_recording_time_preflight)'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

DROP POLICY reconciliation_lifecycle_recording_time_upgrade_visibility
    ON accounting_core.reconciliation_run_transition_command;

-- The authority marker makes the valid/system-time distinction explicit in the
-- relational model and prevents later code from inferring trust merely from the
-- presence of a timestamp. The preflight above means no legacy row is silently
-- admitted into an installation that has completed 0025.
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
