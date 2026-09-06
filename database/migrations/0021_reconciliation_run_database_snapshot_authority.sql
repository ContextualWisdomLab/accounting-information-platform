BEGIN;

-- The lifecycle application reconstructs the close projection for buyer-facing
-- diagnostics, but final reconciliation authority must not trust a caller-owned
-- digest or caller-owned population references. Re-derive a same-or-stronger
-- snapshot from PostgreSQL-owned immutable statement, journal, allocation,
-- approval, and exception facts immediately before the transition row is stored.
CREATE OR REPLACE FUNCTION accounting_core.reconciliation_run_database_snapshot_authority(
    authority_tenant_account_id uuid,
    authority_reconciliation_run_id uuid
)
RETURNS TABLE (
    database_snapshot_hash text,
    database_statement_reference text,
    database_book_reference text
)
LANGUAGE plpgsql
AS $$
DECLARE
    statement_record_id uuid;
    opening_balance_hash text;
    closing_balance_hash text;
    statement_period_start_date date;
    book_cutoff_date date;
    knowledge_cutoff_at timestamptz;
    cash_chart_account_id uuid;
    run_currency_code text;
    opening_command_hash text;
    statement_opening_balance numeric(38, 6);
    statement_period_movements numeric(38, 6);
    statement_closing_balance numeric(38, 6);
    book_opening_balance numeric(38, 6);
    posted_cash_book_movements numeric(38, 6);
    book_closing_balance numeric(38, 6);
    outstanding_bank_items numeric(38, 6);
    outstanding_book_items numeric(38, 6);
    bridge_balance numeric(38, 6);
    statement_population jsonb;
    book_population jsonb;
    statement_allocation_population jsonb;
    book_allocation_population jsonb;
    reviewed_match_population jsonb;
    exception_population jsonb;
    authority_snapshot jsonb;
    opening_balance_count integer;
    closing_balance_count integer;
    statement_entry_count integer;
    statement_identity_count integer;
BEGIN
    SELECT statement.bank_statement_record_id,
           statement.opening_balance_hash,
           statement.closing_balance_hash,
           statement.period_start_at::date,
           run_record.book_cutoff_at::date,
           run_record.knowledge_cutoff_at,
           assignment.chart_account_id,
           run_record.currency_code,
           run_command.reconciliation_command_hash
    INTO statement_record_id,
         opening_balance_hash,
         closing_balance_hash,
         statement_period_start_date,
         book_cutoff_date,
         knowledge_cutoff_at,
         cash_chart_account_id,
         run_currency_code,
         opening_command_hash
    FROM accounting_core.reconciliation_run AS run_record
    JOIN accounting_core.reconciliation_run_command AS run_command
      ON run_command.tenant_account_id = run_record.tenant_account_id
     AND run_command.reconciliation_run_id = run_record.reconciliation_run_id
    JOIN accounting_integration.bank_statement_record AS statement
      ON statement.tenant_account_id = run_command.tenant_account_id
     AND statement.bank_statement_record_id = run_command.bank_statement_record_id
    JOIN accounting_core.bank_account_assignment AS assignment
      ON assignment.tenant_account_id = run_record.tenant_account_id
     AND assignment.bank_account_assignment_id = run_record.bank_account_assignment_id
    WHERE run_record.tenant_account_id = authority_tenant_account_id
      AND run_record.reconciliation_run_id = authority_reconciliation_run_id;

    IF statement_record_id IS NULL
       OR opening_balance_hash IS NULL
       OR closing_balance_hash IS NULL
       OR statement_period_start_date IS NULL
       OR book_cutoff_date IS NULL
       OR knowledge_cutoff_at IS NULL
       OR cash_chart_account_id IS NULL
       OR run_currency_code IS NULL
       OR opening_command_hash IS NULL THEN
        RAISE EXCEPTION
            'database reconciliation snapshot requires one complete run/source scope (reconciliation_database_snapshot_scope)'
            USING ERRCODE = '23514';
    END IF;

    SELECT count(*) FILTER (WHERE balance.source_balance_hash = opening_balance_hash),
           count(*) FILTER (WHERE balance.source_balance_hash = closing_balance_hash),
           max(
               CASE
                   WHEN balance.source_balance_hash = opening_balance_hash
                    AND balance.credit_debit_code = 'CRDT'
                   THEN balance.balance_amount
                   WHEN balance.source_balance_hash = opening_balance_hash
                    AND balance.credit_debit_code = 'DBIT'
                   THEN -balance.balance_amount
               END
           ),
           max(
               CASE
                   WHEN balance.source_balance_hash = closing_balance_hash
                    AND balance.credit_debit_code = 'CRDT'
                   THEN balance.balance_amount
                   WHEN balance.source_balance_hash = closing_balance_hash
                    AND balance.credit_debit_code = 'DBIT'
                   THEN -balance.balance_amount
               END
           )
    INTO opening_balance_count,
         closing_balance_count,
         statement_opening_balance,
         statement_closing_balance
    FROM accounting_integration.bank_statement_balance AS balance
    WHERE balance.tenant_account_id = authority_tenant_account_id
      AND balance.bank_statement_record_id = statement_record_id
      AND balance.source_balance_hash IN (opening_balance_hash, closing_balance_hash)
      AND balance.recorded_at <= knowledge_cutoff_at;

    IF opening_balance_count <> 1 OR closing_balance_count <> 1 THEN
        RAISE EXCEPTION
            'database reconciliation snapshot requires exactly one opening and closing balance row (reconciliation_database_balance_cardinality)'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM accounting_integration.bank_statement_balance AS balance
        WHERE balance.tenant_account_id = authority_tenant_account_id
          AND balance.bank_statement_record_id = statement_record_id
          AND balance.source_balance_hash IN (opening_balance_hash, closing_balance_hash)
          AND balance.recorded_at <= knowledge_cutoff_at
          AND balance.balance_currency_code IS DISTINCT FROM run_currency_code
    ) THEN
        RAISE EXCEPTION
            'database reconciliation balance currency differs from run currency (reconciliation_database_currency_scope)'
            USING ERRCODE = '23514';
    END IF;

    SELECT count(*),
           count(DISTINCT COALESCE(NULLIF(entry.source_entry_identity, ''), entry.bank_statement_entry_id::text)),
           COALESCE(
               sum(
                   CASE entry.credit_debit_code
                       WHEN 'CRDT' THEN entry.entry_amount
                       WHEN 'DBIT' THEN -entry.entry_amount
                   END
                   * CASE WHEN entry.reversal_indicator THEN -1 ELSE 1 END
               ),
               0
           )::numeric(38, 6),
           COALESCE(
               jsonb_agg(
                   jsonb_build_object(
                       'entry_identity', COALESCE(NULLIF(entry.source_entry_identity, ''), entry.bank_statement_entry_id::text),
                       'entry_sequence_number', entry.entry_sequence_number,
                       'entry_amount', entry.entry_amount,
                       'entry_currency_code', entry.entry_currency_code,
                       'credit_debit_code', entry.credit_debit_code,
                       'reversal_indicator', entry.reversal_indicator,
                       'source_entry_hash', entry.source_entry_hash
                   )
                   ORDER BY entry.entry_sequence_number, entry.bank_statement_entry_id
               ),
               '[]'::jsonb
           )
    INTO statement_entry_count,
         statement_identity_count,
         statement_period_movements,
         statement_population
    FROM accounting_integration.bank_statement_entry AS entry
    WHERE entry.tenant_account_id = authority_tenant_account_id
      AND entry.bank_statement_record_id = statement_record_id
      AND entry.recorded_at <= knowledge_cutoff_at;

    IF statement_entry_count = 0 OR statement_entry_count <> statement_identity_count THEN
        RAISE EXCEPTION
            'database reconciliation statement population requires non-empty unique source identities (reconciliation_database_statement_population)'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM accounting_integration.bank_statement_entry AS entry
        WHERE entry.tenant_account_id = authority_tenant_account_id
          AND entry.bank_statement_record_id = statement_record_id
          AND entry.recorded_at <= knowledge_cutoff_at
          AND entry.entry_currency_code IS DISTINCT FROM run_currency_code
    ) THEN
        RAISE EXCEPTION
            'database reconciliation statement entry currency differs from run currency (reconciliation_database_currency_scope)'
            USING ERRCODE = '23514';
    END IF;

    IF statement_opening_balance + statement_period_movements
       IS DISTINCT FROM statement_closing_balance THEN
        RAISE EXCEPTION
            'database reconciliation statement opening plus movements does not equal closing balance (reconciliation_database_statement_equation)'
            USING ERRCODE = '23514';
    END IF;

    SELECT COALESCE(
               sum(line.debit_amount - line.credit_amount)
                   FILTER (WHERE journal.accounting_date < statement_period_start_date),
               0
           )::numeric(38, 6),
           COALESCE(
               sum(line.debit_amount - line.credit_amount)
                   FILTER (WHERE journal.accounting_date >= statement_period_start_date),
               0
           )::numeric(38, 6),
           COALESCE(
               jsonb_agg(
                   jsonb_build_object(
                       'journal_reference', journal.journal_reference,
                       'accounting_date', journal.accounting_date,
                       'posted_at', journal.posted_at,
                       'line_number', line.line_number,
                       'debit_amount', line.debit_amount,
                       'credit_amount', line.credit_amount,
                       'currency_code', journal.transaction_currency_code
                   )
                   ORDER BY journal.accounting_date,
                            journal.posted_at,
                            journal.journal_reference,
                            line.line_number
               ),
               '[]'::jsonb
           )
    INTO book_opening_balance,
         posted_cash_book_movements,
         book_population
    FROM accounting_core.journal_entry_line AS line
    JOIN accounting_core.general_journal AS journal
      ON journal.tenant_account_id = line.tenant_account_id
     AND journal.general_journal_id = line.general_journal_id
    JOIN accounting_core.chart_account AS cash_account
      ON cash_account.tenant_account_id = line.tenant_account_id
     AND cash_account.chart_account_id = line.chart_account_id
    WHERE line.tenant_account_id = authority_tenant_account_id
      AND line.chart_account_id = cash_chart_account_id
      AND journal.accounting_book_id = cash_account.accounting_book_id
      AND journal.accounting_date <= book_cutoff_date
      AND journal.posted_at <= knowledge_cutoff_at;

    IF EXISTS (
        SELECT 1
        FROM accounting_core.journal_entry_line AS line
        JOIN accounting_core.general_journal AS journal
          ON journal.tenant_account_id = line.tenant_account_id
         AND journal.general_journal_id = line.general_journal_id
        JOIN accounting_core.chart_account AS cash_account
          ON cash_account.tenant_account_id = line.tenant_account_id
         AND cash_account.chart_account_id = line.chart_account_id
        WHERE line.tenant_account_id = authority_tenant_account_id
          AND line.chart_account_id = cash_chart_account_id
          AND journal.accounting_book_id = cash_account.accounting_book_id
          AND journal.accounting_date <= book_cutoff_date
          AND journal.posted_at <= knowledge_cutoff_at
          AND journal.transaction_currency_code IS DISTINCT FROM run_currency_code
    ) THEN
        RAISE EXCEPTION
            'database reconciliation cash journal currency differs from run currency (reconciliation_database_currency_scope)'
            USING ERRCODE = '23514';
    END IF;

    book_closing_balance := book_opening_balance + posted_cash_book_movements;

    WITH approved_allocations AS (
        SELECT allocation.statement_entry_reference,
               sum(allocation.allocated_amount)::numeric(38, 6) AS allocated_amount
        FROM accounting_core.statement_match_allocation AS allocation
        JOIN accounting_core.reconciliation_match AS reviewed_match
          ON reviewed_match.tenant_account_id = allocation.tenant_account_id
         AND reviewed_match.reconciliation_run_id = allocation.reconciliation_run_id
         AND reviewed_match.reconciliation_match_id = allocation.reconciliation_match_id
        WHERE allocation.tenant_account_id = authority_tenant_account_id
          AND allocation.reconciliation_run_id = authority_reconciliation_run_id
          AND reviewed_match.match_status_code = 'approved'
        GROUP BY allocation.statement_entry_reference
    ),
    statement_sources AS (
        SELECT COALESCE(NULLIF(entry.source_entry_identity, ''), entry.bank_statement_entry_id::text) AS source_reference,
               (
                   CASE entry.credit_debit_code
                       WHEN 'CRDT' THEN entry.entry_amount
                       WHEN 'DBIT' THEN -entry.entry_amount
                   END
                   * CASE WHEN entry.reversal_indicator THEN -1 ELSE 1 END
               )::numeric(38, 6) AS signed_amount
        FROM accounting_integration.bank_statement_entry AS entry
        WHERE entry.tenant_account_id = authority_tenant_account_id
          AND entry.bank_statement_record_id = statement_record_id
          AND entry.recorded_at <= knowledge_cutoff_at
    )
    SELECT COALESCE(
               sum(
                   CASE
                       WHEN source.signed_amount < 0
                       THEN -(abs(source.signed_amount) - COALESCE(allocation.allocated_amount, 0))
                       ELSE abs(source.signed_amount) - COALESCE(allocation.allocated_amount, 0)
                   END
               ),
               0
           )::numeric(38, 6)
    INTO outstanding_book_items
    FROM statement_sources AS source
    LEFT JOIN approved_allocations AS allocation
      ON allocation.statement_entry_reference = source.source_reference;

    IF EXISTS (
        WITH approved_allocations AS (
            SELECT allocation.statement_entry_reference,
                   sum(allocation.allocated_amount)::numeric(38, 6) AS allocated_amount
            FROM accounting_core.statement_match_allocation AS allocation
            JOIN accounting_core.reconciliation_match AS reviewed_match
              ON reviewed_match.tenant_account_id = allocation.tenant_account_id
             AND reviewed_match.reconciliation_run_id = allocation.reconciliation_run_id
             AND reviewed_match.reconciliation_match_id = allocation.reconciliation_match_id
            WHERE allocation.tenant_account_id = authority_tenant_account_id
              AND allocation.reconciliation_run_id = authority_reconciliation_run_id
              AND reviewed_match.match_status_code = 'approved'
            GROUP BY allocation.statement_entry_reference
        ),
        statement_sources AS (
            SELECT COALESCE(NULLIF(entry.source_entry_identity, ''), entry.bank_statement_entry_id::text) AS source_reference,
                   abs(
                       CASE entry.credit_debit_code
                           WHEN 'CRDT' THEN entry.entry_amount
                           WHEN 'DBIT' THEN -entry.entry_amount
                       END
                       * CASE WHEN entry.reversal_indicator THEN -1 ELSE 1 END
                   )::numeric(38, 6) AS source_capacity
            FROM accounting_integration.bank_statement_entry AS entry
            WHERE entry.tenant_account_id = authority_tenant_account_id
              AND entry.bank_statement_record_id = statement_record_id
              AND entry.recorded_at <= knowledge_cutoff_at
        )
        SELECT 1
        FROM approved_allocations AS allocation
        LEFT JOIN statement_sources AS source
          ON source.source_reference = allocation.statement_entry_reference
        WHERE source.source_reference IS NULL
           OR allocation.allocated_amount > source.source_capacity
    ) THEN
        RAISE EXCEPTION
            'database reconciliation approved statement allocation exceeds or misses its source (reconciliation_database_statement_allocation)'
            USING ERRCODE = '23514';
    END IF;

    WITH journal_sources AS (
        SELECT journal.journal_reference,
               sum(line.debit_amount - line.credit_amount)::numeric(38, 6) AS signed_amount,
               bool_or(journal.accounting_date >= statement_period_start_date) AS period_member
        FROM accounting_core.journal_entry_line AS line
        JOIN accounting_core.general_journal AS journal
          ON journal.tenant_account_id = line.tenant_account_id
         AND journal.general_journal_id = line.general_journal_id
        JOIN accounting_core.chart_account AS cash_account
          ON cash_account.tenant_account_id = line.tenant_account_id
         AND cash_account.chart_account_id = line.chart_account_id
        WHERE line.tenant_account_id = authority_tenant_account_id
          AND line.chart_account_id = cash_chart_account_id
          AND journal.accounting_book_id = cash_account.accounting_book_id
          AND journal.accounting_date <= book_cutoff_date
          AND journal.posted_at <= knowledge_cutoff_at
        GROUP BY journal.journal_reference
    ),
    approved_allocations AS (
        SELECT allocation.journal_reference,
               sum(allocation.allocated_amount)::numeric(38, 6) AS allocated_amount
        FROM accounting_core.journal_match_allocation AS allocation
        JOIN accounting_core.reconciliation_match AS reviewed_match
          ON reviewed_match.tenant_account_id = allocation.tenant_account_id
         AND reviewed_match.reconciliation_run_id = allocation.reconciliation_run_id
         AND reviewed_match.reconciliation_match_id = allocation.reconciliation_match_id
        WHERE allocation.tenant_account_id = authority_tenant_account_id
          AND allocation.reconciliation_run_id = authority_reconciliation_run_id
          AND reviewed_match.match_status_code = 'approved'
        GROUP BY allocation.journal_reference
    )
    SELECT COALESCE(
               sum(
                   CASE
                       WHEN source.signed_amount < 0
                       THEN -(abs(source.signed_amount) - COALESCE(allocation.allocated_amount, 0))
                       ELSE abs(source.signed_amount) - COALESCE(allocation.allocated_amount, 0)
                   END
               ) FILTER (WHERE source.period_member AND source.signed_amount <> 0),
               0
           )::numeric(38, 6)
    INTO outstanding_bank_items
    FROM journal_sources AS source
    LEFT JOIN approved_allocations AS allocation
      ON allocation.journal_reference = source.journal_reference;

    IF EXISTS (
        WITH journal_sources AS (
            SELECT journal.journal_reference,
                   abs(sum(line.debit_amount - line.credit_amount))::numeric(38, 6) AS source_capacity
            FROM accounting_core.journal_entry_line AS line
            JOIN accounting_core.general_journal AS journal
              ON journal.tenant_account_id = line.tenant_account_id
             AND journal.general_journal_id = line.general_journal_id
            JOIN accounting_core.chart_account AS cash_account
              ON cash_account.tenant_account_id = line.tenant_account_id
             AND cash_account.chart_account_id = line.chart_account_id
            WHERE line.tenant_account_id = authority_tenant_account_id
              AND line.chart_account_id = cash_chart_account_id
              AND journal.accounting_book_id = cash_account.accounting_book_id
              AND journal.accounting_date <= book_cutoff_date
              AND journal.posted_at <= knowledge_cutoff_at
            GROUP BY journal.journal_reference
        ),
        approved_allocations AS (
            SELECT allocation.journal_reference,
                   sum(allocation.allocated_amount)::numeric(38, 6) AS allocated_amount
            FROM accounting_core.journal_match_allocation AS allocation
            JOIN accounting_core.reconciliation_match AS reviewed_match
              ON reviewed_match.tenant_account_id = allocation.tenant_account_id
             AND reviewed_match.reconciliation_run_id = allocation.reconciliation_run_id
             AND reviewed_match.reconciliation_match_id = allocation.reconciliation_match_id
            WHERE allocation.tenant_account_id = authority_tenant_account_id
              AND allocation.reconciliation_run_id = authority_reconciliation_run_id
              AND reviewed_match.match_status_code = 'approved'
            GROUP BY allocation.journal_reference
        )
        SELECT 1
        FROM approved_allocations AS allocation
        LEFT JOIN journal_sources AS source
          ON source.journal_reference = allocation.journal_reference
        WHERE source.journal_reference IS NULL
           OR allocation.allocated_amount > source.source_capacity
    ) THEN
        RAISE EXCEPTION
            'database reconciliation approved journal allocation exceeds or misses its source (reconciliation_database_journal_allocation)'
            USING ERRCODE = '23514';
    END IF;

    bridge_balance := book_closing_balance + outstanding_book_items - outstanding_bank_items;
    IF bridge_balance IS DISTINCT FROM statement_closing_balance THEN
        RAISE EXCEPTION
            'database-owned book-to-bank bridge contains an unexplained difference (reconciliation_database_bridge_unexplained)'
            USING ERRCODE = '23514';
    END IF;

    SELECT COALESCE(
               jsonb_agg(
                   jsonb_build_object(
                       'statement_entry_reference', allocation.statement_entry_reference,
                       'allocated_amount', allocation.allocated_amount,
                       'reconciliation_match_id', allocation.reconciliation_match_id::text
                   )
                   ORDER BY allocation.statement_entry_reference,
                            allocation.reconciliation_allocation_id
               ),
               '[]'::jsonb
           )
    INTO statement_allocation_population
    FROM accounting_core.statement_match_allocation AS allocation
    JOIN accounting_core.reconciliation_match AS reviewed_match
      ON reviewed_match.tenant_account_id = allocation.tenant_account_id
     AND reviewed_match.reconciliation_run_id = allocation.reconciliation_run_id
     AND reviewed_match.reconciliation_match_id = allocation.reconciliation_match_id
    WHERE allocation.tenant_account_id = authority_tenant_account_id
      AND allocation.reconciliation_run_id = authority_reconciliation_run_id
      AND reviewed_match.match_status_code = 'approved';

    SELECT COALESCE(
               jsonb_agg(
                   jsonb_build_object(
                       'journal_reference', allocation.journal_reference,
                       'allocated_amount', allocation.allocated_amount,
                       'reconciliation_match_id', allocation.reconciliation_match_id::text
                   )
                   ORDER BY allocation.journal_reference,
                            allocation.reconciliation_allocation_id
               ),
               '[]'::jsonb
           )
    INTO book_allocation_population
    FROM accounting_core.journal_match_allocation AS allocation
    JOIN accounting_core.reconciliation_match AS reviewed_match
      ON reviewed_match.tenant_account_id = allocation.tenant_account_id
     AND reviewed_match.reconciliation_run_id = allocation.reconciliation_run_id
     AND reviewed_match.reconciliation_match_id = allocation.reconciliation_match_id
    WHERE allocation.tenant_account_id = authority_tenant_account_id
      AND allocation.reconciliation_run_id = authority_reconciliation_run_id
      AND reviewed_match.match_status_code = 'approved';

    SELECT COALESCE(
               jsonb_agg(
                   jsonb_build_object(
                       'reconciliation_match_id', reviewed_match.reconciliation_match_id::text,
                       'match_status_code', reviewed_match.match_status_code,
                       'reconciliation_approval_id', approval.reconciliation_approval_id::text,
                       'approval_decision_code', approval.approval_decision_code,
                       'approval_snapshot_hash', approval.reconciliation_snapshot_hash
                   )
                   ORDER BY reviewed_match.reconciliation_match_id
               ),
               '[]'::jsonb
           )
    INTO reviewed_match_population
    FROM accounting_core.reconciliation_match AS reviewed_match
    LEFT JOIN accounting_core.reconciliation_approval AS approval
      ON approval.tenant_account_id = reviewed_match.tenant_account_id
     AND approval.reconciliation_run_id = reviewed_match.reconciliation_run_id
     AND approval.reconciliation_match_id = reviewed_match.reconciliation_match_id
    WHERE reviewed_match.tenant_account_id = authority_tenant_account_id
      AND reviewed_match.reconciliation_run_id = authority_reconciliation_run_id;

    SELECT COALESCE(
               jsonb_agg(
                   jsonb_build_object(
                       'reconciliation_exception_id', exception.reconciliation_exception_id::text,
                       'exception_code', exception.exception_code,
                       'owner_reference', exception.owner_reference,
                       'effective_at', exception.effective_at,
                       'resolution_status_code', exception.resolution_status_code
                   )
                   ORDER BY exception.reconciliation_exception_id
               ),
               '[]'::jsonb
           )
    INTO exception_population
    FROM accounting_core.reconciliation_exception AS exception
    WHERE exception.tenant_account_id = authority_tenant_account_id
      AND exception.reconciliation_run_id = authority_reconciliation_run_id;

    database_statement_reference := 'sha256:' || encode(
        sha256(
            convert_to(
                'reconciliation_transition_statement_population:v1|' || statement_population::text,
                'UTF8'
            )
        ),
        'hex'
    );
    database_book_reference := 'sha256:' || encode(
        sha256(
            convert_to(
                'reconciliation_transition_book_population:v1|' || book_population::text,
                'UTF8'
            )
        ),
        'hex'
    );

    authority_snapshot := jsonb_build_object(
        'book_allocation_population', book_allocation_population,
        'book_closing_balance', book_closing_balance,
        'book_opening_balance', book_opening_balance,
        'book_population', book_population,
        'book_population_reference', database_book_reference,
        'exception_population', exception_population,
        'knowledge_cutoff_at', knowledge_cutoff_at,
        'opening_command_hash', opening_command_hash,
        'posted_cash_book_movements', posted_cash_book_movements,
        'reconciliation_run_id', authority_reconciliation_run_id::text,
        'reviewed_match_population', reviewed_match_population,
        'statement_allocation_population', statement_allocation_population,
        'statement_closing_balance', statement_closing_balance,
        'statement_opening_balance', statement_opening_balance,
        'statement_period_movements', statement_period_movements,
        'statement_population', statement_population,
        'statement_population_reference', database_statement_reference,
        'tenant_account_id', authority_tenant_account_id::text,
        'outstanding_bank_items', outstanding_bank_items,
        'outstanding_book_items', outstanding_book_items,
        'unexplained_difference', 0
    );
    database_snapshot_hash := 'sha256:' || encode(
        sha256(
            convert_to(
                'reconciliation_run_transition_database_snapshot:v1|' || authority_snapshot::text,
                'UTF8'
            )
        ),
        'hex'
    );
    RETURN NEXT;
END;
$$;

CREATE OR REPLACE FUNCTION accounting_core.assign_reconciliation_run_database_snapshot_authority()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    database_snapshot_hash text;
    database_statement_reference text;
    database_book_reference text;
BEGIN
    SELECT authority.database_snapshot_hash,
           authority.database_statement_reference,
           authority.database_book_reference
    INTO database_snapshot_hash,
         database_statement_reference,
         database_book_reference
    FROM accounting_core.reconciliation_run_database_snapshot_authority(
        NEW.tenant_account_id,
        NEW.reconciliation_run_id
    ) AS authority;

    IF database_snapshot_hash IS NULL
       OR database_statement_reference IS NULL
       OR database_book_reference IS NULL THEN
        RAISE EXCEPTION
            'database reconciliation snapshot authority could not be derived (reconciliation_database_snapshot_missing)'
            USING ERRCODE = '23514';
    END IF;

    NEW.reconciliation_snapshot_hash := database_snapshot_hash;
    NEW.statement_population_reference := database_statement_reference;
    NEW.book_population_reference := database_book_reference;
    RETURN NEW;
END;
$$;

-- PostgreSQL executes triggers of the same timing/event in name order. This
-- database-authority guard intentionally sorts before the existing hash guard,
-- so the transition command hash commits only the server-derived snapshot and
-- population references. Child migrations may replace the hash guard function
-- without regaining caller authority over these three fields.
CREATE TRIGGER accounting_reconciliation_transition_database_authority_guard
    BEFORE INSERT ON accounting_core.reconciliation_run_transition_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.assign_reconciliation_run_database_snapshot_authority();

COMMIT;
