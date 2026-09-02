BEGIN;

-- Exception-resolution authority is only complete when the immutable command,
-- the terminal exception status, and the matching accounting outbox event are
-- committed together. Migration 0020 already defers command/status validation;
-- this forward migration adds the missing third leg without weakening the
-- existing maker-checker or reconciliation lifecycle invariants.
CREATE OR REPLACE FUNCTION accounting_core.enforce_reconciliation_exception_resolution_outbox_pair()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    expected_event_type_code text;
    matching_outbox_event_count integer;
BEGIN
    expected_event_type_code := CASE NEW.target_resolution_status_code
        WHEN 'resolved' THEN 'reconciliation_exception_resolved'
        WHEN 'superseded' THEN 'reconciliation_exception_superseded'
        ELSE NULL
    END;

    IF expected_event_type_code IS NULL THEN
        RAISE EXCEPTION
            'reconciliation exception resolution target status is not supported (reconciliation_exception_resolution_atomic_outbox)'
            USING ERRCODE = '23514';
    END IF;

    SELECT count(*)
    INTO matching_outbox_event_count
    FROM accounting_integration.outbox_event AS event
    WHERE event.tenant_account_id = NEW.tenant_account_id
      AND event.event_type_code = expected_event_type_code
      AND event.aggregate_reference =
          'urn:cwl:accounting:reconciliation_exception:'
          || NEW.reconciliation_exception_id::text
      AND event.payload_reference =
          'urn:cwl:accounting:reconciliation_exception_resolution:'
          || NEW.reconciliation_exception_resolution_command_id::text
      AND event.payload_hash = NEW.reconciliation_exception_resolution_command_hash;

    IF matching_outbox_event_count <> 1 THEN
        RAISE EXCEPTION
            'reconciliation exception resolution command, terminal status, and matching outbox event must commit atomically (reconciliation_exception_resolution_atomic_outbox)'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER reconciliation_exception_resolution_outbox_pair_guard
    AFTER INSERT ON accounting_core.reconciliation_exception_resolution_command
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.enforce_reconciliation_exception_resolution_outbox_pair();

-- The application constructs a coherent REPEATABLE READ bridge before asking
-- PostgreSQL to record reconciliation completion, but a direct INSERT into the
-- transition-command table must not be able to promote an arbitrary digest into
-- accounting authority. This function reconstructs the complete source/control
-- population in one SQL statement snapshot, proves the exact book-to-bank bridge,
-- and returns a database-owned digest over those facts. The digest is intentionally
-- server-native rather than a replay of Python serialization; the database owns
-- the authoritative lifecycle snapshot and the existing command hash binds it.
CREATE OR REPLACE FUNCTION accounting_core.reconciliation_run_database_snapshot_hash(
    snapshot_tenant_account_id uuid,
    snapshot_reconciliation_run_id uuid
)
RETURNS text
LANGUAGE plpgsql
AS $$
DECLARE
    authority_payload jsonb;
    scope_count integer;
    balance_count integer;
    statement_entry_count integer;
    invalid_balance_currency_count integer;
    invalid_statement_currency_count integer;
    invalid_journal_currency_count integer;
    unknown_statement_allocation_count integer;
    unknown_journal_allocation_count integer;
    over_statement_capacity_count integer;
    over_journal_capacity_count integer;
    statement_opening numeric;
    statement_movements numeric;
    statement_closing numeric;
    book_closing numeric;
    outstanding_book numeric;
    outstanding_bank numeric;
BEGIN
    PERFORM accounting_core.acquire_reconciliation_run_lifecycle_lock(
        snapshot_tenant_account_id,
        snapshot_reconciliation_run_id
    );

    WITH scope AS (
        SELECT run.reconciliation_run_id,
               run.currency_code,
               run.knowledge_cutoff_at,
               run.book_cutoff_at::date AS book_cutoff_date,
               command.reconciliation_command_hash AS opening_command_hash,
               statement.bank_statement_record_id,
               statement.opening_balance_hash,
               statement.closing_balance_hash,
               statement.period_start_at,
               assignment.chart_account_id
        FROM accounting_core.reconciliation_run AS run
        JOIN accounting_core.reconciliation_run_command AS command
          ON command.tenant_account_id = run.tenant_account_id
         AND command.reconciliation_run_id = run.reconciliation_run_id
        JOIN accounting_integration.bank_statement_record AS statement
          ON statement.tenant_account_id = command.tenant_account_id
         AND statement.bank_statement_record_id = command.bank_statement_record_id
        JOIN accounting_core.bank_account_assignment AS assignment
          ON assignment.tenant_account_id = run.tenant_account_id
         AND assignment.bank_account_assignment_id = run.bank_account_assignment_id
        WHERE run.tenant_account_id = snapshot_tenant_account_id
          AND run.reconciliation_run_id = snapshot_reconciliation_run_id
    ),
    balance_rows AS (
        SELECT balance.source_balance_hash,
               balance.balance_sequence_number,
               balance.balance_amount,
               balance.balance_currency_code,
               balance.credit_debit_code,
               CASE balance.credit_debit_code
                   WHEN 'CRDT' THEN balance.balance_amount
                   WHEN 'DBIT' THEN -balance.balance_amount
                   ELSE NULL
               END AS signed_amount
        FROM accounting_integration.bank_statement_balance AS balance
        JOIN scope
          ON balance.tenant_account_id = snapshot_tenant_account_id
         AND balance.bank_statement_record_id = scope.bank_statement_record_id
         AND balance.source_balance_hash IN (
             scope.opening_balance_hash,
             scope.closing_balance_hash
         )
         AND balance.recorded_at <= scope.knowledge_cutoff_at
    ),
    statement_entries AS (
        SELECT COALESCE(
                   NULLIF(entry.source_entry_identity, ''),
                   entry.bank_statement_entry_id::text
               ) AS source_reference,
               entry.entry_sequence_number,
               entry.entry_amount,
               entry.entry_currency_code,
               entry.credit_debit_code,
               entry.reversal_indicator,
               entry.source_entry_hash,
               CASE entry.credit_debit_code
                   WHEN 'CRDT' THEN
                       CASE WHEN entry.reversal_indicator
                           THEN -entry.entry_amount ELSE entry.entry_amount END
                   WHEN 'DBIT' THEN
                       CASE WHEN entry.reversal_indicator
                           THEN entry.entry_amount ELSE -entry.entry_amount END
                   ELSE NULL
               END AS signed_amount
        FROM accounting_integration.bank_statement_entry AS entry
        JOIN scope
          ON entry.tenant_account_id = snapshot_tenant_account_id
         AND entry.bank_statement_record_id = scope.bank_statement_record_id
         AND entry.recorded_at <= scope.knowledge_cutoff_at
    ),
    journal_lines AS (
        SELECT journal.journal_reference,
               journal.accounting_date,
               journal.posted_at,
               line.line_number,
               line.debit_amount,
               line.credit_amount,
               journal.transaction_currency_code,
               line.debit_amount - line.credit_amount AS signed_amount,
               scope.period_start_at::date AS period_start_date
        FROM accounting_core.journal_entry_line AS line
        JOIN accounting_core.general_journal AS journal
          ON journal.tenant_account_id = line.tenant_account_id
         AND journal.general_journal_id = line.general_journal_id
        JOIN accounting_core.chart_account AS cash_account
          ON cash_account.tenant_account_id = line.tenant_account_id
         AND cash_account.chart_account_id = line.chart_account_id
        JOIN scope
          ON line.tenant_account_id = snapshot_tenant_account_id
         AND line.chart_account_id = scope.chart_account_id
         AND journal.accounting_book_id = cash_account.accounting_book_id
         AND journal.accounting_date <= scope.book_cutoff_date
         AND journal.posted_at <= scope.knowledge_cutoff_at
    ),
    journal_sources AS (
        SELECT journal_reference,
               sum(signed_amount) AS signed_amount,
               bool_or(accounting_date >= period_start_date) AS current_period
        FROM journal_lines
        GROUP BY journal_reference
    ),
    statement_allocation_rows AS (
        SELECT allocation.statement_entry_reference AS source_reference,
               allocation.allocated_amount
        FROM accounting_core.statement_match_allocation AS allocation
        JOIN accounting_core.reconciliation_match AS reviewed_match
          ON reviewed_match.tenant_account_id = allocation.tenant_account_id
         AND reviewed_match.reconciliation_run_id = allocation.reconciliation_run_id
         AND reviewed_match.reconciliation_match_id = allocation.reconciliation_match_id
        WHERE allocation.tenant_account_id = snapshot_tenant_account_id
          AND allocation.reconciliation_run_id = snapshot_reconciliation_run_id
          AND reviewed_match.match_status_code = 'approved'
    ),
    journal_allocation_rows AS (
        SELECT allocation.journal_reference AS source_reference,
               allocation.allocated_amount
        FROM accounting_core.journal_match_allocation AS allocation
        JOIN accounting_core.reconciliation_match AS reviewed_match
          ON reviewed_match.tenant_account_id = allocation.tenant_account_id
         AND reviewed_match.reconciliation_run_id = allocation.reconciliation_run_id
         AND reviewed_match.reconciliation_match_id = allocation.reconciliation_match_id
        WHERE allocation.tenant_account_id = snapshot_tenant_account_id
          AND allocation.reconciliation_run_id = snapshot_reconciliation_run_id
          AND reviewed_match.match_status_code = 'approved'
    ),
    statement_allocated AS (
        SELECT source_reference, sum(allocated_amount) AS allocated_amount
        FROM statement_allocation_rows
        GROUP BY source_reference
    ),
    journal_allocated AS (
        SELECT source_reference, sum(allocated_amount) AS allocated_amount
        FROM journal_allocation_rows
        GROUP BY source_reference
    ),
    bridge AS (
        SELECT
            max(balance_rows.signed_amount) FILTER (
                WHERE balance_rows.source_balance_hash = scope.opening_balance_hash
            ) AS statement_opening,
            COALESCE((SELECT sum(signed_amount) FROM statement_entries), 0::numeric)
                AS statement_movements,
            max(balance_rows.signed_amount) FILTER (
                WHERE balance_rows.source_balance_hash = scope.closing_balance_hash
            ) AS statement_closing,
            COALESCE((SELECT sum(signed_amount) FROM journal_sources), 0::numeric)
                AS book_closing,
            COALESCE((
                SELECT sum(
                    CASE WHEN entry.signed_amount < 0
                        THEN -(abs(entry.signed_amount) - COALESCE(allocated.allocated_amount, 0))
                        ELSE abs(entry.signed_amount) - COALESCE(allocated.allocated_amount, 0)
                    END
                )
                FROM statement_entries AS entry
                LEFT JOIN statement_allocated AS allocated
                  ON allocated.source_reference = entry.source_reference
            ), 0::numeric) AS outstanding_book,
            COALESCE((
                SELECT sum(
                    CASE WHEN source.signed_amount < 0
                        THEN -(abs(source.signed_amount) - COALESCE(allocated.allocated_amount, 0))
                        ELSE abs(source.signed_amount) - COALESCE(allocated.allocated_amount, 0)
                    END
                )
                FROM journal_sources AS source
                LEFT JOIN journal_allocated AS allocated
                  ON allocated.source_reference = source.journal_reference
                WHERE source.current_period AND source.signed_amount <> 0
            ), 0::numeric) AS outstanding_bank
        FROM scope
        LEFT JOIN balance_rows ON true
        GROUP BY scope.opening_balance_hash, scope.closing_balance_hash
    ),
    controls AS (
        SELECT
            (SELECT count(*) FROM scope) AS scope_count,
            (SELECT count(*) FROM balance_rows) AS balance_count,
            (SELECT count(*) FROM statement_entries) AS statement_entry_count,
            (SELECT count(*)
             FROM balance_rows, scope
             WHERE balance_rows.balance_currency_code <> scope.currency_code
                OR balance_rows.signed_amount IS NULL) AS invalid_balance_currency_count,
            (SELECT count(*)
             FROM statement_entries, scope
             WHERE statement_entries.entry_currency_code <> scope.currency_code
                OR statement_entries.signed_amount IS NULL) AS invalid_statement_currency_count,
            (SELECT count(*)
             FROM journal_lines, scope
             WHERE journal_lines.transaction_currency_code <> scope.currency_code) AS invalid_journal_currency_count,
            (SELECT count(*)
             FROM statement_allocation_rows AS allocation
             LEFT JOIN statement_entries AS source
               ON source.source_reference = allocation.source_reference
             WHERE source.source_reference IS NULL) AS unknown_statement_allocation_count,
            (SELECT count(*)
             FROM journal_allocation_rows AS allocation
             LEFT JOIN journal_sources AS source
               ON source.journal_reference = allocation.source_reference
             WHERE source.journal_reference IS NULL) AS unknown_journal_allocation_count,
            (SELECT count(*)
             FROM statement_allocated AS allocation
             JOIN statement_entries AS source
               ON source.source_reference = allocation.source_reference
             WHERE allocation.allocated_amount > abs(source.signed_amount)) AS over_statement_capacity_count,
            (SELECT count(*)
             FROM journal_allocated AS allocation
             JOIN journal_sources AS source
               ON source.journal_reference = allocation.source_reference
             WHERE allocation.allocated_amount > abs(source.signed_amount)) AS over_journal_capacity_count
    ),
    review_state AS (
        SELECT COALESCE(
            jsonb_agg(
                jsonb_build_array(
                    reviewed_match.reconciliation_match_id::text,
                    reviewed_match.match_status_code,
                    COALESCE(approval.approval_decision_code, ''),
                    COALESCE(approval.reconciliation_snapshot_hash, '')
                )
                ORDER BY reviewed_match.reconciliation_match_id
            ),
            '[]'::jsonb
        ) AS value
        FROM accounting_core.reconciliation_match AS reviewed_match
        LEFT JOIN accounting_core.reconciliation_approval AS approval
          ON approval.tenant_account_id = reviewed_match.tenant_account_id
         AND approval.reconciliation_run_id = reviewed_match.reconciliation_run_id
         AND approval.reconciliation_match_id = reviewed_match.reconciliation_match_id
        WHERE reviewed_match.tenant_account_id = snapshot_tenant_account_id
          AND reviewed_match.reconciliation_run_id = snapshot_reconciliation_run_id
    ),
    exception_state AS (
        SELECT COALESCE(
            jsonb_agg(
                jsonb_build_array(
                    exception.reconciliation_exception_id::text,
                    exception.exception_code,
                    exception.resolution_status_code
                )
                ORDER BY exception.reconciliation_exception_id
            ),
            '[]'::jsonb
        ) AS value
        FROM accounting_core.reconciliation_exception AS exception
        WHERE exception.tenant_account_id = snapshot_tenant_account_id
          AND exception.reconciliation_run_id = snapshot_reconciliation_run_id
    ),
    resolution_state AS (
        SELECT COALESCE(
            jsonb_agg(
                jsonb_build_array(
                    resolution.reconciliation_exception_id::text,
                    resolution.target_resolution_status_code,
                    resolution.resolution_evidence_reference,
                    resolution.resolution_evidence_hash,
                    resolution.reconciliation_exception_resolution_command_hash
                )
                ORDER BY resolution.reconciliation_exception_id
            ),
            '[]'::jsonb
        ) AS value
        FROM accounting_core.reconciliation_exception_resolution_command AS resolution
        WHERE resolution.tenant_account_id = snapshot_tenant_account_id
          AND resolution.reconciliation_run_id = snapshot_reconciliation_run_id
    ),
    source_population AS (
        SELECT jsonb_build_object(
            'balances', COALESCE((
                SELECT jsonb_agg(
                    jsonb_build_array(
                        source_balance_hash,
                        balance_sequence_number::text,
                        balance_amount::text,
                        balance_currency_code,
                        credit_debit_code
                    )
                    ORDER BY balance_sequence_number, source_balance_hash
                )
                FROM balance_rows
            ), '[]'::jsonb),
            'statement_entries', COALESCE((
                SELECT jsonb_agg(
                    jsonb_build_array(
                        source_reference,
                        entry_sequence_number::text,
                        entry_amount::text,
                        entry_currency_code,
                        credit_debit_code,
                        reversal_indicator::text,
                        source_entry_hash
                    )
                    ORDER BY entry_sequence_number, source_reference
                )
                FROM statement_entries
            ), '[]'::jsonb),
            'cash_journal_lines', COALESCE((
                SELECT jsonb_agg(
                    jsonb_build_array(
                        journal_reference,
                        accounting_date::text,
                        posted_at::text,
                        line_number::text,
                        debit_amount::text,
                        credit_amount::text,
                        transaction_currency_code
                    )
                    ORDER BY accounting_date, posted_at, journal_reference, line_number
                )
                FROM journal_lines
            ), '[]'::jsonb),
            'statement_allocations', COALESCE((
                SELECT jsonb_agg(
                    jsonb_build_array(source_reference, allocated_amount::text)
                    ORDER BY source_reference, allocated_amount
                )
                FROM statement_allocation_rows
            ), '[]'::jsonb),
            'journal_allocations', COALESCE((
                SELECT jsonb_agg(
                    jsonb_build_array(source_reference, allocated_amount::text)
                    ORDER BY source_reference, allocated_amount
                )
                FROM journal_allocation_rows
            ), '[]'::jsonb)
        ) AS value
    )
    SELECT jsonb_build_object(
               'schema_version', 1,
               'reconciliation_run_id', scope.reconciliation_run_id::text,
               'currency_code', scope.currency_code,
               'knowledge_cutoff_at', scope.knowledge_cutoff_at,
               'book_cutoff_date', scope.book_cutoff_date,
               'opening_command_hash', scope.opening_command_hash,
               'review_state', review_state.value,
               'exception_state', exception_state.value,
               'exception_resolution_state', resolution_state.value,
               'source_population', source_population.value,
               'bridge', jsonb_build_object(
                   'statement_opening_balance', bridge.statement_opening::text,
                   'statement_period_movements', bridge.statement_movements::text,
                   'statement_closing_balance', bridge.statement_closing::text,
                   'book_closing_balance', bridge.book_closing::text,
                   'outstanding_book_items', bridge.outstanding_book::text,
                   'outstanding_bank_items', bridge.outstanding_bank::text,
                   'unexplained_difference',
                       (bridge.book_closing + bridge.outstanding_book
                        - bridge.outstanding_bank - bridge.statement_closing)::text
               )
           ),
           controls.scope_count,
           controls.balance_count,
           controls.statement_entry_count,
           controls.invalid_balance_currency_count,
           controls.invalid_statement_currency_count,
           controls.invalid_journal_currency_count,
           controls.unknown_statement_allocation_count,
           controls.unknown_journal_allocation_count,
           controls.over_statement_capacity_count,
           controls.over_journal_capacity_count,
           bridge.statement_opening,
           bridge.statement_movements,
           bridge.statement_closing,
           bridge.book_closing,
           bridge.outstanding_book,
           bridge.outstanding_bank
    INTO authority_payload,
         scope_count,
         balance_count,
         statement_entry_count,
         invalid_balance_currency_count,
         invalid_statement_currency_count,
         invalid_journal_currency_count,
         unknown_statement_allocation_count,
         unknown_journal_allocation_count,
         over_statement_capacity_count,
         over_journal_capacity_count,
         statement_opening,
         statement_movements,
         statement_closing,
         book_closing,
         outstanding_book,
         outstanding_bank
    FROM scope
    CROSS JOIN controls
    CROSS JOIN bridge
    CROSS JOIN review_state
    CROSS JOIN exception_state
    CROSS JOIN resolution_state
    CROSS JOIN source_population;

    IF scope_count <> 1 OR authority_payload IS NULL THEN
        RAISE EXCEPTION
            'reconciliation lifecycle source scope must resolve exactly one run, opening command, statement, and cash account (reconciliation_lifecycle_snapshot_scope)'
            USING ERRCODE = '23514';
    END IF;

    IF balance_count <> 2 OR statement_opening IS NULL OR statement_closing IS NULL THEN
        RAISE EXCEPTION
            'reconciliation lifecycle requires exact opening and closing bank-balance evidence (reconciliation_lifecycle_bridge_mismatch)'
            USING ERRCODE = '23514';
    END IF;

    IF statement_entry_count = 0 THEN
        RAISE EXCEPTION
            'reconciliation lifecycle requires a non-empty immutable statement population (reconciliation_lifecycle_bridge_mismatch)'
            USING ERRCODE = '23514';
    END IF;

    IF invalid_balance_currency_count <> 0
       OR invalid_statement_currency_count <> 0
       OR invalid_journal_currency_count <> 0 THEN
        RAISE EXCEPTION
            'reconciliation lifecycle source populations must use the run currency (reconciliation_lifecycle_bridge_mismatch)'
            USING ERRCODE = '23514';
    END IF;

    IF unknown_statement_allocation_count <> 0
       OR unknown_journal_allocation_count <> 0
       OR over_statement_capacity_count <> 0
       OR over_journal_capacity_count <> 0 THEN
        RAISE EXCEPTION
            'reconciliation lifecycle approved allocations must conserve database-owned source capacity (reconciliation_lifecycle_bridge_mismatch)'
            USING ERRCODE = '23514';
    END IF;

    IF statement_opening + statement_movements <> statement_closing THEN
        RAISE EXCEPTION
            'reconciliation lifecycle statement opening plus movements must equal closing balance (reconciliation_lifecycle_bridge_mismatch)'
            USING ERRCODE = '23514';
    END IF;

    IF book_closing + outstanding_book - outstanding_bank <> statement_closing THEN
        RAISE EXCEPTION
            'reconciliation lifecycle database-owned book-to-bank bridge does not tie exactly (reconciliation_lifecycle_bridge_mismatch)'
            USING ERRCODE = '23514';
    END IF;

    RETURN 'sha256:' || encode(
        sha256(
            convert_to(
                'reconciliation_run_database_snapshot:v1|' || authority_payload::text,
                'UTF8'
            )
        ),
        'hex'
    );
END;
$$;

CREATE OR REPLACE FUNCTION accounting_core.assign_reconciliation_run_database_snapshot()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.reconciliation_snapshot_hash :=
        accounting_core.reconciliation_run_database_snapshot_hash(
            NEW.tenant_account_id,
            NEW.reconciliation_run_id
        );
    RETURN NEW;
END;
$$;

-- PostgreSQL executes same-kind triggers in name order. This authority trigger
-- sorts before the existing command-identity/hash guards, so the transition
-- command hash commits the database-derived snapshot rather than caller bytes.
CREATE TRIGGER accounting_reconciliation_transition_authority_snapshot_guard
    BEFORE INSERT ON accounting_core.reconciliation_run_transition_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.assign_reconciliation_run_database_snapshot();

COMMIT;
