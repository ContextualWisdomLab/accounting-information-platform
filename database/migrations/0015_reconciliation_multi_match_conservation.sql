BEGIN;

-- Replace migration 0014's run-wide single-approved-match shortcut with
-- source-level allocation conservation. A reconciliation run can approve
-- multiple independent matches, including split/aggregate plans, while exact
-- statement and journal source amounts remain impossible to over-consume.
-- Conservation follows immutable source identity across reconciliation runs;
-- explicitly rejected or superseded matches release their allocations because
-- only approved matches consume capacity. This migration records reconciliation
-- evidence only; it grants no journal posting, reversal, close, or
-- accounting-policy authority.

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

CREATE INDEX statement_allocation_source_index
    ON accounting_core.statement_match_allocation (
        tenant_account_id,
        statement_entry_reference,
        reconciliation_run_id,
        reconciliation_match_id
    );

CREATE INDEX journal_allocation_source_index
    ON accounting_core.journal_match_allocation (
        tenant_account_id,
        journal_reference,
        reconciliation_run_id,
        reconciliation_match_id
    );

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_candidate AS candidate
        JOIN accounting_core.reconciliation_run AS run_scope
          ON run_scope.tenant_account_id = candidate.tenant_account_id
         AND run_scope.reconciliation_run_id = candidate.reconciliation_run_id
        GROUP BY
            candidate.tenant_account_id,
            run_scope.legal_entity_id,
            run_scope.accounting_book_id,
            run_scope.bank_account_assignment_id,
            run_scope.currency_code,
            candidate.statement_entry_reference
        HAVING MIN(candidate.statement_amount) <> MAX(candidate.statement_amount)
    ) THEN
        RAISE EXCEPTION
            'statement source amount differs across reconciliation runs (reconciliation_source_amount_conflict)'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_candidate AS candidate
        JOIN accounting_core.reconciliation_run AS run_scope
          ON run_scope.tenant_account_id = candidate.tenant_account_id
         AND run_scope.reconciliation_run_id = candidate.reconciliation_run_id
        GROUP BY
            candidate.tenant_account_id,
            run_scope.legal_entity_id,
            run_scope.accounting_book_id,
            run_scope.currency_code,
            candidate.journal_reference
        HAVING MIN(candidate.journal_amount) <> MAX(candidate.journal_amount)
    ) THEN
        RAISE EXCEPTION
            'journal source amount differs across reconciliation runs (reconciliation_source_amount_conflict)'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_candidate_capacity_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    current_legal_entity_id uuid;
    current_accounting_book_id uuid;
    current_bank_account_assignment_id uuid;
    current_currency_code text;
BEGIN
    SELECT
        run_scope.legal_entity_id,
        run_scope.accounting_book_id,
        run_scope.bank_account_assignment_id,
        run_scope.currency_code
    INTO
        current_legal_entity_id,
        current_accounting_book_id,
        current_bank_account_assignment_id,
        current_currency_code
    FROM accounting_core.reconciliation_run AS run_scope
    WHERE run_scope.tenant_account_id = NEW.tenant_account_id
      AND run_scope.reconciliation_run_id = NEW.reconciliation_run_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'candidate is outside the tenant reconciliation run (reconciliation_scope_mismatch)'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_candidate AS candidate
        JOIN accounting_core.reconciliation_run AS candidate_run
          ON candidate_run.tenant_account_id = candidate.tenant_account_id
         AND candidate_run.reconciliation_run_id = candidate.reconciliation_run_id
        WHERE candidate.tenant_account_id = NEW.tenant_account_id
          AND candidate.statement_entry_reference = NEW.statement_entry_reference
          AND candidate.reconciliation_candidate_id <> NEW.reconciliation_candidate_id
          AND candidate_run.legal_entity_id = current_legal_entity_id
          AND candidate_run.accounting_book_id = current_accounting_book_id
          AND candidate_run.bank_account_assignment_id = current_bank_account_assignment_id
          AND candidate_run.currency_code = current_currency_code
          AND candidate.statement_amount <> NEW.statement_amount
    ) THEN
        RAISE EXCEPTION
            'statement source amount differs across reconciliation runs (reconciliation_source_amount_conflict)'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_candidate AS candidate
        JOIN accounting_core.reconciliation_run AS candidate_run
          ON candidate_run.tenant_account_id = candidate.tenant_account_id
         AND candidate_run.reconciliation_run_id = candidate.reconciliation_run_id
        WHERE candidate.tenant_account_id = NEW.tenant_account_id
          AND candidate.journal_reference = NEW.journal_reference
          AND candidate.reconciliation_candidate_id <> NEW.reconciliation_candidate_id
          AND candidate_run.legal_entity_id = current_legal_entity_id
          AND candidate_run.accounting_book_id = current_accounting_book_id
          AND candidate_run.currency_code = current_currency_code
          AND candidate.journal_amount <> NEW.journal_amount
    ) THEN
        RAISE EXCEPTION
            'journal source amount differs across reconciliation runs (reconciliation_source_amount_conflict)'
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

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_candidate_immutability_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'recorded reconciliation candidates are immutable; record a new candidate instead (reconciliation_candidate_immutable)'
        USING ERRCODE = '23514';
END;
$$;

CREATE TRIGGER reconciliation_candidate_immutability_guard
BEFORE UPDATE OR DELETE
ON accounting_core.reconciliation_candidate
FOR EACH ROW EXECUTE FUNCTION accounting_core.reconciliation_candidate_immutability_guard();

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
    current_legal_entity_id uuid;
    current_accounting_book_id uuid;
    current_bank_account_assignment_id uuid;
    current_currency_code text;
    consumed_amount numeric(30, 6);
    conservation_key text;
BEGIN
    SELECT
        candidate.statement_entry_reference,
        candidate.journal_reference,
        candidate.statement_amount,
        candidate.journal_amount,
        match.match_status_code,
        run_scope.legal_entity_id,
        run_scope.accounting_book_id,
        run_scope.bank_account_assignment_id,
        run_scope.currency_code
    INTO
        candidate_statement_reference,
        candidate_journal_reference,
        candidate_statement_amount,
        candidate_journal_amount,
        current_match_status,
        current_legal_entity_id,
        current_accounting_book_id,
        current_bank_account_assignment_id,
        current_currency_code
    FROM accounting_core.reconciliation_match AS match
    JOIN accounting_core.reconciliation_candidate AS candidate
      ON candidate.tenant_account_id = match.tenant_account_id
     AND candidate.reconciliation_run_id = match.reconciliation_run_id
     AND candidate.reconciliation_candidate_id = match.reconciliation_candidate_id
    JOIN accounting_core.reconciliation_run AS run_scope
      ON run_scope.tenant_account_id = match.tenant_account_id
     AND run_scope.reconciliation_run_id = match.reconciliation_run_id
    WHERE match.tenant_account_id = NEW.tenant_account_id
      AND match.reconciliation_run_id = NEW.reconciliation_run_id
      AND match.reconciliation_match_id = NEW.reconciliation_match_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'allocation match is outside the tenant reconciliation run (reconciliation_scope_mismatch)'
            USING ERRCODE = '23514';
    END IF;

    IF current_match_status <> 'proposed' THEN
    RAISE EXCEPTION
        'reviewed reconciliation allocation evidence is frozen; supersede the match and create a new proposed match (reconciliation_allocation_frozen)'
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
                current_legal_entity_id::text,
                current_accounting_book_id::text,
                current_bank_account_assignment_id::text,
                current_currency_code,
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
            JOIN accounting_core.reconciliation_run AS consuming_run
              ON consuming_run.tenant_account_id = allocation.tenant_account_id
             AND consuming_run.reconciliation_run_id = allocation.reconciliation_run_id
            WHERE allocation.tenant_account_id = NEW.tenant_account_id
              AND allocation.statement_entry_reference = NEW.statement_entry_reference
              AND approved_match.match_status_code = 'approved'
              AND consuming_run.legal_entity_id = current_legal_entity_id
              AND consuming_run.accounting_book_id = current_accounting_book_id
              AND consuming_run.bank_account_assignment_id = current_bank_account_assignment_id
              AND consuming_run.currency_code = current_currency_code;

            IF consumed_amount + NEW.allocated_amount > candidate_statement_amount THEN
                RAISE EXCEPTION
                    'approved reconciliation allocations exceed statement source amount across active runs (reconciliation_allocation_overconsumed)'
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
                current_legal_entity_id::text,
                current_accounting_book_id::text,
                current_currency_code,
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
            JOIN accounting_core.reconciliation_run AS consuming_run
              ON consuming_run.tenant_account_id = allocation.tenant_account_id
             AND consuming_run.reconciliation_run_id = allocation.reconciliation_run_id
            WHERE allocation.tenant_account_id = NEW.tenant_account_id
              AND allocation.journal_reference = NEW.journal_reference
              AND approved_match.match_status_code = 'approved'
              AND consuming_run.legal_entity_id = current_legal_entity_id
              AND consuming_run.accounting_book_id = current_accounting_book_id
              AND consuming_run.currency_code = current_currency_code;

            IF consumed_amount + NEW.allocated_amount > candidate_journal_amount THEN
                RAISE EXCEPTION
                    'approved reconciliation allocations exceed journal source amount across active runs (reconciliation_allocation_overconsumed)'
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
BEGIN
    RAISE EXCEPTION
        'recorded reconciliation allocations are immutable; supersede the match instead (reconciliation_allocation_immutable)'
        USING ERRCODE = '23514';
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
    current_legal_entity_id uuid;
    current_accounting_book_id uuid;
    current_bank_account_assignment_id uuid;
    current_currency_code text;
    statement_allocation_count bigint;
    journal_allocation_count bigint;
    statement_allocation_total numeric(30, 6);
    journal_allocation_total numeric(30, 6);
BEGIN
    IF NEW.match_status_code <> 'approved'
       OR (TG_OP = 'UPDATE' AND OLD.match_status_code = 'approved') THEN
        RETURN NEW;
    END IF;

    SELECT
        run_scope.legal_entity_id,
        run_scope.accounting_book_id,
        run_scope.bank_account_assignment_id,
        run_scope.currency_code
    INTO
        current_legal_entity_id,
        current_accounting_book_id,
        current_bank_account_assignment_id,
        current_currency_code
    FROM accounting_core.reconciliation_run AS run_scope
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
            current_bank_account_assignment_id::text,
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
        WHERE candidate.tenant_account_id = NEW.tenant_account_id
          AND candidate.statement_entry_reference = source_row.statement_entry_reference
          AND candidate_run.legal_entity_id = current_legal_entity_id
          AND candidate_run.accounting_book_id = current_accounting_book_id
          AND candidate_run.bank_account_assignment_id = current_bank_account_assignment_id
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
        WHERE allocation.tenant_account_id = NEW.tenant_account_id
          AND allocation.statement_entry_reference = source_row.statement_entry_reference
          AND approved_match.match_status_code = 'approved'
          AND approved_match.reconciliation_match_id <> NEW.reconciliation_match_id
          AND consuming_run.legal_entity_id = current_legal_entity_id
          AND consuming_run.accounting_book_id = current_accounting_book_id
          AND consuming_run.bank_account_assignment_id = current_bank_account_assignment_id
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
