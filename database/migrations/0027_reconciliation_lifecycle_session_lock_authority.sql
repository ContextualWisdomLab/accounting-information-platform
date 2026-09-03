BEGIN;

-- A backend-held advisory lock by itself does not prove that a fresh authority
-- transaction began after the lock was granted: session and transaction locks
-- share the same advisory-lock key space in pg_locks. Retain a small database-
-- owned lease witness from the preliminary lock-acquisition transaction so the
-- transition trigger can require both the exact session lock and a prior
-- committed transaction boundary before any authority population is read.
CREATE TABLE accounting_core.reconciliation_lifecycle_session_lease (
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    backend_process_id integer NOT NULL CHECK (backend_process_id > 0),
    backend_started_at timestamptz NOT NULL,
    lease_transaction_id xid8 NOT NULL,
    lease_recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant_account_id, reconciliation_run_id),
    FOREIGN KEY (tenant_account_id, reconciliation_run_id)
        REFERENCES accounting_core.reconciliation_run (
            tenant_account_id,
            reconciliation_run_id
        )
);

CREATE INDEX reconciliation_lifecycle_session_lease_backend_index
    ON accounting_core.reconciliation_lifecycle_session_lease (
        backend_process_id,
        backend_started_at
    );

ALTER TABLE accounting_core.reconciliation_lifecycle_session_lease
    ENABLE ROW LEVEL SECURITY;
CREATE POLICY reconciliation_lifecycle_session_lease_isolation
    ON accounting_core.reconciliation_lifecycle_session_lease
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
REVOKE ALL ON accounting_core.reconciliation_lifecycle_session_lease FROM PUBLIC;

CREATE OR REPLACE FUNCTION accounting_core.acquire_reconciliation_lifecycle_session_lease(
    authority_tenant_reference text,
    authority_reconciliation_run_id uuid
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, accounting_core
AS $$
DECLARE
    authority_tenant_account_id uuid;
    caller_tenant_account_id uuid;
    caller_is_superuser boolean;
    lifecycle_scope text;
    backend_started_at timestamptz;
BEGIN
    SELECT tenant.tenant_account_id
    INTO authority_tenant_account_id
    FROM accounting_core.tenant_account AS tenant
    WHERE tenant.tenant_account_code = authority_tenant_reference;

    IF authority_tenant_account_id IS NULL
       OR NOT EXISTS (
           SELECT 1
           FROM accounting_core.reconciliation_run AS run_record
           WHERE run_record.tenant_account_id = authority_tenant_account_id
             AND run_record.reconciliation_run_id = authority_reconciliation_run_id
       ) THEN
        RAISE EXCEPTION
            'reconciliation lifecycle lease requires a recorded tenant/run (reconciliation_lifecycle_session_lock_scope)'
            USING ERRCODE = '23514';
    END IF;

    SELECT accounting_core.current_tenant_account_id()
    INTO caller_tenant_account_id;
    SELECT role_record.rolsuper
    INTO caller_is_superuser
    FROM pg_catalog.pg_roles AS role_record
    WHERE role_record.rolname = session_user;

    IF caller_tenant_account_id IS DISTINCT FROM authority_tenant_account_id
       AND NOT COALESCE(caller_is_superuser, false) THEN
        RAISE EXCEPTION
            'reconciliation lifecycle lease tenant is not authorized for this database login (reconciliation_lifecycle_session_lock_tenant)'
            USING ERRCODE = '42501';
    END IF;

    lifecycle_scope := 'reconciliation_run_lifecycle:' || authority_reconciliation_run_id::text;
    PERFORM pg_catalog.pg_advisory_lock(
        pg_catalog.hashtext(authority_tenant_reference),
        pg_catalog.hashtext(lifecycle_scope)
    );

    BEGIN
        SELECT activity.backend_start
        INTO backend_started_at
        FROM pg_catalog.pg_stat_activity AS activity
        WHERE activity.pid = pg_catalog.pg_backend_pid();

        IF backend_started_at IS NULL THEN
            RAISE EXCEPTION
                'reconciliation lifecycle backend identity is unavailable (reconciliation_lifecycle_session_lock_backend)'
                USING ERRCODE = '55000';
        END IF;

        INSERT INTO accounting_core.reconciliation_lifecycle_session_lease (
            tenant_account_id,
            reconciliation_run_id,
            backend_process_id,
            backend_started_at,
            lease_transaction_id,
            lease_recorded_at
        )
        VALUES (
            authority_tenant_account_id,
            authority_reconciliation_run_id,
            pg_catalog.pg_backend_pid(),
            backend_started_at,
            pg_catalog.pg_current_xact_id(),
            pg_catalog.clock_timestamp()
        )
        ON CONFLICT (tenant_account_id, reconciliation_run_id)
        DO UPDATE
        SET backend_process_id = EXCLUDED.backend_process_id,
            backend_started_at = EXCLUDED.backend_started_at,
            lease_transaction_id = EXCLUDED.lease_transaction_id,
            lease_recorded_at = EXCLUDED.lease_recorded_at;
    EXCEPTION WHEN OTHERS THEN
        PERFORM pg_catalog.pg_advisory_unlock(
            pg_catalog.hashtext(authority_tenant_reference),
            pg_catalog.hashtext(lifecycle_scope)
        );
        RAISE;
    END;
END;
$$;

CREATE OR REPLACE FUNCTION accounting_core.release_reconciliation_lifecycle_session_lease(
    authority_tenant_reference text,
    authority_reconciliation_run_id uuid
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, accounting_core
AS $$
DECLARE
    authority_tenant_account_id uuid;
    lifecycle_scope text;
    backend_started_at timestamptz;
    unlocked boolean;
BEGIN
    SELECT tenant.tenant_account_id
    INTO authority_tenant_account_id
    FROM accounting_core.tenant_account AS tenant
    WHERE tenant.tenant_account_code = authority_tenant_reference;

    lifecycle_scope := 'reconciliation_run_lifecycle:' || authority_reconciliation_run_id::text;
    SELECT activity.backend_start
    INTO backend_started_at
    FROM pg_catalog.pg_stat_activity AS activity
    WHERE activity.pid = pg_catalog.pg_backend_pid();

    DELETE FROM accounting_core.reconciliation_lifecycle_session_lease AS lease
    WHERE lease.tenant_account_id = authority_tenant_account_id
      AND lease.reconciliation_run_id = authority_reconciliation_run_id
      AND lease.backend_process_id = pg_catalog.pg_backend_pid()
      AND lease.backend_started_at = backend_started_at;

    SELECT pg_catalog.pg_advisory_unlock(
        pg_catalog.hashtext(authority_tenant_reference),
        pg_catalog.hashtext(lifecycle_scope)
    )
    INTO unlocked;
    RETURN unlocked;
END;
$$;

REVOKE ALL ON FUNCTION accounting_core.acquire_reconciliation_lifecycle_session_lease(text, uuid)
    FROM PUBLIC;
REVOKE ALL ON FUNCTION accounting_core.release_reconciliation_lifecycle_session_lease(text, uuid)
    FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting_core.acquire_reconciliation_lifecycle_session_lease(text, uuid)
    TO PUBLIC;
GRANT EXECUTE ON FUNCTION accounting_core.release_reconciliation_lifecycle_session_lease(text, uuid)
    TO PUBLIC;

CREATE OR REPLACE FUNCTION accounting_core.require_reconciliation_lifecycle_session_lock()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, accounting_core
AS $$
DECLARE
    tenant_reference text;
    lifecycle_scope text;
    tenant_lock_key bigint;
    lifecycle_lock_key bigint;
    backend_started_at timestamptz;
    lease_transaction_id xid8;
BEGIN
    SELECT tenant.tenant_account_code
    INTO tenant_reference
    FROM accounting_core.tenant_account AS tenant
    WHERE tenant.tenant_account_id = NEW.tenant_account_id;

    IF tenant_reference IS NULL THEN
        RAISE EXCEPTION
            'reconciliation lifecycle tenant is not recorded (reconciliation_lifecycle_session_lock_scope)'
            USING ERRCODE = '23514';
    END IF;

    lifecycle_scope := 'reconciliation_run_lifecycle:' || NEW.reconciliation_run_id::text;
    tenant_lock_key := pg_catalog.hashtext(tenant_reference)::bigint & 4294967295::bigint;
    lifecycle_lock_key := pg_catalog.hashtext(lifecycle_scope)::bigint & 4294967295::bigint;

    SELECT activity.backend_start
    INTO backend_started_at
    FROM pg_catalog.pg_stat_activity AS activity
    WHERE activity.pid = pg_catalog.pg_backend_pid();

    SELECT lease.lease_transaction_id
    INTO lease_transaction_id
    FROM accounting_core.reconciliation_lifecycle_session_lease AS lease
    WHERE lease.tenant_account_id = NEW.tenant_account_id
      AND lease.reconciliation_run_id = NEW.reconciliation_run_id
      AND lease.backend_process_id = pg_catalog.pg_backend_pid()
      AND lease.backend_started_at = backend_started_at;

    IF lease_transaction_id IS NULL
       OR lease_transaction_id = pg_catalog.pg_current_xact_id()
       OR NOT EXISTS (
           SELECT 1
           FROM pg_catalog.pg_locks AS held_lock
           WHERE held_lock.locktype = 'advisory'
             AND held_lock.database = (
                 SELECT database_row.oid
                 FROM pg_catalog.pg_database AS database_row
                 WHERE database_row.datname = pg_catalog.current_database()
             )
             AND held_lock.pid = pg_catalog.pg_backend_pid()
             AND held_lock.mode = 'ExclusiveLock'
             AND held_lock.granted
             AND held_lock.objsubid = 2
             AND held_lock.classid::bigint = tenant_lock_key
             AND held_lock.objid::bigint = lifecycle_lock_key
       ) THEN
        RAISE EXCEPTION
            'reconciliation lifecycle authority requires a committed tenant/run session-lock lease before the transition statement begins (reconciliation_lifecycle_session_lock_required)'
            USING ERRCODE = '55000';
    END IF;

    RETURN NEW;
END;
$$;

-- PostgreSQL fires same-kind triggers in name order. This prerequisite sorts
-- before the database snapshot authority guard so no statement/book/review query
-- can run until the backend proves both a prior committed lease transaction and
-- the still-held exact tenant/run advisory lock.
CREATE TRIGGER accounting_reconciliation_transition_000_session_lock_guard
    BEFORE INSERT ON accounting_core.reconciliation_run_transition_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.require_reconciliation_lifecycle_session_lock();

COMMIT;
