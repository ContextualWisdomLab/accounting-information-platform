BEGIN;

-- The supported lifecycle command acquires the tenant/run session advisory lock,
-- commits that acquisition, opens a fresh REPEATABLE READ transaction, and then
-- reacquires the same key as a transaction advisory lock before any authority
-- reads. The direct table boundary must at least prove that the backend still
-- owns both lock forms and is in REPEATABLE READ before database-authority
-- population queries can run. A transaction lock by itself is not session-lock
-- evidence because session and transaction advisory locks share one key space.
CREATE OR REPLACE FUNCTION accounting_core.require_reconciliation_lifecycle_session_lock()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    tenant_reference text;
    lifecycle_scope text;
    tenant_lock_key bigint;
    lifecycle_lock_key bigint;
    session_lock_owned boolean;
    transaction_lock_still_owned boolean;
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
    tenant_lock_key := hashtext(tenant_reference)::bigint & 4294967295::bigint;
    lifecycle_lock_key := hashtext(lifecycle_scope)::bigint & 4294967295::bigint;

    -- pg_advisory_unlock releases only a session-level advisory lock. Probe one
    -- session hold, while the required transaction-level hold prevents another
    -- backend from entering the key between this probe and the immediate
    -- re-acquisition below.
    SELECT pg_advisory_unlock(hashtext(tenant_reference), hashtext(lifecycle_scope))
    INTO session_lock_owned;

    IF NOT session_lock_owned THEN
        RAISE EXCEPTION
            'reconciliation lifecycle authority requires the tenant/run session advisory lock before the transition statement begins (reconciliation_lifecycle_session_lock_required)'
            USING ERRCODE = '55000';
    END IF;

    SELECT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_locks AS held_lock
        WHERE held_lock.locktype = 'advisory'
          AND held_lock.database = (
              SELECT database_row.oid
              FROM pg_catalog.pg_database AS database_row
              WHERE database_row.datname = current_database()
          )
          AND held_lock.pid = pg_backend_pid()
          AND held_lock.mode = 'ExclusiveLock'
          AND held_lock.granted
          AND held_lock.objsubid = 2
          AND held_lock.classid::bigint = tenant_lock_key
          AND held_lock.objid::bigint = lifecycle_lock_key
    )
    INTO transaction_lock_still_owned;

    -- Restore the session hold before either returning or rejecting. When the
    -- transaction lock is present this re-acquisition is reentrant and cannot
    -- create an inter-backend window on the lifecycle key.
    PERFORM pg_advisory_lock(hashtext(tenant_reference), hashtext(lifecycle_scope));

    IF NOT transaction_lock_still_owned
       OR current_setting('transaction_isolation') <> 'repeatable read' THEN
        RAISE EXCEPTION
            'reconciliation lifecycle authority requires the fresh REPEATABLE READ transaction lock after the session lock (reconciliation_lifecycle_session_lock_required)'
            USING ERRCODE = '55000';
    END IF;

    RETURN NEW;
END;
$$;

-- PostgreSQL fires same-kind triggers in name order. This prerequisite sorts
-- before the database snapshot authority guard so no statement/book/review query
-- can run until the backend proves the session lock plus the fresh authority
-- transaction's matching transaction lock.
CREATE TRIGGER accounting_reconciliation_transition_000_session_lock_guard
    BEFORE INSERT ON accounting_core.reconciliation_run_transition_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.require_reconciliation_lifecycle_session_lock();

COMMIT;
