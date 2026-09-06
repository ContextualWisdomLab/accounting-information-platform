BEGIN;

-- Journals admitted while a period is soft-closed change the authoritative
-- population that hard close is about to freeze. Version those close-window
-- writes on the same control row that hard close later locks. Ordinary open-
-- period posting must not UPDATE that row on every journal: concurrent open
-- posting instead holds a shared row lock so a period-state transition cannot
-- overtake a journal that was admitted under the open-state contract.
ALTER TABLE accounting_core.accounting_book_period_control
    ADD COLUMN journal_population_revision bigint NOT NULL DEFAULT 0
        CHECK (journal_population_revision >= 0);

CREATE OR REPLACE FUNCTION accounting_core.guard_period_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    period_status_value text;
    locked_period_status_value text;
    journal_write_role_value text;
BEGIN
    SELECT accounting_book_period_control.period_status_code
      INTO period_status_value
      FROM accounting_core.accounting_book_period_control
     WHERE accounting_book_period_control.tenant_account_id = NEW.tenant_account_id
       AND accounting_book_period_control.accounting_book_id = NEW.accounting_book_id
       AND accounting_book_period_control.fiscal_period_id = NEW.fiscal_period_id;

    IF period_status_value IS NULL THEN
        RAISE EXCEPTION
            'accounting book fiscal period control is missing for this journal insert (period_control_missing)'
            USING ERRCODE = 'check_violation';
    END IF;

    IF period_status_value = 'open' THEN
        -- Many ordinary postings may share this row lock concurrently. A soft-close
        -- UPDATE must wait for all journals admitted as open-period work to commit.
        -- If the period changed while this statement waited, retry from a fresh
        -- transaction rather than silently applying open-period authority to a
        -- soft-closed period.
        SELECT accounting_book_period_control.period_status_code
          INTO locked_period_status_value
          FROM accounting_core.accounting_book_period_control
         WHERE accounting_book_period_control.tenant_account_id = NEW.tenant_account_id
           AND accounting_book_period_control.accounting_book_id = NEW.accounting_book_id
           AND accounting_book_period_control.fiscal_period_id = NEW.fiscal_period_id
         FOR SHARE;

        IF locked_period_status_value = 'open' THEN
            RETURN NEW;
        END IF;

        RAISE EXCEPTION
            'accounting book fiscal period changed during journal admission (period_state_changed_retry)'
            USING ERRCODE = 'serialization_failure';
    END IF;

    journal_write_role_value := nullif(
        current_setting('accounting_core.journal_write_role', true),
        ''
    );

    IF period_status_value = 'soft_closed'
       AND journal_write_role_value IN ('period_closing', 'adjusting', 'reversal')
       AND pg_has_role(session_user, 'accounting_closing_writer', 'MEMBER')
    THEN
        -- Close-window journals are intentionally serialized on this revision.
        -- If hard close already won the row, PostgreSQL either raises a serialization
        -- failure under REPEATABLE READ or this predicate stops matching after wait.
        locked_period_status_value := NULL;
        UPDATE accounting_core.accounting_book_period_control
           SET journal_population_revision = journal_population_revision + 1
         WHERE tenant_account_id = NEW.tenant_account_id
           AND accounting_book_id = NEW.accounting_book_id
           AND fiscal_period_id = NEW.fiscal_period_id
           AND period_status_code = 'soft_closed'
         RETURNING period_status_code
              INTO locked_period_status_value;

        IF locked_period_status_value = 'soft_closed' THEN
            RETURN NEW;
        END IF;

        RAISE EXCEPTION
            'accounting book fiscal period changed during close-window journal admission (period_state_changed_retry)'
            USING ERRCODE = 'serialization_failure';
    END IF;

    RAISE EXCEPTION
        'Accounting book fiscal period is % (period_closed). Ordinary journals cannot be inserted after close. Post the AIS closing journal before hard-close; do not insert a later ordinary or reversal journal into a locked book period.',
        period_status_value
        USING ERRCODE = 'check_violation';
END;
$$;

REVOKE ALL ON FUNCTION accounting_core.guard_period_insert() FROM PUBLIC;

COMMIT;
