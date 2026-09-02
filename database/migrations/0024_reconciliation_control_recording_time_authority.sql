BEGIN;

-- Exception and retained review evidence carry both valid/business time and
-- system/recording time. The latter is database provenance, not caller input.
-- Migration 0020 already makes resolution-command recorded_at database-owned;
-- apply the same rule to the maker evidence and retained artifact rows whose
-- system time is later used for temporal admission. This migration does not
-- alter effective_at and grants no posting, period-close, or policy authority.
CREATE OR REPLACE FUNCTION accounting_core.assign_reconciliation_control_recorded_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.recorded_at := clock_timestamp();
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

COMMIT;
