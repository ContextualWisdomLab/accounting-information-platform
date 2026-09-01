BEGIN;

-- Evidence-backed reconciliation-run completion.
--
-- A run may not become `reconciled` through a naked status UPDATE. The application
-- derives its completion snapshot from immutable statement/book populations and
-- reviewed reconciliation evidence, persists one immutable command, and only then
-- changes status. The run-scoped advisory lock serializes completion against new
-- candidate, match, and exception facts.

CREATE TABLE accounting_core.reconciliation_run_completion_command (
    reconciliation_run_completion_command_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    completion_idempotency_key text NOT NULL CHECK (btrim(completion_idempotency_key) <> ''),
    prior_run_status_code text NOT NULL
        CHECK (prior_run_status_code IN ('evaluating', 'review_required')),
    completion_snapshot_hash text NOT NULL
        CHECK (completion_snapshot_hash ~ '^sha256:[0-9a-f]{64}$'),
    statement_population_reference text NOT NULL
        CHECK (statement_population_reference ~ '^sha256:[0-9a-f]{64}$'),
    book_population_reference text NOT NULL
        CHECK (book_population_reference ~ '^sha256:[0-9a-f]{64}$'),
    completed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, reconciliation_run_id)
        REFERENCES accounting_core.reconciliation_run (
            tenant_account_id,
            reconciliation_run_id
        ),
    UNIQUE (tenant_account_id, completion_idempotency_key),
    UNIQUE (tenant_account_id, reconciliation_run_id)
);

CREATE INDEX reconciliation_run_completion_recorded_index
    ON accounting_core.reconciliation_run_completion_command (
        tenant_account_id,
        completed_at,
        reconciliation_run_completion_command_id
    );

CREATE OR REPLACE FUNCTION accounting_core.lock_reconciliation_run_lifecycle(
    p_tenant_account_id uuid,
    p_reconciliation_run_id uuid
)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            'reconciliation_run_lifecycle:'
            || p_tenant_account_id::text
            || ':'
            || p_reconciliation_run_id::text,
            0
        )
    );
END;
$$;

CREATE OR REPLACE FUNCTION accounting_core.guard_reconciliation_completion_command()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    current_status text;
BEGIN
    PERFORM accounting_core.lock_reconciliation_run_lifecycle(
        NEW.tenant_account_id,
        NEW.reconciliation_run_id
    );

    SELECT run_status_code
    INTO current_status
    FROM accounting_core.reconciliation_run
    WHERE tenant_account_id = NEW.tenant_account_id
      AND reconciliation_run_id = NEW.reconciliation_run_id
    FOR UPDATE;

    IF current_status IS NULL THEN
        RAISE EXCEPTION
            'reconciliation run is missing for completion command (reconciliation_completion_run_missing)'
            USING ERRCODE = '23514';
    END IF;
    IF current_status NOT IN ('evaluating', 'review_required')
       OR NEW.prior_run_status_code <> current_status THEN
        RAISE EXCEPTION
            'completion command must bind the current evaluating or review_required run status (reconciliation_completion_status_conflict)'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_exception
        WHERE tenant_account_id = NEW.tenant_account_id
          AND reconciliation_run_id = NEW.reconciliation_run_id
          AND resolution_status_code = 'open'
    ) THEN
        RAISE EXCEPTION
            'open reconciliation exceptions block completion (reconciliation_completion_open_exception)'
            USING ERRCODE = '23514';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_match
        WHERE tenant_account_id = NEW.tenant_account_id
          AND reconciliation_run_id = NEW.reconciliation_run_id
          AND match_status_code = 'proposed'
    ) THEN
        RAISE EXCEPTION
            'proposed reconciliation matches block completion (reconciliation_completion_unreviewed_match)'
            USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_match
        WHERE tenant_account_id = NEW.tenant_account_id
          AND reconciliation_run_id = NEW.reconciliation_run_id
          AND match_status_code = 'approved'
    ) THEN
        RAISE EXCEPTION
            'at least one approved reconciliation match is required for completion (reconciliation_completion_approval_missing)'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER reconciliation_run_completion_command_guard
    BEFORE INSERT
    ON accounting_core.reconciliation_run_completion_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_reconciliation_completion_command();

CREATE OR REPLACE FUNCTION accounting_core.reject_reconciliation_completion_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'reconciliation completion command evidence is immutable (reconciliation_completion_immutable)'
        USING ERRCODE = '23514';
END;
$$;

CREATE TRIGGER reconciliation_run_completion_immutable_guard
    BEFORE UPDATE OR DELETE
    ON accounting_core.reconciliation_run_completion_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.reject_reconciliation_completion_mutation();

CREATE OR REPLACE FUNCTION accounting_core.guard_reconciliation_run_status_transition()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    command_count bigint;
BEGIN
    IF NEW.run_status_code IS NOT DISTINCT FROM OLD.run_status_code THEN
        RETURN NEW;
    END IF;

    PERFORM accounting_core.lock_reconciliation_run_lifecycle(
        OLD.tenant_account_id,
        OLD.reconciliation_run_id
    );

    IF OLD.run_status_code = 'reconciled' THEN
        RAISE EXCEPTION
            'reconciled run status is immutable; supersede with a new run instead (reconciliation_status_immutable)'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.run_status_code = 'reconciled' THEN
        IF OLD.run_status_code NOT IN ('evaluating', 'review_required') THEN
            RAISE EXCEPTION
                'reconciled transition requires evaluating or review_required source state (reconciliation_status_transition_invalid)'
                USING ERRCODE = '23514';
        END IF;
        SELECT COUNT(*)
        INTO command_count
        FROM accounting_core.reconciliation_run_completion_command
        WHERE tenant_account_id = OLD.tenant_account_id
          AND reconciliation_run_id = OLD.reconciliation_run_id
          AND prior_run_status_code = OLD.run_status_code;
        IF command_count <> 1 THEN
            RAISE EXCEPTION
                'reconciled transition requires exactly one immutable completion command (reconciliation_completion_command_required)'
                USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER reconciliation_run_status_transition_guard
    BEFORE UPDATE OF run_status_code
    ON accounting_core.reconciliation_run
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_reconciliation_run_status_transition();

CREATE OR REPLACE FUNCTION accounting_core.guard_reconciled_run_evidence_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    guarded_tenant_account_id uuid;
    guarded_reconciliation_run_id uuid;
    current_status text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        guarded_tenant_account_id := OLD.tenant_account_id;
        guarded_reconciliation_run_id := OLD.reconciliation_run_id;
    ELSE
        guarded_tenant_account_id := NEW.tenant_account_id;
        guarded_reconciliation_run_id := NEW.reconciliation_run_id;
    END IF;

    PERFORM accounting_core.lock_reconciliation_run_lifecycle(
        guarded_tenant_account_id,
        guarded_reconciliation_run_id
    );
    SELECT run_status_code
    INTO current_status
    FROM accounting_core.reconciliation_run
    WHERE tenant_account_id = guarded_tenant_account_id
      AND reconciliation_run_id = guarded_reconciliation_run_id;
    IF current_status = 'reconciled' THEN
        RAISE EXCEPTION
            'reconciled run decision evidence is immutable; create a new run instead (reconciliation_decision_evidence_immutable)'
            USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER reconciliation_candidate_reconciled_run_guard
    BEFORE INSERT OR UPDATE OR DELETE
    ON accounting_core.reconciliation_candidate
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_reconciled_run_evidence_mutation();

CREATE TRIGGER reconciliation_match_reconciled_run_guard
    BEFORE INSERT OR UPDATE OR DELETE
    ON accounting_core.reconciliation_match
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_reconciled_run_evidence_mutation();

CREATE TRIGGER reconciliation_exception_reconciled_run_guard
    BEFORE INSERT OR UPDATE OR DELETE
    ON accounting_core.reconciliation_exception
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_reconciled_run_evidence_mutation();

CREATE OR REPLACE FUNCTION accounting_core.record_reconciliation_completion_evidence()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO accounting_core.reconciliation_evidence (
        tenant_account_id,
        reconciliation_run_id,
        evidence_type_code,
        evidence_reference,
        evidence_payload_hash,
        effective_at
    )
    VALUES (
        NEW.tenant_account_id,
        NEW.reconciliation_run_id,
        'reconciliation_run_completion',
        NEW.reconciliation_run_completion_command_id::text,
        NEW.completion_snapshot_hash,
        NEW.completed_at
    );
    RETURN NEW;
END;
$$;

CREATE TRIGGER reconciliation_run_completion_evidence_insert
    AFTER INSERT
    ON accounting_core.reconciliation_run_completion_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.record_reconciliation_completion_evidence();

ALTER TABLE accounting_core.reconciliation_run_completion_command ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.reconciliation_run_completion_command FORCE ROW LEVEL SECURITY;
CREATE POLICY reconciliation_run_completion_isolation
    ON accounting_core.reconciliation_run_completion_command
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

REVOKE ALL ON accounting_core.reconciliation_run_completion_command FROM PUBLIC;

COMMIT;
