BEGIN;

-- The supported lifecycle command must acquire the tenant/run session advisory
-- lock in a transaction that ends before the authority-bearing REPEATABLE READ
-- transaction begins. Live lock ownership alone cannot prove that ordering: a
-- backend can establish a stale repeatable-read snapshot, wait for the session
-- lock, and then hold both lock forms while still reading the predecessor
-- snapshot. Persist a backend/session lease in the lock-acquisition transaction
-- so the transition trigger can prove that its authority transaction is a
-- different, later transaction.
CREATE TABLE accounting_core.reconciliation_lifecycle_session_lease (
    backend_pid integer NOT NULL,
    backend_start timestamptz NOT NULL,
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    acquisition_transaction_id xid8 NOT NULL,
    acquired_at timestamptz NOT NULL,
    PRIMARY KEY (
        backend_pid,
        backend_start,
        tenant_account_id,
        reconciliation_run_id
    ),
    FOREIGN KEY (tenant_account_id, reconciliation_run_id)
        REFERENCES accounting_core.reconciliation_run (
            tenant_account_id,
            reconciliation_run_id
        )
);

REVOKE ALL ON accounting_core.reconciliation_lifecycle_session_lease FROM PUBLIC;

CREATE OR REPLACE FUNCTION accounting_core.acquire_reconciliation_lifecycle_session(
    tenant_reference_input text,
    reconciliation_run_id_input uuid
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, accounting_core
AS $$
DECLARE
    tenant_id uuid;
    lifecycle_scope text;
    current_backend_start timestamptz;
    existing_lease boolean;
    existing_session_lock boolean;
BEGIN
    SELECT tenant.tenant_account_id
    INTO tenant_id
    FROM accounting_core.tenant_account AS tenant
    WHERE tenant.tenant_account_code = tenant_reference_input;

    IF tenant_id IS NULL OR NOT EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_run AS run
        WHERE run.tenant_account_id = tenant_id
          AND run.reconciliation_run_id = reconciliation_run_id_input
    ) THEN
        RAISE EXCEPTION
            'reconciliation lifecycle tenant/run is not recorded (reconciliation_lifecycle_session_lock_scope)'
            USING ERRCODE = '23514';
    END IF;

    lifecycle_scope :=
        'reconciliation_run_lifecycle:' || reconciliation_run_id_input::text;

    SELECT activity.backend_start
    INTO current_backend_start
    FROM pg_catalog.pg_stat_activity AS activity
    WHERE activity.pid = pg_backend_pid();

    IF current_backend_start IS NULL THEN
        RAISE EXCEPTION
            'reconciliation lifecycle backend identity is unavailable (reconciliation_lifecycle_session_lock_scope)'
            USING ERRCODE = '55000';
    END IF;

    -- Clean leases left by disconnected backends. Session locks themselves are
    -- released automatically at disconnect; backend_start prevents PID reuse
    -- from inheriting stale lease authority before this cleanup runs.
    DELETE FROM accounting_core.reconciliation_lifecycle_session_lease AS lease
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_stat_activity AS activity
        WHERE activity.pid = lease.backend_pid
          AND activity.backend_start = lease.backend_start
    );

    SELECT EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_lifecycle_session_lease AS lease
        WHERE lease.backend_pid = pg_backend_pid()
          AND lease.backend_start = current_backend_start
          AND lease.tenant_account_id = tenant_id
          AND lease.reconciliation_run_id = reconciliation_run_id_input
    )
    INTO existing_lease;

    -- Session advisory locks stack on repeated acquisition by the same backend.
    -- Take the matching transaction lock first, then normalize this backend's
    -- session hold to exactly one while the xact lock prevents any other backend
    -- from entering the lifecycle key during the brief targeted unlock/relock.
    -- If the backend still held a previously leased session lock, keep the older
    -- committed lease so a retry cannot move the freshness boundary forward.
    -- If the lock had been released (or no lease existed), record this transaction
    -- as a new acquisition so a stale snapshot in this same transaction fails the
    -- transition prerequisite.
    PERFORM pg_advisory_xact_lock(
        hashtext(tenant_reference_input),
        hashtext(lifecycle_scope)
    );

    SELECT pg_advisory_unlock(
        hashtext(tenant_reference_input),
        hashtext(lifecycle_scope)
    )
    INTO existing_session_lock;

    IF existing_session_lock THEN
        WHILE pg_advisory_unlock(
            hashtext(tenant_reference_input),
            hashtext(lifecycle_scope)
        ) LOOP
            NULL;
        END LOOP;
    END IF;

    PERFORM pg_advisory_lock(
        hashtext(tenant_reference_input),
        hashtext(lifecycle_scope)
    );

    IF NOT (existing_lease AND existing_session_lock) THEN
        INSERT INTO accounting_core.reconciliation_lifecycle_session_lease (
            backend_pid,
            backend_start,
            tenant_account_id,
            reconciliation_run_id,
            acquisition_transaction_id,
            acquired_at
        )
        VALUES (
            pg_backend_pid(),
            current_backend_start,
            tenant_id,
            reconciliation_run_id_input,
            pg_current_xact_id(),
            clock_timestamp()
        )
        ON CONFLICT (
            backend_pid,
            backend_start,
            tenant_account_id,
            reconciliation_run_id
        ) DO UPDATE
        SET acquisition_transaction_id = EXCLUDED.acquisition_transaction_id,
            acquired_at = EXCLUDED.acquired_at;
    END IF;
END;
$$;

-- SECURITY DEFINER functions receive PUBLIC EXECUTE by default. Revoke in the
-- creation transaction so read-only/schema-usage roles cannot acquire a
-- tenant/run authority lock as an unintended denial-of-service capability.
REVOKE ALL ON FUNCTION accounting_core.acquire_reconciliation_lifecycle_session(text, uuid)
    FROM PUBLIC;

CREATE OR REPLACE FUNCTION accounting_core.release_reconciliation_lifecycle_session(
    tenant_reference_input text,
    reconciliation_run_id_input uuid
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, accounting_core
AS $$
DECLARE
    tenant_id uuid;
    lifecycle_scope text;
    current_backend_start timestamptz;
BEGIN
    SELECT tenant.tenant_account_id
    INTO tenant_id
    FROM accounting_core.tenant_account AS tenant
    WHERE tenant.tenant_account_code = tenant_reference_input;

    lifecycle_scope :=
        'reconciliation_run_lifecycle:' || reconciliation_run_id_input::text;

    SELECT activity.backend_start
    INTO current_backend_start
    FROM pg_catalog.pg_stat_activity AS activity
    WHERE activity.pid = pg_backend_pid();

    IF tenant_id IS NOT NULL AND current_backend_start IS NOT NULL THEN
        DELETE FROM accounting_core.reconciliation_lifecycle_session_lease AS lease
        WHERE lease.backend_pid = pg_backend_pid()
          AND lease.backend_start = current_backend_start
          AND lease.tenant_account_id = tenant_id
          AND lease.reconciliation_run_id = reconciliation_run_id_input;
    END IF;

    RETURN pg_advisory_unlock(
        hashtext(tenant_reference_input),
        hashtext(lifecycle_scope)
    );
END;
$$;

REVOKE ALL ON FUNCTION accounting_core.release_reconciliation_lifecycle_session(text, uuid)
    FROM PUBLIC;

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
    session_lock_owned boolean;
    transaction_lock_still_owned boolean;
    current_backend_start timestamptz;
    lease_transaction_id xid8;
    lease_acquired_at timestamptz;
    current_transaction_id xid8;
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

    SELECT activity.backend_start
    INTO current_backend_start
    FROM pg_catalog.pg_stat_activity AS activity
    WHERE activity.pid = pg_backend_pid();

    SELECT lease.acquisition_transaction_id,
           lease.acquired_at
    INTO lease_transaction_id,
         lease_acquired_at
    FROM accounting_core.reconciliation_lifecycle_session_lease AS lease
    WHERE lease.backend_pid = pg_backend_pid()
      AND lease.backend_start = current_backend_start
      AND lease.tenant_account_id = NEW.tenant_account_id
      AND lease.reconciliation_run_id = NEW.reconciliation_run_id;

    current_transaction_id := pg_current_xact_id();

    IF lease_transaction_id IS NULL
       OR lease_transaction_id = current_transaction_id
       OR lease_acquired_at > transaction_timestamp() THEN
        RAISE EXCEPTION
            'reconciliation lifecycle authority requires session-lock acquisition to commit before a fresh REPEATABLE READ authority transaction begins (reconciliation_lifecycle_fresh_transaction_required)'
            USING ERRCODE = '55000';
    END IF;

    RETURN NEW;
END;
$$;

-- PostgreSQL fires same-kind triggers in name order. This prerequisite sorts
-- before the database snapshot authority guard so no statement/book/review query
-- can run until the backend proves the session lock, the fresh transaction lease,
-- and the matching transaction lock.
CREATE TRIGGER accounting_reconciliation_transition_000_session_lock_guard
    BEFORE INSERT ON accounting_core.reconciliation_run_transition_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.require_reconciliation_lifecycle_session_lock();

COMMIT;
