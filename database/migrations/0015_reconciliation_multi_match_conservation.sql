BEGIN;

-- Replace migration 0014's run-wide single-approved-match shortcut with
-- source-level allocation conservation. A reconciliation run can approve
-- multiple independent matches, including split/aggregate plans, while exact
-- statement and journal source amounts remain impossible to over-consume.
-- This migration records reconciliation evidence only; it grants no journal
-- posting, reversal, close, or accounting-policy authority.

DROP INDEX accounting_core.reconciliation_match_approved_single;

ALTER TABLE accounting_core.reconciliation_candidate
    ADD CONSTRAINT reconciliation_candidate_scope_identity
    UNIQUE (
        tenant_account_id,
        reconciliation_run_id,
        reconciliation_candidate_id
    );

ALTER TABLE accounting_core.reconciliation_match
    ADD CONSTRAINT reconciliation_match_scope_identity
    UNIQUE (
        tenant_account_id,
        reconciliation_run_id,
        reconciliation_match_id
    );

ALTER TABLE accounting_core.reconciliation_match
    DROP CONSTRAINT reconciliation_match_reconciliation_candidate_id_fkey,
    ADD CONSTRAINT reconciliation_candidate_scope_foreign_key
        FOREIGN KEY (
            tenant_account_id,
            reconciliation_run_id,
            reconciliation_candidate_id
        )
        REFERENCES accounting_core.reconciliation_candidate (
            tenant_account_id,
            reconciliation_run_id,
            reconciliation_candidate_id
        );

ALTER TABLE accounting_core.statement_match_allocation
    DROP CONSTRAINT statement_match_allocation_reconciliation_match_id_fkey,
    ADD CONSTRAINT reconciliation_match_scope_foreign_key
        FOREIGN KEY (
            tenant_account_id,
            reconciliation_run_id,
            reconciliation_match_id
        )
        REFERENCES accounting_core.reconciliation_match (
            tenant_account_id,
            reconciliation_run_id,
            reconciliation_match_id
        );

ALTER TABLE accounting_core.journal_match_allocation
    DROP CONSTRAINT journal_match_allocation_reconciliation_match_id_fkey,
    ADD CONSTRAINT reconciliation_match_scope_foreign_key
        FOREIGN KEY (
            tenant_account_id,
            reconciliation_run_id,
            reconciliation_match_id
        )
        REFERENCES accounting_core.reconciliation_match (
            tenant_account_id,
            reconciliation_run_id,
            reconciliation_match_id
        );

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_candidate_capacity_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_candidate AS candidate
        WHERE candidate.tenant_account_id = NEW.tenant_account_id
          AND candidate.reconciliation_run_id = NEW.reconciliation_run_id
          AND candidate.statement_entry_reference = NEW.statement_entry_reference
          AND candidate.reconciliation_candidate_id <> NEW.reconciliation_candidate_id
          AND candidate.statement_amount <> NEW.statement_amount
    ) THEN
        RAISE EXCEPTION
            'statement source amount differs across reconciliation candidates (reconciliation_source_amount_conflict)'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_candidate AS candidate
        WHERE candidate.tenant_account_id = NEW.tenant_account_id
          AND candidate.reconciliation_run_id = NEW.reconciliation_run_id
          AND candidate.journal_reference = NEW.journal_reference
          AND candidate.reconciliation_candidate_id <> NEW.reconciliation_candidate_id
          AND candidate.journal_amount <> NEW.journal_amount
    ) THEN
        RAISE EXCEPTION
            'journal source amount differs across reconciliation candidates (reconciliation_source_amount_conflict)'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER reconciliation_candidate_capacity_guard
BEFORE INSERT OR UPDATE OF
    tenant_account_id,
    reconciliation_run_id,
    statement_entry_reference,
    journal_reference,
    statement_amount,
    journal_amount
ON accounting_core.reconciliation_candidate
FOR EACH ROW EXECUTE FUNCTION accounting_core.reconciliation_candidate_capacity_guard();

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_allocation_conservation_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    candidate_statement_reference text;
    candidate_journal_reference text;
    candidate_statement_amount numeric(30, 6);
    candidate_journal_amount numeric(30, 6);
    current_match_status text;
    consumed_amount numeric(30, 6);
    conservation_key text;
BEGIN
    SELECT
        candidate.statement_entry_reference,
        candidate.journal_reference,
        candidate.statement_amount,
        candidate.journal_amount,
        match.match_status_code
    INTO
        candidate_statement_reference,
        candidate_journal_reference,
        candidate_statement_amount,
        candidate_journal_amount,
        current_match_status
    FROM accounting_core.reconciliation_match AS match
    JOIN accounting_core.reconciliation_candidate AS candidate
      ON candidate.tenant_account_id = match.tenant_account_id
     AND candidate.reconciliation_run_id = match.reconciliation_run_id
     AND candidate.reconciliation_candidate_id = match.reconciliation_candidate_id
    WHERE match.tenant_account_id = NEW.tenant_account_id
      AND match.reconciliation_run_id = NEW.reconciliation_run_id
      AND match.reconciliation_match_id = NEW.reconciliation_match_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'allocation match is outside the tenant reconciliation run (reconciliation_scope_mismatch)'
            USING ERRCODE = '23514';
    END IF;

    IF TG_TABLE_NAME = 'statement_match_allocation' THEN
        IF NEW.statement_entry_reference <> candidate_statement_reference THEN
            RAISE EXCEPTION
                'statement allocation does not identify the matched candidate source (reconciliation_scope_mismatch)'
                USING ERRCODE = '23514';
        END IF;

        IF current_match_status = 'approved' THEN
            conservation_key := concat_ws(
                ':',
                'reconciliation-statement',
                NEW.tenant_account_id::text,
                NEW.reconciliation_run_id::text,
                NEW.statement_entry_reference
            );
            PERFORM pg_advisory_xact_lock(hashtextextended(conservation_key, 0));

            SELECT COALESCE(SUM(allocation.allocated_amount), 0)
            INTO consumed_amount
            FROM accounting_core.statement_match_allocation AS allocation
            JOIN accounting_core.reconciliation_match AS approved_match
              ON approved_match.tenant_account_id = allocation.tenant_account_id
             AND approved_match.reconciliation_run_id = allocation.reconciliation_run_id
             AND approved_match.reconciliation_match_id = allocation.reconciliation_match_id
            WHERE allocation.tenant_account_id = NEW.tenant_account_id
              AND allocation.reconciliation_run_id = NEW.reconciliation_run_id
              AND allocation.statement_entry_reference = NEW.statement_entry_reference
              AND approved_match.match_status_code = 'approved';

            IF consumed_amount + NEW.allocated_amount > candidate_statement_amount THEN
                RAISE EXCEPTION
                    'approved reconciliation allocations exceed statement source amount (reconciliation_allocation_overconsumed)'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
    ELSE
        IF NEW.journal_reference <> candidate_journal_reference THEN
            RAISE EXCEPTION
                'journal allocation does not identify the matched candidate source (reconciliation_scope_mismatch)'
                USING ERRCODE = '23514';
        END IF;

        IF current_match_status = 'approved' THEN
            conservation_key := concat_ws(
                ':',
                'reconciliation-journal',
                NEW.tenant_account_id::text,
                NEW.reconciliation_run_id::text,
                NEW.journal_reference
            );
            PERFORM pg_advisory_xact_lock(hashtextextended(conservation_key, 0));

            SELECT COALESCE(SUM(allocation.allocated_amount), 0)
            INTO consumed_amount
            FROM accounting_core.journal_match_allocation AS allocation
            JOIN accounting_core.reconciliation_match AS approved_match
              ON approved_match.tenant_account_id = allocation.tenant_account_id
             AND approved_match.reconciliation_run_id = allocation.reconciliation_run_id
             AND approved_match.reconciliation_match_id = allocation.reconciliation_match_id
            WHERE allocation.tenant_account_id = NEW.tenant_account_id
              AND allocation.reconciliation_run_id = NEW.reconciliation_run_id
              AND allocation.journal_reference = NEW.journal_reference
              AND approved_match.match_status_code = 'approved';

            IF consumed_amount + NEW.allocated_amount > candidate_journal_amount THEN
                RAISE EXCEPTION
                    'approved reconciliation allocations exceed journal source amount (reconciliation_allocation_overconsumed)'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER statement_allocation_conservation_guard
BEFORE INSERT
ON accounting_core.statement_match_allocation
FOR EACH ROW EXECUTE FUNCTION accounting_core.reconciliation_allocation_conservation_guard();

CREATE TRIGGER journal_allocation_conservation_guard
BEFORE INSERT
ON accounting_core.journal_match_allocation
FOR EACH ROW EXECUTE FUNCTION accounting_core.reconciliation_allocation_conservation_guard();

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_approved_allocation_immutability_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    current_match_status text;
BEGIN
    SELECT match.match_status_code
    INTO current_match_status
    FROM accounting_core.reconciliation_match AS match
    WHERE match.tenant_account_id = OLD.tenant_account_id
      AND match.reconciliation_run_id = OLD.reconciliation_run_id
      AND match.reconciliation_match_id = OLD.reconciliation_match_id;

    IF current_match_status = 'approved' THEN
        RAISE EXCEPTION
            'approved reconciliation allocations are immutable; supersede the match instead (reconciliation_allocation_immutable)'
            USING ERRCODE = '23514';
    END IF;

    RETURN OLD;
END;
$$;

CREATE TRIGGER statement_allocation_immutability_guard
BEFORE UPDATE OR DELETE
ON accounting_core.statement_match_allocation
FOR EACH ROW EXECUTE FUNCTION accounting_core.reconciliation_approved_allocation_immutability_guard();

CREATE TRIGGER journal_allocation_immutability_guard
BEFORE UPDATE OR DELETE
ON accounting_core.journal_match_allocation
FOR EACH ROW EXECUTE FUNCTION accounting_core.reconciliation_approved_allocation_immutability_guard();

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_match_approval_conservation_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    source_row record;
    source_capacity numeric(30, 6);
    consumed_amount numeric(30, 6);
    conservation_key text;
BEGIN
    IF NEW.match_status_code <> 'approved'
       OR (TG_OP = 'UPDATE' AND OLD.match_status_code = 'approved') THEN
        RETURN NEW;
    END IF;

    FOR source_row IN
        SELECT allocation.statement_entry_reference,
               SUM(allocation.allocated_amount) AS allocation_amount
        FROM accounting_core.statement_match_allocation AS allocation
        WHERE allocation.tenant_account_id = NEW.tenant_account_id
          AND allocation.reconciliation_run_id = NEW.reconciliation_run_id
          AND allocation.reconciliation_match_id = NEW.reconciliation_match_id
        GROUP BY allocation.statement_entry_reference
    LOOP
        conservation_key := concat_ws(
            ':',
            'reconciliation-statement',
            NEW.tenant_account_id::text,
            NEW.reconciliation_run_id::text,
            source_row.statement_entry_reference
        );
        PERFORM pg_advisory_xact_lock(hashtextextended(conservation_key, 0));

        SELECT MAX(candidate.statement_amount)
        INTO source_capacity
        FROM accounting_core.reconciliation_candidate AS candidate
        WHERE candidate.tenant_account_id = NEW.tenant_account_id
          AND candidate.reconciliation_run_id = NEW.reconciliation_run_id
          AND candidate.statement_entry_reference = source_row.statement_entry_reference;

        SELECT COALESCE(SUM(allocation.allocated_amount), 0)
        INTO consumed_amount
        FROM accounting_core.statement_match_allocation AS allocation
        JOIN accounting_core.reconciliation_match AS approved_match
          ON approved_match.tenant_account_id = allocation.tenant_account_id
         AND approved_match.reconciliation_run_id = allocation.reconciliation_run_id
         AND approved_match.reconciliation_match_id = allocation.reconciliation_match_id
        WHERE allocation.tenant_account_id = NEW.tenant_account_id
          AND allocation.reconciliation_run_id = NEW.reconciliation_run_id
          AND allocation.statement_entry_reference = source_row.statement_entry_reference
          AND approved_match.match_status_code = 'approved'
          AND approved_match.reconciliation_match_id <> NEW.reconciliation_match_id;

        IF source_capacity IS NULL
           OR consumed_amount + source_row.allocation_amount > source_capacity THEN
            RAISE EXCEPTION
                'approving reconciliation match would over-consume statement source amount (reconciliation_allocation_overconsumed)'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;

    FOR source_row IN
        SELECT allocation.journal_reference,
               SUM(allocation.allocated_amount) AS allocation_amount
        FROM accounting_core.journal_match_allocation AS allocation
        WHERE allocation.tenant_account_id = NEW.tenant_account_id
          AND allocation.reconciliation_run_id = NEW.reconciliation_run_id
          AND allocation.reconciliation_match_id = NEW.reconciliation_match_id
        GROUP BY allocation.journal_reference
    LOOP
        conservation_key := concat_ws(
            ':',
            'reconciliation-journal',
            NEW.tenant_account_id::text,
            NEW.reconciliation_run_id::text,
            source_row.journal_reference
        );
        PERFORM pg_advisory_xact_lock(hashtextextended(conservation_key, 0));

        SELECT MAX(candidate.journal_amount)
        INTO source_capacity
        FROM accounting_core.reconciliation_candidate AS candidate
        WHERE candidate.tenant_account_id = NEW.tenant_account_id
          AND candidate.reconciliation_run_id = NEW.reconciliation_run_id
          AND candidate.journal_reference = source_row.journal_reference;

        SELECT COALESCE(SUM(allocation.allocated_amount), 0)
        INTO consumed_amount
        FROM accounting_core.journal_match_allocation AS allocation
        JOIN accounting_core.reconciliation_match AS approved_match
          ON approved_match.tenant_account_id = allocation.tenant_account_id
         AND approved_match.reconciliation_run_id = allocation.reconciliation_run_id
         AND approved_match.reconciliation_match_id = allocation.reconciliation_match_id
        WHERE allocation.tenant_account_id = NEW.tenant_account_id
          AND allocation.reconciliation_run_id = NEW.reconciliation_run_id
          AND allocation.journal_reference = source_row.journal_reference
          AND approved_match.match_status_code = 'approved'
          AND approved_match.reconciliation_match_id <> NEW.reconciliation_match_id;

        IF source_capacity IS NULL
           OR consumed_amount + source_row.allocation_amount > source_capacity THEN
            RAISE EXCEPTION
                'approving reconciliation match would over-consume journal source amount (reconciliation_allocation_overconsumed)'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;

    RETURN NEW;
END;
$$;

CREATE TRIGGER reconciliation_match_approval_guard
BEFORE INSERT OR UPDATE OF match_status_code
ON accounting_core.reconciliation_match
FOR EACH ROW EXECUTE FUNCTION accounting_core.reconciliation_match_approval_conservation_guard();

CREATE POLICY reconciliation_candidate_isolation
ON accounting_core.reconciliation_candidate
USING (tenant_account_id = accounting_core.current_tenant_account_id())
WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

CREATE POLICY reconciliation_match_isolation
ON accounting_core.reconciliation_match
USING (tenant_account_id = accounting_core.current_tenant_account_id())
WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

CREATE POLICY statement_allocation_isolation
ON accounting_core.statement_match_allocation
USING (tenant_account_id = accounting_core.current_tenant_account_id())
WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

CREATE POLICY journal_allocation_isolation
ON accounting_core.journal_match_allocation
USING (tenant_account_id = accounting_core.current_tenant_account_id())
WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

COMMIT;
