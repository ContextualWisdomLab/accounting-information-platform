-- Repair tenant-isolation policies for databases that already ran migration 0014.
-- Migration 0014 installs these policies for new databases; this forward
-- migration is idempotent so an upgrade never removes an existing policy.

BEGIN;

DO $reconciliation_policy_repair$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_policies
        WHERE schemaname = 'accounting_core'
          AND tablename = 'reconciliation_candidate'
          AND policyname = 'reconciliation_candidate_isolation'
    ) THEN
        CREATE POLICY reconciliation_candidate_isolation
        ON accounting_core.reconciliation_candidate
        USING (tenant_account_id = accounting_core.current_tenant_account_id())
        WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_policies
        WHERE schemaname = 'accounting_core'
          AND tablename = 'reconciliation_match'
          AND policyname = 'reconciliation_match_isolation'
    ) THEN
        CREATE POLICY reconciliation_match_isolation
        ON accounting_core.reconciliation_match
        USING (tenant_account_id = accounting_core.current_tenant_account_id())
        WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_policies
        WHERE schemaname = 'accounting_core'
          AND tablename = 'statement_match_allocation'
          AND policyname = 'statement_match_allocation_isolation'
    ) THEN
        CREATE POLICY statement_match_allocation_isolation
        ON accounting_core.statement_match_allocation
        USING (tenant_account_id = accounting_core.current_tenant_account_id())
        WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_policies
        WHERE schemaname = 'accounting_core'
          AND tablename = 'journal_match_allocation'
          AND policyname = 'journal_match_allocation_isolation'
    ) THEN
        CREATE POLICY journal_match_allocation_isolation
        ON accounting_core.journal_match_allocation
        USING (tenant_account_id = accounting_core.current_tenant_account_id())
        WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
    END IF;
END;
$reconciliation_policy_repair$;

COMMIT;
