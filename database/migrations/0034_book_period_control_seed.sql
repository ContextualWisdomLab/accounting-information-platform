BEGIN;

-- Migration 0033 requires the tenant/book/period authority row and all 64
-- journal-population fence rows to exist before ordinary posting or close
-- evaluation. 0009 backfilled only rows that existed at migration time, so
-- later fiscal periods or accounting books could otherwise reach the journal
-- guard without a materialized book-period authority.
--
-- fiscal_period.period_status_code is only the tenant/calendar compatibility
-- projection after book-scoped close control exists. A newly created book or a
-- newly inserted non-open period has no retained per-book close evidence from
-- which soft_closed/hard_closed could be derived. Automatic seeding therefore
-- creates controls only for periods that are actually open and always starts
-- the new book-period authority as open. Missing controls for non-open periods
-- fail closed until an explicit book-period lifecycle can establish authority;
-- the compatibility projection is never copied into authoritative close state.
--
-- The two AFTER INSERT triggers also form one cross-product invariant. Without
-- a pre-existing common version witness, concurrent transactions can each
-- insert one side, scan before the other side commits, and leave the new
-- book-period pair absent. Both seeders therefore perform a non-key UPDATE of
-- the existing tenant row before scanning the peer population. Under READ
-- COMMITTED, the later seeder waits and its following statement sees the peer
-- commit. Under REPEATABLE READ/SERIALIZABLE, a transaction whose snapshot
-- predates the competing tenant-row version fails closed with serialization
-- failure and must retry from a fresh transaction. Updating only created_at to
-- its retained value changes no tenant business fact. PostgreSQL acquires the
-- weaker FOR NO KEY UPDATE row lock for this non-key update, so unrelated child
-- foreign-key checks using FOR KEY SHARE remain compatible.
CREATE OR REPLACE FUNCTION accounting_core.seed_book_period_control_for_period()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF NEW.period_status_code IS DISTINCT FROM 'open' THEN
        RETURN NEW;
    END IF;

    UPDATE accounting_core.tenant_account AS tenant
    SET created_at = tenant.created_at
    WHERE tenant.tenant_account_id = NEW.tenant_account_id;

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
           'open',
           NULL
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

    UPDATE accounting_core.tenant_account AS tenant
    SET created_at = tenant.created_at
    WHERE tenant.tenant_account_id = NEW.tenant_account_id;

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
           'open',
           NULL
    FROM accounting_core.fiscal_period
    WHERE fiscal_period.tenant_account_id = NEW.tenant_account_id
      AND fiscal_period.period_status_code = 'open'
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
-- accounting_book/fiscal_period as well as the control/fence targets are
-- already FORCE RLS protected at this point. An unbound NOSUPERUSER /
-- NOBYPASSRLS migration owner would otherwise see no source rows and could not
-- populate the targets. NO FORCE restores only ordinary table-owner bypass;
-- RLS remains enabled for non-owner roles. The same transaction restores FORCE
-- on every source/target table before commit. ALTER TABLE's locking also keeps
-- concurrent runtime traffic from observing a partially changed owner policy.
--
-- This repair fills only missing open controls. A missing non-open book-period
-- pair cannot be reconstructed from fiscal_period's compatibility projection,
-- because that projection is not retained per-book close evidence.
ALTER TABLE accounting_core.accounting_book NO FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.fiscal_period NO FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.accounting_book_period_control NO FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.period_journal_population_fence NO FORCE ROW LEVEL SECURITY;

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
       'open',
       NULL
FROM accounting_core.accounting_book
JOIN accounting_core.fiscal_period
  ON fiscal_period.tenant_account_id = accounting_book.tenant_account_id
WHERE accounting_book.valid_to IS NULL
  AND fiscal_period.period_status_code = 'open'
ON CONFLICT (
    tenant_account_id,
    accounting_book_id,
    fiscal_period_id
) DO NOTHING;

ALTER TABLE accounting_core.period_journal_population_fence FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.accounting_book_period_control FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.fiscal_period FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.accounting_book FORCE ROW LEVEL SECURITY;

-- After the one-time repair above, new book-period authority may be created only
-- by the two canonical master-data seed triggers. A direct application or SQL
-- INSERT must not reconstruct authority from fiscal_period compatibility state.
-- pg_trigger_depth() is structural rather than a caller-controlled custom GUC:
-- the control-table trigger runs at depth 2 when invoked by either canonical
-- AFTER INSERT seeder and at depth 1 for a direct control-table INSERT. Returning
-- NULL leaves an unsupported direct write unapplied; the close path then reads
-- the still-missing control and fails with its domain validation error. This
-- keeps the database single-writer boundary intact without granting a mutable
-- session flag that another writer could spoof.
CREATE OR REPLACE FUNCTION accounting_core.guard_book_period_control_insert_authority()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF pg_trigger_depth() < 2
       OR NEW.period_status_code IS DISTINCT FROM 'open'
       OR NEW.period_closed_at IS NOT NULL
    THEN
        RETURN NULL;
    END IF;

    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION accounting_core.guard_book_period_control_insert_authority()
    FROM PUBLIC;

CREATE TRIGGER book_period_control_insert_authority_guard
    BEFORE INSERT
    ON accounting_core.accounting_book_period_control
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_book_period_control_insert_authority();

COMMIT;
