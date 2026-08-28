BEGIN;

-- Reconciliation approval INSERT must use the same parent-row -> snapshot-
-- advisory lock order as allocation INSERT and terminal match UPDATE. The
-- predecessor approval trigger took the advisory lock first and then acquired
-- the parent row implicitly through its foreign key, allowing a cycle with an
-- allocation transaction that already owned the parent row and was waiting for
-- the advisory lock.
CREATE OR REPLACE FUNCTION accounting_core.reconciliation_approval_insert_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    current_status text;
    statement_allocation_count bigint;
    journal_allocation_count bigint;
    statement_allocation_total numeric(30, 6);
    journal_allocation_total numeric(30, 6);
BEGIN
    -- Establish the canonical reconciliation lock order before taking the
    -- snapshot lock. The FK later requests a compatible lock from this same
    -- transaction, so it cannot invert the allocation path's row -> advisory
    -- ordering.
    SELECT match.match_status_code
    INTO current_status
    FROM accounting_core.reconciliation_match AS match
    WHERE match.tenant_account_id = NEW.tenant_account_id
      AND match.reconciliation_run_id = NEW.reconciliation_run_id
      AND match.reconciliation_match_id = NEW.reconciliation_match_id
    FOR UPDATE OF match;

    IF NOT FOUND OR current_status <> 'proposed' THEN
        RAISE EXCEPTION
            'reconciliation approval evidence requires a proposed match in the same tenant/run scope (reconciliation_approval_scope_mismatch)'
            USING ERRCODE = '23514';
    END IF;

    PERFORM accounting_core.reconciliation_match_snapshot_lock(
        NEW.tenant_account_id,
        NEW.reconciliation_run_id,
        NEW.reconciliation_match_id
    );

    IF NEW.approval_decision_code = 'approved' THEN
        SELECT COUNT(*), COALESCE(SUM(allocation.allocated_amount), 0)
        INTO statement_allocation_count, statement_allocation_total
        FROM accounting_core.statement_match_allocation AS allocation
        WHERE allocation.tenant_account_id = NEW.tenant_account_id
          AND allocation.reconciliation_run_id = NEW.reconciliation_run_id
          AND allocation.reconciliation_match_id = NEW.reconciliation_match_id;

        SELECT COUNT(*), COALESCE(SUM(allocation.allocated_amount), 0)
        INTO journal_allocation_count, journal_allocation_total
        FROM accounting_core.journal_match_allocation AS allocation
        WHERE allocation.tenant_account_id = NEW.tenant_account_id
          AND allocation.reconciliation_run_id = NEW.reconciliation_run_id
          AND allocation.reconciliation_match_id = NEW.reconciliation_match_id;

        IF statement_allocation_count = 0
           OR journal_allocation_count = 0
           OR statement_allocation_total <> journal_allocation_total THEN
            RAISE EXCEPTION
                'approved reconciliation evidence requires non-empty equal statement and journal allocation totals; add or correct allocations before recording approval evidence (reconciliation_match_unbalanced)'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    NEW.reconciliation_snapshot_version := 1;
    NEW.reconciliation_snapshot_hash :=
        accounting_core.reconciliation_match_snapshot_hash(
            NEW.tenant_account_id,
            NEW.reconciliation_run_id,
            NEW.reconciliation_match_id
        );
    IF NEW.reconciliation_snapshot_hash IS NULL THEN
        RAISE EXCEPTION
            'reconciliation approval evidence has no reviewable candidate snapshot (reconciliation_snapshot_missing)'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

COMMIT;
