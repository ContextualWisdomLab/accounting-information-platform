BEGIN;

-- Exception and retained review evidence carry both valid/business time and
-- system/recording time. The latter is database provenance, not caller input.
-- Migration 0020 already makes resolution-command recorded_at database-owned;
-- apply the same rule to the maker evidence and retained artifact rows whose
-- system time is later used for temporal admission. This migration does not
-- alter effective_at and grants no posting, period-close, or policy authority.
--
-- Migrations before 0024 allowed callers to supply recorded_at explicitly. The
-- database cannot later prove whether a pre-0024 value came from the column
-- default or from a caller. Preserve those rows and timestamps exactly, but tag
-- them as legacy_unverified instead of silently relabelling them as trusted or
-- forcing an impossible delete/rewrite of immutable audit evidence. PostgreSQL
-- owns both recorded_at and the authority marker for every row inserted after
-- this migration. Legacy rows remain queryable audit evidence but cannot back a
-- new maker-checker resolution command that depends on trusted system time.

ALTER TABLE accounting_core.reconciliation_exception
    ADD COLUMN recording_time_authority_code text NOT NULL DEFAULT 'legacy_unverified'
        CHECK (recording_time_authority_code IN ('legacy_unverified', 'database_clock'));
ALTER TABLE accounting_core.reconciliation_exception
    ALTER COLUMN recording_time_authority_code DROP DEFAULT;

ALTER TABLE accounting_core.reconciliation_evidence
    ADD COLUMN recording_time_authority_code text NOT NULL DEFAULT 'legacy_unverified'
        CHECK (recording_time_authority_code IN ('legacy_unverified', 'database_clock'));
ALTER TABLE accounting_core.reconciliation_evidence
    ALTER COLUMN recording_time_authority_code DROP DEFAULT;

CREATE OR REPLACE FUNCTION accounting_core.assign_reconciliation_control_recorded_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.recorded_at := clock_timestamp();
    NEW.recording_time_authority_code := 'database_clock';
    RETURN NEW;
END;
$$;

CREATE TRIGGER reconciliation_exception_recording_time_guard
    BEFORE INSERT ON accounting_core.reconciliation_exception
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.assign_reconciliation_control_recorded_at();

CREATE TRIGGER reconciliation_evidence_recording_time_guard
    BEFORE INSERT ON accounting_core.reconciliation_evidence
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.assign_reconciliation_control_recorded_at();

CREATE OR REPLACE FUNCTION accounting_core.reject_reconciliation_control_recording_time_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.recorded_at IS DISTINCT FROM OLD.recorded_at
       OR NEW.recording_time_authority_code IS DISTINCT FROM OLD.recording_time_authority_code THEN
        RAISE EXCEPTION
            'reconciliation control recording-time provenance is immutable (reconciliation_control_recording_time_immutable)'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER accounting_reconciliation_exception_recording_time_immutable_guard
    BEFORE UPDATE OF recorded_at, recording_time_authority_code
    ON accounting_core.reconciliation_exception
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.reject_reconciliation_control_recording_time_mutation();

CREATE TRIGGER accounting_reconciliation_evidence_recording_time_immutable_guard
    BEFORE UPDATE OF recorded_at, recording_time_authority_code
    ON accounting_core.reconciliation_evidence
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.reject_reconciliation_control_recording_time_mutation();

CREATE OR REPLACE FUNCTION accounting_core.require_reconciliation_exception_resolution_recording_time_authority()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    exception_recording_time_authority text;
    evidence_recording_time_authority text;
BEGIN
    SELECT exception.recording_time_authority_code,
           evidence.recording_time_authority_code
    INTO exception_recording_time_authority,
         evidence_recording_time_authority
    FROM accounting_core.reconciliation_exception AS exception
    JOIN accounting_core.reconciliation_evidence AS evidence
      ON evidence.reconciliation_evidence_id = NEW.reconciliation_evidence_id
     AND evidence.tenant_account_id = exception.tenant_account_id
     AND evidence.reconciliation_run_id = exception.reconciliation_run_id
     AND evidence.reconciliation_exception_id = exception.reconciliation_exception_id
    WHERE exception.tenant_account_id = NEW.tenant_account_id
      AND exception.reconciliation_run_id = NEW.reconciliation_run_id
      AND exception.reconciliation_exception_id = NEW.reconciliation_exception_id;

    IF exception_recording_time_authority IS DISTINCT FROM 'database_clock'
       OR evidence_recording_time_authority IS DISTINCT FROM 'database_clock' THEN
        RAISE EXCEPTION
            'exception resolution requires database-owned exception and review-evidence recording time (reconciliation_resolution_recording_time_authority_required)'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

-- PostgreSQL fires same-event triggers in name order. The existing hash guard
-- resolves NEW.reconciliation_evidence_id before this recording-time authority
-- guard runs, so this check binds the exact retained artifact selected by the
-- command rather than a caller-selected reference alone.
CREATE TRIGGER accounting_reconciliation_exception_resolution_recording_time_authority_guard
    BEFORE INSERT ON accounting_core.reconciliation_exception_resolution_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.require_reconciliation_exception_resolution_recording_time_authority();

COMMIT;
