BEGIN;

-- Direct open-to-close transitions need a freshness witness for journals that
-- commit after a close transaction has established its REPEATABLE READ snapshot.
-- A single per-period revision row would serialize the high-volume posting path,
-- so ordinary open-period journals version one of 64 pre-existing fence rows.
CREATE TABLE accounting_core.period_journal_population_fence (
    tenant_account_id uuid NOT NULL,
    accounting_book_id uuid NOT NULL,
    fiscal_period_id uuid NOT NULL,
    fence_slot smallint NOT NULL
        CHECK (fence_slot >= 0 AND fence_slot < 64),
    journal_population_revision bigint NOT NULL DEFAULT 0
        CHECK (journal_population_revision >= 0),
    PRIMARY KEY (
        tenant_account_id,
        accounting_book_id,
        fiscal_period_id,
        fence_slot
    ),
    FOREIGN KEY (
        tenant_account_id,
        accounting_book_id,
        fiscal_period_id
    ) REFERENCES accounting_core.accounting_book_period_control (
        tenant_account_id,
        accounting_book_id,
        fiscal_period_id
    )
);

REVOKE ALL ON accounting_core.period_journal_population_fence FROM PUBLIC;

-- Fence rows must pre-date any REPEATABLE READ close snapshot. Creating a fence
-- lazily after a stale snapshot would let the close miss the new row entirely.
-- Seed the migration-owned backfill before FORCE RLS so a non-superuser schema
-- owner can initialize every tenant without borrowing one runtime tenant scope.
INSERT INTO accounting_core.period_journal_population_fence (
    tenant_account_id,
    accounting_book_id,
    fiscal_period_id,
    fence_slot
)
SELECT period_control.tenant_account_id,
       period_control.accounting_book_id,
       period_control.fiscal_period_id,
       generated_slot.fence_slot::smallint
FROM accounting_core.accounting_book_period_control AS period_control
CROSS JOIN generate_series(0, 63) AS generated_slot(fence_slot);

ALTER TABLE accounting_core.period_journal_population_fence ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.period_journal_population_fence FORCE ROW LEVEL SECURITY;
CREATE POLICY period_journal_population_fence_isolation
    ON accounting_core.period_journal_population_fence
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

CREATE OR REPLACE FUNCTION accounting_core.seed_period_journal_population_fence()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    effective_role_bypasses_rls boolean;
BEGIN
    -- SECURITY DEFINER changes current_user to the function owner. RLS bypass is
    -- evaluated for that effective role, while current_tenant_account_id() uses
    -- the original session_user binding. Do not make this explicit guard stricter
    -- than PostgreSQL itself for superuser/BYPASSRLS migration operators.
    SELECT role.rolsuper OR role.rolbypassrls
      INTO effective_role_bypasses_rls
      FROM pg_catalog.pg_roles AS role
     WHERE role.rolname = current_user;

    -- Runtime seeding runs while the fence table is FORCE RLS protected and
    -- therefore needs the same authenticated tenant identity as the control row
    -- whenever the effective function owner cannot bypass RLS. Migration 0034
    -- temporarily removes FORCE RLS for its owner backfill; keep that repair path
    -- distinct instead of minting a synthetic runtime tenant binding.
    IF COALESCE(
        (
            SELECT relation.relforcerowsecurity
            FROM pg_catalog.pg_class AS relation
            JOIN pg_catalog.pg_namespace AS namespace
              ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = 'accounting_core'
              AND relation.relname = 'period_journal_population_fence'
        ),
        TRUE
    )
       AND NOT COALESCE(effective_role_bypasses_rls, FALSE)
       AND accounting_core.current_tenant_account_id()
           IS DISTINCT FROM NEW.tenant_account_id
    THEN
        RAISE EXCEPTION
            'runtime tenant binding must match journal-population fence seed scope (period_journal_population_fence_tenant_binding_required)'
            USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO accounting_core.period_journal_population_fence (
        tenant_account_id,
        accounting_book_id,
        fiscal_period_id,
        fence_slot
    )
    SELECT NEW.tenant_account_id,
           NEW.accounting_book_id,
           NEW.fiscal_period_id,
           generated_slot.fence_slot::smallint
    FROM generate_series(0, 63) AS generated_slot(fence_slot)
    ON CONFLICT (
        tenant_account_id,
        accounting_book_id,
        fiscal_period_id,
        fence_slot
    ) DO NOTHING;

    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION accounting_core.seed_period_journal_population_fence()
    FROM PUBLIC;

CREATE TRIGGER period_journal_population_fence_seed
    AFTER INSERT
    ON accounting_core.accounting_book_period_control
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.seed_period_journal_population_fence();

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
    fence_slot_value smallint;
    affected_fence_rows integer;
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
        SELECT accounting_book_period_control.period_status_code
          INTO locked_period_status_value
          FROM accounting_core.accounting_book_period_control
         WHERE accounting_book_period_control.tenant_account_id = NEW.tenant_account_id
           AND accounting_book_period_control.accounting_book_id = NEW.accounting_book_id
           AND accounting_book_period_control.fiscal_period_id = NEW.fiscal_period_id
         FOR SHARE;

        IF locked_period_status_value IS DISTINCT FROM 'open' THEN
            RAISE EXCEPTION
                'accounting book fiscal period changed during journal admission (period_state_changed_retry)'
                USING ERRCODE = 'serialization_failure';
        END IF;

        -- UUID identity supplies a stable, caller-independent distribution key.
        -- Only journals choosing the same slot contend with each other; the
        -- authoritative transition later inspects every pre-existing slot.
        fence_slot_value := (
            get_byte(uuid_send(NEW.general_journal_id), 15) % 64
        )::smallint;
        UPDATE accounting_core.period_journal_population_fence
           SET journal_population_revision = journal_population_revision + 1
         WHERE tenant_account_id = NEW.tenant_account_id
           AND accounting_book_id = NEW.accounting_book_id
           AND fiscal_period_id = NEW.fiscal_period_id
           AND fence_slot = fence_slot_value;
        GET DIAGNOSTICS affected_fence_rows = ROW_COUNT;

        IF affected_fence_rows <> 1 THEN
            RAISE EXCEPTION
                'open-period journal population fence is incomplete (period_journal_population_fence_missing)'
                USING ERRCODE = 'check_violation';
        END IF;

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

-- A state transition must observe every stripe under FOR UPDATE. If an open
-- journal committed to any stripe after this REPEATABLE READ transaction's
-- snapshot was fixed, PostgreSQL raises SQLSTATE 40001 rather than allowing a
-- close receipt or snapshot derived from a stale journal population to commit.
CREATE OR REPLACE FUNCTION accounting_core.guard_period_state_transition_freshness()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    locked_fence_rows integer;
BEGIN
    IF current_setting('transaction_isolation')
       NOT IN ('repeatable read', 'serializable')
    THEN
        RAISE EXCEPTION
            'period state transition requires repeatable read or serializable isolation (period_close_isolation_required)'
            USING ERRCODE = 'check_violation';
    END IF;

    PERFORM period_fence.fence_slot
      FROM accounting_core.period_journal_population_fence AS period_fence
     WHERE period_fence.tenant_account_id = NEW.tenant_account_id
       AND period_fence.accounting_book_id = NEW.accounting_book_id
       AND period_fence.fiscal_period_id = NEW.fiscal_period_id
     ORDER BY period_fence.fence_slot
     FOR UPDATE;

    GET DIAGNOSTICS locked_fence_rows = ROW_COUNT;
    IF locked_fence_rows <> 64 THEN
        RAISE EXCEPTION
            'period journal population fence is incomplete for close transition (period_journal_population_fence_missing)'
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION accounting_core.guard_period_state_transition_freshness()
    FROM PUBLIC;

CREATE TRIGGER period_state_transition_population_fence
    BEFORE UPDATE OF period_status_code
    ON accounting_core.accounting_book_period_control
    FOR EACH ROW
    WHEN (OLD.period_status_code IS DISTINCT FROM NEW.period_status_code)
    EXECUTE FUNCTION accounting_core.guard_period_state_transition_freshness();

-- Preserve the purpose-limited snapshot writer while restoring the supported
-- direct open-to-hard-close command. Both open and soft-closed snapshots require
-- the exact tenant/book/period close advisory lock. The caller-controlled
-- journal_write_role GUC remains journal-admission context only and cannot mint
-- retained close evidence by itself.
CREATE OR REPLACE FUNCTION accounting_reporting.guard_trial_balance_snapshot_insert()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    period_status_value text;
    book_legal_entity_id uuid;
    book_reporting_currency_code text;
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

    SELECT accounting_book.legal_entity_id,
           accounting_book.reporting_currency_code
      INTO book_legal_entity_id,
           book_reporting_currency_code
      FROM accounting_core.accounting_book
     WHERE accounting_book.tenant_account_id = NEW.tenant_account_id
       AND accounting_book.accounting_book_id = NEW.accounting_book_id;

    IF book_legal_entity_id IS NOT NULL
       AND book_legal_entity_id IS DISTINCT FROM NEW.legal_entity_id THEN
        RAISE EXCEPTION
            'trial balance snapshot legal entity must own the accounting book (trial_balance_snapshot_book_entity_mismatch)'
            USING ERRCODE = 'check_violation';
    END IF;

    IF book_reporting_currency_code IS NOT NULL
       AND book_reporting_currency_code IS DISTINCT FROM NEW.snapshot_currency_code THEN
        RAISE EXCEPTION
            'trial balance snapshot currency must match the accounting book reporting currency (trial_balance_snapshot_currency_mismatch)'
            USING ERRCODE = 'check_violation';
    END IF;

    IF period_status_value = 'hard_closed' THEN
        RAISE EXCEPTION
            'hard-close trial balance evidence is immutable (trial_balance_snapshot_immutable)'
            USING ERRCODE = 'check_violation';
    END IF;

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

    IF period_status_value NOT IN ('open', 'soft_closed')
       OR NOT pg_has_role(session_user, 'accounting_closing_writer', 'MEMBER')
       OR NOT close_command_lock_held
    THEN
        RAISE EXCEPTION
            'trial balance snapshot creation requires the purpose-limited hard-close writer (trial_balance_snapshot_authority_required)'
            USING ERRCODE = 'check_violation';
    END IF;

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

COMMIT;
