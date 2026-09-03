BEGIN;

-- The supported lifecycle command acquires the tenant/run session advisory lock,
-- commits that acquisition, and only then opens the fresh REPEATABLE READ
-- authority transaction. A raw INSERT must not enter the database authority
-- trigger chain without the same pre-statement lock prerequisite: acquiring the
-- transaction-level advisory lock later in a BEFORE INSERT trigger cannot refresh
-- a statement or transaction snapshot that was already established.
CREATE OR REPLACE FUNCTION accounting_core.require_reconciliation_lifecycle_session_lock()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    tenant_reference text;
    lifecycle_scope text;
    tenant_lock_key bigint;
    lifecycle_lock_key bigint;
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

    IF NOT EXISTS (
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
    ) THEN
        RAISE EXCEPTION
            'reconciliation lifecycle authority requires the tenant/run session advisory lock before the transition statement begins (reconciliation_lifecycle_session_lock_required)'
            USING ERRCODE = '55000';
    END IF;

    RETURN NEW;
END;
$$;

-- PostgreSQL fires same-kind triggers in name order. This prerequisite sorts
-- before the database snapshot authority guard so no statement/book/review query
-- can run until the backend proves it already owns the exact lifecycle lock.
CREATE TRIGGER accounting_reconciliation_transition_000_session_lock_guard
    BEFORE INSERT ON accounting_core.reconciliation_run_transition_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.require_reconciliation_lifecycle_session_lock();

COMMIT;
