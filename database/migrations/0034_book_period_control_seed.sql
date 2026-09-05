BEGIN;

-- Migration 0033 requires the tenant/book/period authority row and all 64
-- journal-population fence rows to exist before ordinary posting or close
-- evaluation. 0009 backfilled only rows that existed at migration time, so
-- later fiscal periods or accounting books could otherwise reach the journal
-- guard without a materialized book-period authority.
CREATE OR REPLACE FUNCTION accounting_core.seed_book_period_control_for_period()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    INSERT INTO accounting_core.accounting_book_period_control (
        tenant_account_id,
        accounting_book_id,
        fiscal_period_id,
        period_status_code,
        period_closed_at
    )
    SELECT NEW.tenant_account_id,
           accounting_book.accounting_book_id,
           NEW.fiscal_period_id,
           NEW.period_status_code,
           NEW.period_closed_at
    FROM accounting_core.accounting_book
    WHERE accounting_book.tenant_account_id = NEW.tenant_account_id
      AND accounting_book.valid_to IS NULL
    ON CONFLICT (
        tenant_account_id,
        accounting_book_id,
        fiscal_period_id
    ) DO NOTHING;

    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION accounting_core.seed_book_period_control_for_period()
    FROM PUBLIC;

CREATE TRIGGER book_period_control_seed_for_period
    AFTER INSERT
    ON accounting_core.fiscal_period
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.seed_book_period_control_for_period();

CREATE OR REPLACE FUNCTION accounting_core.seed_book_period_control_for_book()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF NEW.valid_to IS NOT NULL THEN
        RETURN NEW;
    END IF;

    INSERT INTO accounting_core.accounting_book_period_control (
        tenant_account_id,
        accounting_book_id,
        fiscal_period_id,
        period_status_code,
        period_closed_at
    )
    SELECT NEW.tenant_account_id,
           NEW.accounting_book_id,
           fiscal_period.fiscal_period_id,
           fiscal_period.period_status_code,
           fiscal_period.period_closed_at
    FROM accounting_core.fiscal_period
    WHERE fiscal_period.tenant_account_id = NEW.tenant_account_id
    ON CONFLICT (
        tenant_account_id,
        accounting_book_id,
        fiscal_period_id
    ) DO NOTHING;

    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION accounting_core.seed_book_period_control_for_book()
    FROM PUBLIC;

CREATE TRIGGER book_period_control_seed_for_book
    AFTER INSERT
    ON accounting_core.accounting_book
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.seed_book_period_control_for_book();

-- Repair databases that installed 0009 before later master-data rows existed.
-- Each inserted control row synchronously fires migration 0033's fence seeder,
-- so no post-migration active book-period pair can lack its pre-existing stripes.
INSERT INTO accounting_core.accounting_book_period_control (
    tenant_account_id,
    accounting_book_id,
    fiscal_period_id,
    period_status_code,
    period_closed_at
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
ON CONFLICT (
    tenant_account_id,
    accounting_book_id,
    fiscal_period_id
) DO NOTHING;

COMMIT;
