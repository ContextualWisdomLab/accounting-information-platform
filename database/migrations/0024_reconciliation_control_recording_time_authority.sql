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
-- default or from a caller. Do not silently relabel those historical values as
-- database-owned provenance. A populated pre-0024 reconciliation-control store
-- therefore requires an explicit audited remediation/migration decision before
-- this stronger authority boundary can be installed.
--
-- Both source tables use FORCE ROW LEVEL SECURITY. Give only the migration
-- current_user transaction-scoped all-tenant SELECT visibility for this upgrade
-- preflight, then remove it before installing the durable INSERT guards.
CREATE POLICY reconciliation_exception_recording_time_upgrade_visibility
    ON accounting_core.reconciliation_exception
    FOR SELECT
    TO current_user
    USING (true);

CREATE POLICY reconciliation_evidence_recording_time_upgrade_visibility
    ON accounting_core.reconciliation_evidence
    FOR SELECT
    TO current_user
    USING (true);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_exception
    ) OR EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_evidence
    ) THEN
        RAISE EXCEPTION
            'pre-0024 reconciliation exception/evidence rows have unverifiable system recording time; complete an audited remediation before installing database-owned recording time (reconciliation_recording_time_legacy_preflight)'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

DROP POLICY reconciliation_exception_recording_time_upgrade_visibility
    ON accounting_core.reconciliation_exception;
DROP POLICY reconciliation_evidence_recording_time_upgrade_visibility
    ON accounting_core.reconciliation_evidence;

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
