BEGIN;

-- The unique index was built concurrently in migration 0029. Attaching it as a
-- constraint is a short metadata operation and makes the physical invariant part
-- of the table contract without rebuilding the index under a long write-blocking lock.
ALTER TABLE accounting_reporting.trial_balance_snapshot
    ADD CONSTRAINT trial_balance_snapshot_one_population_per_book_period
    UNIQUE USING INDEX trial_balance_snapshot_one_population_per_book_period;

-- Retained trial-balance values are accounting evidence, not three independently
-- writable amounts. Add the arithmetic invariant without holding a long validation
-- scan under the initial ALTER TABLE lock, then validate all inherited rows before
-- this migration can commit.
ALTER TABLE accounting_reporting.trial_balance_line
    ADD CONSTRAINT trial_balance_line_net_balance_conservation
    CHECK (net_balance_amount = debit_total_amount - credit_total_amount)
    NOT VALID;
ALTER TABLE accounting_reporting.trial_balance_line
    VALIDATE CONSTRAINT trial_balance_line_net_balance_conservation;

CREATE OR REPLACE FUNCTION accounting_reporting.reject_trial_balance_snapshot_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'hard-close trial balance evidence is immutable (trial_balance_snapshot_immutable)'
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE TRIGGER trial_balance_snapshot_immutable_guard
    BEFORE UPDATE OR DELETE
    ON accounting_reporting.trial_balance_snapshot
    FOR EACH ROW
    EXECUTE FUNCTION accounting_reporting.reject_trial_balance_snapshot_mutation();

CREATE OR REPLACE FUNCTION accounting_reporting.reject_trial_balance_line_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'hard-close trial balance evidence is immutable (trial_balance_snapshot_immutable)'
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE TRIGGER trial_balance_line_immutable_guard
    BEFORE UPDATE OR DELETE
    ON accounting_reporting.trial_balance_line
    FOR EACH ROW
    EXECUTE FUNCTION accounting_reporting.reject_trial_balance_line_mutation();

CREATE OR REPLACE FUNCTION accounting_reporting.guard_trial_balance_snapshot_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    period_status_value text;
    book_legal_entity_id uuid;
    journal_write_role_value text;
    close_command_lock_held boolean;
BEGIN
    SELECT accounting_book_period_control.period_status_code
      INTO period_status_value
      FROM accounting_core.accounting_book_period_control
     WHERE accounting_book_period_control.tenant_account_id = NEW.tenant_account_id
       AND accounting_book_period_control.accounting_book_id = NEW.accounting_book_id
       AND accounting_book_period_control.fiscal_period_id = NEW.fiscal_period_id
     FOR UPDATE;

    IF period_status_value IS NULL THEN
        RAISE EXCEPTION
            'trial balance snapshot has no matching book-period authority (trial_balance_snapshot_scope_missing)'
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT accounting_book.legal_entity_id
      INTO book_legal_entity_id
      FROM accounting_core.accounting_book
     WHERE accounting_book.tenant_account_id = NEW.tenant_account_id
       AND accounting_book.accounting_book_id = NEW.accounting_book_id;

    IF book_legal_entity_id IS NOT NULL
       AND book_legal_entity_id IS DISTINCT FROM NEW.legal_entity_id THEN
        RAISE EXCEPTION
            'trial balance snapshot legal entity must own the accounting book (trial_balance_snapshot_book_entity_mismatch)'
            USING ERRCODE = 'check_violation';
    END IF;

    IF period_status_value = 'hard_closed' THEN
        RAISE EXCEPTION
            'hard-close trial balance evidence is immutable (trial_balance_snapshot_immutable)'
            USING ERRCODE = 'check_violation';
    END IF;

    journal_write_role_value := nullif(
        current_setting('accounting_core.journal_write_role', true),
        ''
    );

    -- The hard-close command always acquires this tenant/book/period transaction
    -- advisory lock before assembling close evidence. The lock remains present even
    -- when zero net revenue/expense means no period-closing journal is emitted, so
    -- snapshot admission must not depend on an optional journal INSERT side effect.
    SELECT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_locks AS held_lock
          JOIN accounting_core.tenant_account
            ON tenant_account.tenant_account_id = NEW.tenant_account_id
          JOIN accounting_core.accounting_book
            ON accounting_book.tenant_account_id = NEW.tenant_account_id
           AND accounting_book.accounting_book_id = NEW.accounting_book_id
          JOIN accounting_core.fiscal_period
            ON fiscal_period.tenant_account_id = NEW.tenant_account_id
           AND fiscal_period.fiscal_period_id = NEW.fiscal_period_id
         WHERE held_lock.locktype = 'advisory'
           AND held_lock.pid = pg_backend_pid()
           AND held_lock.database = (
                SELECT pg_database.oid
                  FROM pg_catalog.pg_database
                 WHERE pg_database.datname = current_database()
           )
           AND held_lock.mode = 'ExclusiveLock'
           AND held_lock.granted
           AND held_lock.objsubid = 2
           AND held_lock.classid::bigint = (
                hashtext(tenant_account.tenant_account_code)::bigint & 4294967295
           )
           AND held_lock.objid::bigint = (
                hashtext(
                    'period:' || accounting_book.accounting_book_id::text || ':' || fiscal_period.period_code
                )::bigint & 4294967295
           )
    ) INTO close_command_lock_held;

    IF period_status_value <> 'soft_closed'
       OR NOT pg_has_role(session_user, 'accounting_closing_writer', 'MEMBER')
       OR (
            journal_write_role_value IS DISTINCT FROM 'period_closing'
            AND NOT close_command_lock_held
       )
    THEN
        RAISE EXCEPTION
            'trial balance snapshot creation requires the purpose-limited hard-close writer (trial_balance_snapshot_authority_required)'
            USING ERRCODE = 'check_violation';
    END IF;

    -- Snapshot chronology is an AIS system-time fact. A purpose-limited closing
    -- writer may supply accounting evidence but cannot select the recording clock.
    NEW.snapshot_generated_at := clock_timestamp();

    IF EXISTS (
        SELECT 1
          FROM accounting_reporting.trial_balance_snapshot
         WHERE trial_balance_snapshot.tenant_account_id = NEW.tenant_account_id
           AND trial_balance_snapshot.accounting_book_id = NEW.accounting_book_id
           AND trial_balance_snapshot.fiscal_period_id = NEW.fiscal_period_id
    ) THEN
        RAISE EXCEPTION
            'trial balance snapshot population already occupies this book-period (trial_balance_snapshot_population_conflict)'
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION accounting_reporting.guard_trial_balance_snapshot_insert()
    FROM PUBLIC;

CREATE TRIGGER trial_balance_snapshot_population_guard
    BEFORE INSERT
    ON accounting_reporting.trial_balance_snapshot
    FOR EACH ROW
    EXECUTE FUNCTION accounting_reporting.guard_trial_balance_snapshot_insert();

CREATE OR REPLACE FUNCTION accounting_reporting.guard_trial_balance_line_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    period_status_value text;
    snapshot_book_id uuid;
    chart_account_book_id uuid;
BEGIN
    SELECT accounting_book_period_control.period_status_code,
           trial_balance_snapshot.accounting_book_id
      INTO period_status_value, snapshot_book_id
      FROM accounting_reporting.trial_balance_snapshot
      JOIN accounting_core.accounting_book_period_control
        ON accounting_book_period_control.tenant_account_id
           = trial_balance_snapshot.tenant_account_id
       AND accounting_book_period_control.accounting_book_id
           = trial_balance_snapshot.accounting_book_id
       AND accounting_book_period_control.fiscal_period_id
           = trial_balance_snapshot.fiscal_period_id
     WHERE trial_balance_snapshot.tenant_account_id = NEW.tenant_account_id
       AND trial_balance_snapshot.trial_balance_snapshot_id
           = NEW.trial_balance_snapshot_id
     FOR UPDATE OF accounting_book_period_control;

    IF period_status_value IS NULL THEN
        RAISE EXCEPTION
            'trial balance snapshot has no matching book-period authority (trial_balance_snapshot_scope_missing)'
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT chart_account.accounting_book_id
      INTO chart_account_book_id
      FROM accounting_core.chart_account
     WHERE chart_account.tenant_account_id = NEW.tenant_account_id
       AND chart_account.chart_account_id = NEW.chart_account_id;

    IF chart_account_book_id IS NOT NULL
       AND snapshot_book_id IS DISTINCT FROM chart_account_book_id THEN
        RAISE EXCEPTION
            'trial balance line chart account must belong to the snapshot accounting book (trial_balance_line_book_scope_mismatch)'
            USING ERRCODE = 'check_violation';
    END IF;

    IF period_status_value = 'hard_closed' THEN
        RAISE EXCEPTION
            'hard-close trial balance evidence is immutable (trial_balance_snapshot_immutable)'
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION accounting_reporting.guard_trial_balance_line_insert()
    FROM PUBLIC;

CREATE TRIGGER trial_balance_line_population_guard
    BEFORE INSERT
    ON accounting_reporting.trial_balance_line
    FOR EACH ROW
    EXECUTE FUNCTION accounting_reporting.guard_trial_balance_line_insert();

COMMIT;