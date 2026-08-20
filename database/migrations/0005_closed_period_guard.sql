BEGIN;

DO $role_setup$
BEGIN
    IF to_regrole('accounting_closing_writer') IS NULL THEN
        CREATE ROLE accounting_closing_writer NOLOGIN;
    END IF;
END
$role_setup$;

ALTER ROLE accounting_closing_writer NOLOGIN;

CREATE OR REPLACE FUNCTION accounting_core.guard_period_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    period_status_value text;
    journal_write_role_value text;
BEGIN
    SELECT fiscal_period.period_status_code
      INTO period_status_value
      FROM accounting_core.fiscal_period
     WHERE fiscal_period.tenant_account_id = NEW.tenant_account_id
       AND fiscal_period.fiscal_period_id = NEW.fiscal_period_id;

    IF period_status_value IS NULL THEN
        RAISE EXCEPTION
            'fiscal period is missing for this journal insert (period_closed)'
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
        'Fiscal period is % (period_closed). Ordinary journals cannot be inserted after close. Post the AIS closing journal before hard-close; do not insert a later ordinary or reversal journal into a locked period.',
        period_status_value
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE OR REPLACE TRIGGER closed_period_guard
    BEFORE INSERT ON accounting_core.general_journal
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_period_insert();

CREATE OR REPLACE FUNCTION accounting_core.assert_journal_balance()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    target_tenant_id uuid;
    target_journal_id uuid;
    journal_exists_value boolean;
    line_count_value bigint;
    debit_total_value numeric(38, 6);
    credit_total_value numeric(38, 6);
BEGIN
    target_tenant_id := COALESCE(NEW.tenant_account_id, OLD.tenant_account_id);
    target_journal_id := COALESCE(NEW.general_journal_id, OLD.general_journal_id);

    SELECT EXISTS (
        SELECT 1
          FROM accounting_core.general_journal
         WHERE general_journal.tenant_account_id = target_tenant_id
           AND general_journal.general_journal_id = target_journal_id
    )
      INTO journal_exists_value;

    IF NOT journal_exists_value THEN
        RETURN NULL;
    END IF;

    SELECT count(*),
           COALESCE(sum(journal_entry_line.debit_amount), 0),
           COALESCE(sum(journal_entry_line.credit_amount), 0)
      INTO line_count_value, debit_total_value, credit_total_value
      FROM accounting_core.journal_entry_line
     WHERE journal_entry_line.tenant_account_id = target_tenant_id
       AND journal_entry_line.general_journal_id = target_journal_id;

    IF line_count_value = 0 OR debit_total_value <> credit_total_value THEN
        RAISE EXCEPTION
            'journal must contain lines whose debit and credit totals are equal (journal_unbalanced)'
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS general_journal_balance_guard
    ON accounting_core.general_journal;
CREATE CONSTRAINT TRIGGER general_journal_balance_guard
    AFTER INSERT OR UPDATE ON accounting_core.general_journal
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.assert_journal_balance();

DROP TRIGGER IF EXISTS journal_entry_balance_guard
    ON accounting_core.journal_entry_line;
CREATE CONSTRAINT TRIGGER journal_entry_balance_guard
    AFTER INSERT OR UPDATE OR DELETE ON accounting_core.journal_entry_line
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.assert_journal_balance();

COMMIT;
