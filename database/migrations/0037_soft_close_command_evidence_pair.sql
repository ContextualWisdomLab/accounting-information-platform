BEGIN;

-- Migration 0010 made the three soft-close command-evidence fields internally
-- all-or-none, but it did not bind that evidence to the authoritative
-- accounting_book_period_control status. Migration 0009 can therefore leave a
-- legacy soft_closed control that was projected from fiscal_period before the
-- per-book command-evidence boundary existed. Do not fabricate evidence from a
-- later ledger state or silently reopen an authoritative close fact. Fail the
-- upgrade so remediation can be audited against the original close command.
--
-- accounting_book_period_control is FORCE RLS. Give only the migration role a
-- transaction-scoped permissive SELECT policy for the preflight, then remove it
-- before the durable trigger is installed. This does not grant runtime DML or
-- weaken tenant isolation after commit.
CREATE POLICY soft_close_evidence_pair_upgrade_visibility
    ON accounting_core.accounting_book_period_control
    FOR SELECT
    TO current_user
    USING (true);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM accounting_core.accounting_book_period_control AS period_control
        WHERE period_control.period_status_code = 'soft_closed'
          AND (
              period_control.soft_close_idempotency_key IS NULL
              OR period_control.soft_close_source_payload_hash IS NULL
              OR period_control.soft_close_source_journal_count IS NULL
          )
    ) THEN
        RAISE EXCEPTION
            'pre-0037 soft-closed book-period authority has no complete durable command evidence; perform audited remediation before migration 0037 (soft_close_command_evidence_pair_legacy_preflight)'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

DROP POLICY soft_close_evidence_pair_upgrade_visibility
    ON accounting_core.accounting_book_period_control;

-- The supported soft-close command intentionally performs the status transition
-- and evidence write as two statements in one transaction. A deferred trigger
-- must therefore inspect the final retained row at commit rather than trust the
-- NEW image captured by the first UPDATE. This preserves that atomic command
-- while rejecting any transaction that commits soft_closed without all three
-- immutable evidence fields.
CREATE OR REPLACE FUNCTION accounting_core.require_soft_close_command_evidence_pair()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    evidence_complete boolean;
BEGIN
    SELECT period_control.soft_close_idempotency_key IS NOT NULL
           AND period_control.soft_close_source_payload_hash IS NOT NULL
           AND period_control.soft_close_source_journal_count IS NOT NULL
      INTO evidence_complete
      FROM accounting_core.accounting_book_period_control AS period_control
     WHERE period_control.tenant_account_id = NEW.tenant_account_id
       AND period_control.accounting_book_id = NEW.accounting_book_id
       AND period_control.fiscal_period_id = NEW.fiscal_period_id
       AND period_control.period_status_code = 'soft_closed';

    IF NOT COALESCE(evidence_complete, FALSE) THEN
        RAISE EXCEPTION
            'soft-closed book-period authority must commit with durable command evidence (soft_close_command_evidence_pair_required)'
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NULL;
END;
$$;

REVOKE ALL ON FUNCTION accounting_core.require_soft_close_command_evidence_pair()
    FROM PUBLIC;

CREATE CONSTRAINT TRIGGER soft_close_command_evidence_pair_guard
    AFTER UPDATE OF period_status_code
    ON accounting_core.accounting_book_period_control
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    WHEN (
        OLD.period_status_code IS DISTINCT FROM 'soft_closed'
        AND NEW.period_status_code = 'soft_closed'
    )
    EXECUTE FUNCTION accounting_core.require_soft_close_command_evidence_pair();

COMMIT;
