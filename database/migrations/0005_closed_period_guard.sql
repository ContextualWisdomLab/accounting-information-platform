BEGIN;

CREATE OR REPLACE FUNCTION accounting_core.guard_period_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    period_status_code text;
    journal_write_role text;
BEGIN
    SELECT fiscal_period.period_status_code
      INTO period_status_code
      FROM accounting_core.fiscal_period
     WHERE fiscal_period.tenant_account_id = NEW.tenant_account_id
       AND fiscal_period.fiscal_period_id = NEW.fiscal_period_id;

    IF period_status_code IS NULL THEN
        RAISE EXCEPTION
            'fiscal period is missing for this journal insert (period_closed)'
            USING ERRCODE = 'check_violation';
    END IF;

    IF period_status_code = 'open' THEN
        RETURN NEW;
    END IF;

    journal_write_role := nullif(
        current_setting('accounting_core.journal_write_role', true),
        ''
    );

    IF period_status_code = 'soft_closed'
       AND journal_write_role IN ('period_closing', 'adjusting', 'reversal')
    THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION
        'Fiscal period is % (period_closed). Ordinary journals cannot be inserted after close. Post the AIS closing journal before hard-close; do not insert a later ordinary or reversal journal into a locked period.',
        period_status_code
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE TRIGGER closed_period_guard
    BEFORE INSERT ON accounting_core.general_journal
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_period_insert();

COMMIT;
