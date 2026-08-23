BEGIN;

CREATE TABLE accounting_core.accounting_book_period_control (
    accounting_book_period_control_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    accounting_book_id uuid NOT NULL,
    fiscal_period_id uuid NOT NULL,
    period_status_code text NOT NULL
        CHECK (period_status_code IN ('open', 'soft_closed', 'hard_closed')),
    period_closed_at timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, accounting_book_id)
        REFERENCES accounting_core.accounting_book (tenant_account_id, accounting_book_id),
    FOREIGN KEY (tenant_account_id, fiscal_period_id)
        REFERENCES accounting_core.fiscal_period (tenant_account_id, fiscal_period_id),
    UNIQUE (tenant_account_id, accounting_book_id, fiscal_period_id),
    UNIQUE (tenant_account_id, accounting_book_period_control_id)
);

CREATE INDEX accounting_book_period_scope_index
    ON accounting_core.accounting_book_period_control (
        tenant_account_id, accounting_book_id, fiscal_period_id, period_status_code
    );

ALTER TABLE accounting_core.accounting_book_period_control ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.accounting_book_period_control FORCE ROW LEVEL SECURITY;
CREATE POLICY accounting_book_period_isolation
    ON accounting_core.accounting_book_period_control
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

INSERT INTO accounting_core.accounting_book_period_control (
    tenant_account_id, accounting_book_id, fiscal_period_id,
    period_status_code, period_closed_at
)
SELECT accounting_book.tenant_account_id,
       accounting_book.accounting_book_id,
       fiscal_period.fiscal_period_id,
       fiscal_period.period_status_code,
       fiscal_period.period_closed_at
FROM accounting_core.accounting_book
JOIN accounting_core.fiscal_period
  ON fiscal_period.tenant_account_id = accounting_book.tenant_account_id
WHERE accounting_book.valid_to IS NULL
ON CONFLICT (tenant_account_id, accounting_book_id, fiscal_period_id) DO NOTHING;

CREATE OR REPLACE FUNCTION accounting_core.guard_period_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, accounting_core
AS $$
DECLARE
    period_status_value text;
    journal_write_role_value text;
BEGIN
    SELECT COALESCE(
               accounting_book_period_control.period_status_code,
               fiscal_period.period_status_code
           )
      INTO period_status_value
      FROM accounting_core.fiscal_period
      LEFT JOIN accounting_core.accounting_book_period_control
        ON accounting_book_period_control.tenant_account_id
           = fiscal_period.tenant_account_id
       AND accounting_book_period_control.fiscal_period_id
           = fiscal_period.fiscal_period_id
       AND accounting_book_period_control.accounting_book_id = NEW.accounting_book_id
     WHERE fiscal_period.tenant_account_id = NEW.tenant_account_id
       AND fiscal_period.fiscal_period_id = NEW.fiscal_period_id;

    IF period_status_value IS NULL THEN
        RAISE EXCEPTION
            'fiscal period is missing for this accounting book journal insert (period_closed)'
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

CREATE OR REPLACE TRIGGER closed_period_guard
    BEFORE INSERT ON accounting_core.general_journal
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_period_insert();

COMMIT;
