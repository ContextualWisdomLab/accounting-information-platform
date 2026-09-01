BEGIN;

-- Owner-controlled evidence for the first lawful transition of a reconciliation
-- run to `reconciled`. The command is immutable and tenant scoped. It does not
-- post, reverse, close a period, or alter accounting policy.

DO $role_setup$
BEGIN
    IF to_regrole('accounting_reconciliation_completer') IS NULL THEN
        CREATE ROLE accounting_reconciliation_completer NOLOGIN;
    END IF;
END
$role_setup$;

-- Reassert NOLOGIN on every migration run so an accidentally pre-created login
-- cannot become a database authority shortcut. Deployment grants membership to
-- a purpose-limited application identity; the application cannot SET ROLE itself.
ALTER ROLE accounting_reconciliation_completer NOLOGIN;

CREATE TABLE accounting_core.reconciliation_completion_command (
    reconciliation_completion_command_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    reconciliation_completion_key text NOT NULL
        CHECK (btrim(reconciliation_completion_key) <> ''),
    completion_command_hash text NOT NULL
        CHECK (completion_command_hash ~ '^sha256:[0-9a-f]{64}$'),
    statement_population_hash text NOT NULL
        CHECK (statement_population_hash ~ '^sha256:[0-9a-f]{64}$'),
    book_population_hash text NOT NULL
        CHECK (book_population_hash ~ '^sha256:[0-9a-f]{64}$'),
    approval_population_hash text NOT NULL
        CHECK (approval_population_hash ~ '^sha256:[0-9a-f]{64}$'),
    bridge_evidence_hash text NOT NULL
        CHECK (bridge_evidence_hash ~ '^sha256:[0-9a-f]{64}$'),
    actor_reference text NOT NULL
        CHECK (btrim(actor_reference) <> ''),
    completion_purpose_code text NOT NULL
        CHECK (completion_purpose_code = 'reconciliation_close_review'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, reconciliation_run_id)
        REFERENCES accounting_core.reconciliation_run (
            tenant_account_id,
            reconciliation_run_id
        ),
    UNIQUE (tenant_account_id, reconciliation_completion_key),
    UNIQUE (tenant_account_id, reconciliation_run_id),
    UNIQUE (tenant_account_id, reconciliation_completion_command_id)
);

CREATE INDEX reconciliation_completion_run_index
    ON accounting_core.reconciliation_completion_command (
        tenant_account_id,
        reconciliation_run_id,
        recorded_at,
        reconciliation_completion_command_id
    );

ALTER TABLE accounting_core.reconciliation_completion_command ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.reconciliation_completion_command FORCE ROW LEVEL SECURITY;

CREATE POLICY reconciliation_completion_command_isolation
    ON accounting_core.reconciliation_completion_command
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

REVOKE ALL ON accounting_core.reconciliation_completion_command FROM PUBLIC;
GRANT USAGE ON SCHEMA accounting_core, accounting_integration
    TO accounting_reconciliation_completer;
GRANT INSERT, SELECT ON accounting_core.reconciliation_completion_command
    TO accounting_reconciliation_completer;
GRANT UPDATE (run_status_code) ON accounting_core.reconciliation_run
    TO accounting_reconciliation_completer;
GRANT INSERT ON accounting_integration.outbox_event
    TO accounting_reconciliation_completer;

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_completion_scope_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    current_run_status_code text;
BEGIN
    IF NOT pg_has_role(
        session_user,
        'accounting_reconciliation_completer',
        'MEMBER'
    ) THEN
        RAISE EXCEPTION
            'reconciliation completion requires purpose-limited database role membership (reconciliation_completion_role_required)'
            USING ERRCODE = '42501';
    END IF;

    SELECT run_record.run_status_code
    INTO current_run_status_code
    FROM accounting_core.reconciliation_run AS run_record
    WHERE run_record.tenant_account_id = NEW.tenant_account_id
      AND run_record.reconciliation_run_id = NEW.reconciliation_run_id
    FOR UPDATE OF run_record;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'reconciliation completion run is outside the tenant scope (reconciliation_completion_scope_mismatch)'
            USING ERRCODE = '23514';
    END IF;

    IF current_run_status_code NOT IN ('evaluating', 'review_required') THEN
        RAISE EXCEPTION
            'reconciliation completion requires an evaluating or review_required run (reconciliation_completion_invalid_state)'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_exception AS exception_record
        WHERE exception_record.tenant_account_id = NEW.tenant_account_id
          AND exception_record.reconciliation_run_id = NEW.reconciliation_run_id
          AND exception_record.resolution_status_code = 'open'
    ) THEN
        RAISE EXCEPTION
            'reconciliation completion requires every open exception to be resolved or superseded (reconciliation_completion_open_exception)'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_match AS match_record
        WHERE match_record.tenant_account_id = NEW.tenant_account_id
          AND match_record.reconciliation_run_id = NEW.reconciliation_run_id
          AND match_record.match_status_code = 'proposed'
    ) THEN
        RAISE EXCEPTION
            'reconciliation completion requires every proposed match to be reviewed (reconciliation_completion_pending_match)'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER reconciliation_completion_scope_guard
BEFORE INSERT ON accounting_core.reconciliation_completion_command
FOR EACH ROW EXECUTE FUNCTION accounting_core.reconciliation_completion_scope_guard();

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_completion_command_immutability_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'recorded reconciliation completion commands are immutable (reconciliation_completion_immutable)'
        USING ERRCODE = '23514';
END;
$$;

CREATE TRIGGER reconciliation_completion_command_immutability_guard
BEFORE UPDATE OR DELETE ON accounting_core.reconciliation_completion_command
FOR EACH ROW EXECUTE FUNCTION accounting_core.reconciliation_completion_command_immutability_guard();

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_run_reconciled_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.run_status_code IS NOT DISTINCT FROM OLD.run_status_code THEN
        RETURN NEW;
    END IF;

    IF NEW.run_status_code = 'reconciled' THEN
        IF NOT pg_has_role(
            session_user,
            'accounting_reconciliation_completer',
            'MEMBER'
        ) THEN
            RAISE EXCEPTION
                'reconciliation transition requires purpose-limited database role membership (reconciliation_completion_role_required)'
                USING ERRCODE = '42501';
        END IF;

        IF OLD.run_status_code NOT IN ('evaluating', 'review_required') THEN
            RAISE EXCEPTION
                'reconciliation run may enter reconciled only from evaluating or review_required (reconciliation_run_invalid_transition)'
                USING ERRCODE = '23514';
        END IF;

        IF NOT EXISTS (
            SELECT 1
            FROM accounting_core.reconciliation_completion_command AS completion_command
            WHERE completion_command.tenant_account_id = NEW.tenant_account_id
              AND completion_command.reconciliation_run_id = NEW.reconciliation_run_id
        ) THEN
            RAISE EXCEPTION
                'reconciliation run requires immutable completion command evidence before reconciled (reconciliation_completion_required)'
                USING ERRCODE = '23514';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM accounting_core.reconciliation_exception AS exception_record
            WHERE exception_record.tenant_account_id = NEW.tenant_account_id
              AND exception_record.reconciliation_run_id = NEW.reconciliation_run_id
              AND exception_record.resolution_status_code = 'open'
        ) THEN
            RAISE EXCEPTION
                'reconciliation run cannot enter reconciled with an open exception (reconciliation_completion_open_exception)'
                USING ERRCODE = '23514';
        END IF;

        IF EXISTS (
            SELECT 1
            FROM accounting_core.reconciliation_match AS match_record
            WHERE match_record.tenant_account_id = NEW.tenant_account_id
              AND match_record.reconciliation_run_id = NEW.reconciliation_run_id
              AND match_record.match_status_code = 'proposed'
        ) THEN
            RAISE EXCEPTION
                'reconciliation run cannot enter reconciled with a proposed match awaiting review (reconciliation_completion_pending_match)'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER reconciliation_run_reconciled_guard
BEFORE UPDATE OF run_status_code ON accounting_core.reconciliation_run
FOR EACH ROW EXECUTE FUNCTION accounting_core.reconciliation_run_reconciled_guard();

COMMIT;
