BEGIN;

-- Every admitted journal changes the authoritative population for one book-period.
-- Materialize that change on the same control row that hard close later locks. Under
-- REPEATABLE READ, a close transaction whose snapshot predates a committed journal
-- then fails closed with a serialization error instead of freezing stale evidence.
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
    journal_write_role_value text;
BEGIN
    UPDATE accounting_core.accounting_book_period_control
       SET journal_population_revision = journal_population_revision + 1
     WHERE tenant_account_id = NEW.tenant_account_id
       AND accounting_book_id = NEW.accounting_book_id
       AND fiscal_period_id = NEW.fiscal_period_id
     RETURNING period_status_code
          INTO period_status_value;

    IF period_status_value IS NULL THEN
        RAISE EXCEPTION
            'accounting book fiscal period control is missing for this journal insert (period_control_missing)'
            USING ERRCODE = 'check_violation';
    END IF;

    IF period_status_value = 'open' THEN
        RETURN NEW;
    END IF;

    journal_write_role_value := nullif(
        current_setting('accounting_core.journal_write_role', true),
        ''
    );

    IF period_status_value = 'soft_closed'
       AND journal_write_role_value IN ('period_closing', 'adjusting', 'reversal')
       AND pg_has_role(session_user, 'accounting_closing_writer', 'MEMBER')
    THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION
        'Accounting book fiscal period is % (period_closed). Ordinary journals cannot be inserted after close. Post the AIS closing journal before hard-close; do not insert a later ordinary or reversal journal into a locked book period.',
        period_status_value
        USING ERRCODE = 'check_violation';
END;
$$;

REVOKE ALL ON FUNCTION accounting_core.guard_period_insert() FROM PUBLIC;

COMMIT;
