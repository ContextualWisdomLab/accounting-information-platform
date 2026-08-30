BEGIN;

-- Reconciliation monetary facts must use the same numeric(38, 6) domain as
-- journal lines and bank-statement evidence. The original 0014 tables used a
-- narrower legacy domain, which could reject valid source amounts.
-- PostgreSQL does not permit changing a column type while an UPDATE OF trigger
-- names that column, so the existing registration is rebuilt below.
DROP TRIGGER reconciliation_candidate_capacity_guard
    ON accounting_core.reconciliation_candidate;

ALTER TABLE accounting_core.reconciliation_candidate
    ALTER COLUMN statement_amount TYPE numeric(38, 6),
    ALTER COLUMN journal_amount TYPE numeric(38, 6);

ALTER TABLE accounting_core.statement_match_allocation
    ALTER COLUMN allocated_amount TYPE numeric(38, 6);

ALTER TABLE accounting_core.journal_match_allocation
    ALTER COLUMN allocated_amount TYPE numeric(38, 6);

-- Keep aggregate variables unconstrained so an over-consumption comparison
-- remains an exact rejection instead of overflowing before the guard runs.
CREATE OR REPLACE FUNCTION accounting_core.reconciliation_match_approval_conservation_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    source_row record;
    source_capacity numeric;
    consumed_amount numeric;
    conservation_key text;
    current_legal_entity_id uuid;
    current_accounting_book_id uuid;
    current_bank_account_record_id uuid;
    current_currency_code text;
    statement_allocation_count bigint;
    journal_allocation_count bigint;
    statement_allocation_total numeric;
    journal_allocation_total numeric;
BEGIN
    IF NEW.match_status_code <> 'approved'
       OR (TG_OP = 'UPDATE' AND OLD.match_status_code = 'approved') THEN
        RETURN NEW;
    END IF;

    SELECT
        run_scope.legal_entity_id,
        run_scope.accounting_book_id,
        bank_assignment.bank_account_record_id,
        run_scope.currency_code
    INTO
        current_legal_entity_id,
        current_accounting_book_id,
        current_bank_account_record_id,
        current_currency_code
    FROM accounting_core.reconciliation_run AS run_scope
    JOIN accounting_core.bank_account_assignment AS bank_assignment
      ON bank_assignment.tenant_account_id = run_scope.tenant_account_id
     AND bank_assignment.bank_account_assignment_id = run_scope.bank_account_assignment_id
    WHERE run_scope.tenant_account_id = NEW.tenant_account_id
      AND run_scope.reconciliation_run_id = NEW.reconciliation_run_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'match is outside the tenant reconciliation run (reconciliation_scope_mismatch)'
            USING ERRCODE = '23514';
    END IF;

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
            'approved reconciliation match requires non-empty equal statement and journal allocation totals (reconciliation_match_unbalanced)'
            USING ERRCODE = '23514';
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
            current_legal_entity_id::text,
            current_accounting_book_id::text,
            current_bank_account_record_id::text,
            current_currency_code,
            source_row.statement_entry_reference
        );
        PERFORM pg_advisory_xact_lock(hashtextextended(conservation_key, 0));

        SELECT MAX(candidate.statement_amount)
        INTO source_capacity
        FROM accounting_core.reconciliation_candidate AS candidate
        JOIN accounting_core.reconciliation_run AS candidate_run
          ON candidate_run.tenant_account_id = candidate.tenant_account_id
         AND candidate_run.reconciliation_run_id = candidate.reconciliation_run_id
        JOIN accounting_core.bank_account_assignment AS candidate_assignment
          ON candidate_assignment.tenant_account_id = candidate_run.tenant_account_id
         AND candidate_assignment.bank_account_assignment_id = candidate_run.bank_account_assignment_id
        WHERE candidate.tenant_account_id = NEW.tenant_account_id
          AND candidate.statement_entry_reference = source_row.statement_entry_reference
          AND candidate_run.legal_entity_id = current_legal_entity_id
          AND candidate_run.accounting_book_id = current_accounting_book_id
          AND candidate_assignment.bank_account_record_id = current_bank_account_record_id
          AND candidate_run.currency_code = current_currency_code;

        SELECT COALESCE(SUM(allocation.allocated_amount), 0)
        INTO consumed_amount
        FROM accounting_core.statement_match_allocation AS allocation
        JOIN accounting_core.reconciliation_match AS approved_match
          ON approved_match.tenant_account_id = allocation.tenant_account_id
         AND approved_match.reconciliation_run_id = allocation.reconciliation_run_id
         AND approved_match.reconciliation_match_id = allocation.reconciliation_match_id
        JOIN accounting_core.reconciliation_run AS consuming_run
          ON consuming_run.tenant_account_id = allocation.tenant_account_id
         AND consuming_run.reconciliation_run_id = allocation.reconciliation_run_id
        JOIN accounting_core.bank_account_assignment AS consuming_assignment
          ON consuming_assignment.tenant_account_id = consuming_run.tenant_account_id
         AND consuming_assignment.bank_account_assignment_id = consuming_run.bank_account_assignment_id
        WHERE allocation.tenant_account_id = NEW.tenant_account_id
          AND allocation.statement_entry_reference = source_row.statement_entry_reference
          AND approved_match.match_status_code = 'approved'
          AND approved_match.reconciliation_match_id <> NEW.reconciliation_match_id
          AND consuming_run.legal_entity_id = current_legal_entity_id
          AND consuming_run.accounting_book_id = current_accounting_book_id
          AND consuming_assignment.bank_account_record_id = current_bank_account_record_id
          AND consuming_run.currency_code = current_currency_code;

        IF source_capacity IS NULL
           OR consumed_amount + source_row.allocation_amount > source_capacity THEN
            RAISE EXCEPTION
                'approving reconciliation match would over-consume statement source amount across active runs (reconciliation_allocation_overconsumed)'
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
            current_legal_entity_id::text,
            current_accounting_book_id::text,
            current_currency_code,
            source_row.journal_reference
        );
        PERFORM pg_advisory_xact_lock(hashtextextended(conservation_key, 0));

        SELECT MAX(candidate.journal_amount)
        INTO source_capacity
        FROM accounting_core.reconciliation_candidate AS candidate
        JOIN accounting_core.reconciliation_run AS candidate_run
          ON candidate_run.tenant_account_id = candidate.tenant_account_id
         AND candidate_run.reconciliation_run_id = candidate.reconciliation_run_id
        WHERE candidate.tenant_account_id = NEW.tenant_account_id
          AND candidate.journal_reference = source_row.journal_reference
          AND candidate_run.legal_entity_id = current_legal_entity_id
          AND candidate_run.accounting_book_id = current_accounting_book_id
          AND candidate_run.currency_code = current_currency_code;

        SELECT COALESCE(SUM(allocation.allocated_amount), 0)
        INTO consumed_amount
        FROM accounting_core.journal_match_allocation AS allocation
        JOIN accounting_core.reconciliation_match AS approved_match
          ON approved_match.tenant_account_id = allocation.tenant_account_id
         AND approved_match.reconciliation_run_id = allocation.reconciliation_run_id
         AND approved_match.reconciliation_match_id = allocation.reconciliation_match_id
        JOIN accounting_core.reconciliation_run AS consuming_run
          ON consuming_run.tenant_account_id = allocation.tenant_account_id
         AND consuming_run.reconciliation_run_id = allocation.reconciliation_run_id
        WHERE allocation.tenant_account_id = NEW.tenant_account_id
          AND allocation.journal_reference = source_row.journal_reference
          AND approved_match.match_status_code = 'approved'
          AND approved_match.reconciliation_match_id <> NEW.reconciliation_match_id
          AND consuming_run.legal_entity_id = current_legal_entity_id
          AND consuming_run.accounting_book_id = current_accounting_book_id
          AND consuming_run.currency_code = current_currency_code;

        IF source_capacity IS NULL
           OR consumed_amount + source_row.allocation_amount > source_capacity THEN
            RAISE EXCEPTION
                'approving reconciliation match would over-consume journal source amount across active runs (reconciliation_allocation_overconsumed)'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_approval_insert_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    current_status text;
    statement_allocation_count bigint;
    journal_allocation_count bigint;
    statement_allocation_total numeric;
    journal_allocation_total numeric;
BEGIN
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

CREATE OR REPLACE FUNCTION accounting_core.enforce_reconciliation_match_command_allocations()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    statement_allocation_count bigint;
    journal_allocation_count bigint;
    statement_allocation_total numeric;
    journal_allocation_total numeric;
    candidate_statement_amount numeric;
    candidate_journal_amount numeric;
BEGIN
    PERFORM 1
    FROM accounting_core.reconciliation_match AS match
    WHERE match.tenant_account_id = NEW.tenant_account_id
      AND match.reconciliation_run_id = NEW.reconciliation_run_id
      AND match.reconciliation_match_id = NEW.reconciliation_match_id
    FOR UPDATE;

    SELECT candidate.statement_amount, candidate.journal_amount
    INTO candidate_statement_amount, candidate_journal_amount
    FROM accounting_core.reconciliation_candidate AS candidate
    WHERE candidate.tenant_account_id = NEW.tenant_account_id
      AND candidate.reconciliation_run_id = NEW.reconciliation_run_id
      AND candidate.reconciliation_candidate_id = NEW.reconciliation_candidate_id;

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

    IF statement_allocation_count <> 1
       OR journal_allocation_count <> 1
       OR statement_allocation_total <> journal_allocation_total
       OR statement_allocation_total <> candidate_statement_amount
       OR journal_allocation_total <> candidate_journal_amount THEN
        RAISE EXCEPTION
            'reconciliation match command requires exactly one statement and one journal allocation matching candidate amounts (reconciliation_match_command_allocation)'
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

COMMIT;
