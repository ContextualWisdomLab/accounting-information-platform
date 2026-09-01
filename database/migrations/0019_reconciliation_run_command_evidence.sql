BEGIN;

-- Bind the first run command to one immutable bank-statement source. The run
-- scope remains owned by reconciliation_run; this command row supplies the
-- idempotency and source-payload evidence required for opening that scope.
CREATE TABLE accounting_core.reconciliation_run_command (
    reconciliation_run_command_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    bank_statement_record_id uuid NOT NULL,
    reconciliation_idempotency_key text NOT NULL
        CHECK (btrim(reconciliation_idempotency_key) <> ''),
    reconciliation_command_hash text NOT NULL
        CHECK (reconciliation_command_hash ~ '^sha256:[0-9a-f]{64}$'),
    source_payload_hash text NOT NULL
        CHECK (source_payload_hash ~ '^sha256:[0-9a-f]{64}$'),
    source_payload_reference text NOT NULL
        CHECK (btrim(source_payload_reference) <> ''),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, reconciliation_run_id)
        REFERENCES accounting_core.reconciliation_run (
            tenant_account_id, reconciliation_run_id
        ),
    FOREIGN KEY (tenant_account_id, bank_statement_record_id)
        REFERENCES accounting_integration.bank_statement_record (
            tenant_account_id, bank_statement_record_id
        ),
    UNIQUE (tenant_account_id, reconciliation_run_command_id),
    UNIQUE (tenant_account_id, reconciliation_idempotency_key),
    UNIQUE (tenant_account_id, reconciliation_run_id)
);

-- The persisted command digest is database-owned. PostgreSQL 18 provides
-- sha256(bytea) as a core binary-string function, so no extension or caller-
-- supplied digest is trusted. The trigger overwrites any supplied hash with a
-- domain-separated digest over one deterministic jsonb command projection.
CREATE OR REPLACE FUNCTION accounting_core.assign_reconciliation_run_command_hash()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    canonical_command jsonb;
BEGIN
    SELECT jsonb_build_object(
        'accounting_book_reference', accounting_book.book_name,
        'bank_account_assignment_id', run.bank_account_assignment_id::text,
        'bank_cutoff_at', to_char(
            run.bank_cutoff_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
        ),
        'bank_statement_record_id', NEW.bank_statement_record_id::text,
        'book_cutoff_at', to_char(
            run.book_cutoff_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
        ),
        'knowledge_cutoff_at', to_char(
            run.knowledge_cutoff_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
        ),
        'legal_entity_reference', legal_entity.legal_entity_code,
        'matching_policy_version', run.matching_policy_version,
        'normalized_payload_hash', statement.normalized_payload_hash,
        'reconciliation_idempotency_key', NEW.reconciliation_idempotency_key,
        'source_payload_hash', NEW.source_payload_hash,
        'tenant_reference', tenant.tenant_account_code
    )
    INTO canonical_command
    FROM accounting_core.reconciliation_run AS run
    JOIN accounting_core.tenant_account AS tenant
      ON tenant.tenant_account_id = run.tenant_account_id
    JOIN accounting_core.legal_entity_record AS legal_entity
      ON legal_entity.tenant_account_id = run.tenant_account_id
     AND legal_entity.legal_entity_id = run.legal_entity_id
    JOIN accounting_core.accounting_book AS accounting_book
      ON accounting_book.tenant_account_id = run.tenant_account_id
     AND accounting_book.accounting_book_id = run.accounting_book_id
    JOIN accounting_integration.bank_statement_record AS statement
      ON statement.tenant_account_id = run.tenant_account_id
     AND statement.bank_statement_record_id = NEW.bank_statement_record_id
    WHERE run.tenant_account_id = NEW.tenant_account_id
      AND run.reconciliation_run_id = NEW.reconciliation_run_id;

    IF canonical_command IS NULL THEN
        RAISE EXCEPTION
            'reconciliation run command scope cannot be canonicalized (reconciliation_run_command_provenance)'
            USING ERRCODE = '23514';
    END IF;

    NEW.reconciliation_command_hash := 'sha256:' || encode(
        sha256(
            convert_to(
                'reconciliation_run_command:v1|' || canonical_command::text,
                'UTF8'
            )
        ),
        'hex'
    );
    RETURN NEW;
END;
$$;

CREATE TRIGGER reconciliation_run_command_hash_guard
    BEFORE INSERT ON accounting_core.reconciliation_run_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.assign_reconciliation_run_command_hash();

-- reconciliation_run has forced tenant RLS from migration 0013. Give this
-- install-time check transaction-scoped visibility of historical rows, then
-- remove that visibility before commit. Historical runs predate this command
-- table, so their command provenance cannot be fabricated safely: refuse the
-- upgrade and require the operator to resolve/reconstruct durable evidence.
CREATE POLICY reconciliation_run_command_upgrade_visibility
    ON accounting_core.reconciliation_run
    FOR SELECT
    TO current_user
    USING (true);

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_run_command_upgrade_guard()
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_run AS run
        WHERE NOT EXISTS (
            SELECT 1
            FROM accounting_core.reconciliation_run_command AS command
            WHERE command.tenant_account_id = run.tenant_account_id
              AND command.reconciliation_run_id = run.reconciliation_run_id
        )
    ) THEN
        RAISE EXCEPTION
            'migration 0019 requires durable command evidence for existing reconciliation runs; reconstruct retained provenance before retrying (reconciliation_run_command_upgrade_required)'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

SELECT accounting_core.reconciliation_run_command_upgrade_guard();
DROP FUNCTION accounting_core.reconciliation_run_command_upgrade_guard();
DROP POLICY reconciliation_run_command_upgrade_visibility
    ON accounting_core.reconciliation_run;

CREATE INDEX reconciliation_run_command_statement_index
    ON accounting_core.reconciliation_run_command (
        tenant_account_id,
        bank_statement_record_id,
        recorded_at,
        reconciliation_run_command_id
    );

CREATE OR REPLACE FUNCTION accounting_core.reject_reconciliation_run_command_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'reconciliation run command evidence is immutable once recorded'
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE TRIGGER reconciliation_run_command_immutable_guard
    BEFORE UPDATE OR DELETE ON accounting_core.reconciliation_run_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.reject_reconciliation_run_command_mutation();

CREATE OR REPLACE FUNCTION accounting_core.enforce_reconciliation_run_command_provenance()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    command_count integer;
BEGIN
    SELECT count(*)
    INTO command_count
    FROM accounting_core.reconciliation_run_command AS command
    WHERE command.tenant_account_id = NEW.tenant_account_id
      AND command.reconciliation_run_id = NEW.reconciliation_run_id;

    IF command_count <> 1 THEN
        RAISE EXCEPTION
            'reconciliation run must have exactly one command evidence row at commit (reconciliation_run_command_provenance)'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_run_command AS command
        JOIN accounting_integration.bank_statement_record AS statement
          ON statement.tenant_account_id = command.tenant_account_id
         AND statement.bank_statement_record_id = command.bank_statement_record_id
        JOIN accounting_core.reconciliation_run AS run
          ON run.tenant_account_id = command.tenant_account_id
         AND run.reconciliation_run_id = command.reconciliation_run_id
        JOIN accounting_core.bank_account_assignment AS assignment
          ON assignment.tenant_account_id = run.tenant_account_id
         AND assignment.legal_entity_id = run.legal_entity_id
         AND assignment.accounting_book_id = run.accounting_book_id
         AND assignment.bank_account_assignment_id = run.bank_account_assignment_id
        WHERE command.tenant_account_id = NEW.tenant_account_id
          AND command.reconciliation_run_id = NEW.reconciliation_run_id
          AND statement.bank_account_record_id IS DISTINCT FROM assignment.bank_account_record_id
    ) THEN
        RAISE EXCEPTION
            'reconciliation run command bank account provenance does not match the run assignment (reconciliation_run_command_provenance)'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_run_command AS command
        JOIN accounting_integration.bank_statement_record AS statement
          ON statement.tenant_account_id = command.tenant_account_id
         AND statement.bank_statement_record_id = command.bank_statement_record_id
        JOIN accounting_integration.bank_statement_artifact AS artifact
          ON artifact.tenant_account_id = statement.tenant_account_id
         AND artifact.bank_statement_artifact_id = statement.bank_statement_artifact_id
        WHERE command.tenant_account_id = NEW.tenant_account_id
          AND command.reconciliation_run_id = NEW.reconciliation_run_id
          AND (
              command.source_payload_hash IS DISTINCT FROM statement.source_artifact_hash
              OR statement.source_artifact_hash IS DISTINCT FROM artifact.source_artifact_hash
              OR command.source_payload_reference IS DISTINCT FROM artifact.artifact_store_reference
          )
    ) THEN
        RAISE EXCEPTION
            'reconciliation run command source payload hash does not match retained statement artifact (reconciliation_run_command_provenance)'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER reconciliation_run_command_provenance_guard
    AFTER INSERT ON accounting_core.reconciliation_run
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.enforce_reconciliation_run_command_provenance();

-- Validate provenance whenever command evidence is attached as well as when a
-- new run is created. This keeps the control effective for runs that predate
-- the command-evidence migration while preserving run-before-command ordering
-- inside one transaction.
CREATE CONSTRAINT TRIGGER reconciliation_run_command_provenance_insert_guard
    AFTER INSERT ON accounting_core.reconciliation_run_command
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.enforce_reconciliation_run_command_provenance();

ALTER TABLE accounting_core.reconciliation_run_command ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.reconciliation_run_command FORCE ROW LEVEL SECURITY;
CREATE POLICY reconciliation_run_command_isolation
    ON accounting_core.reconciliation_run_command
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

REVOKE ALL ON accounting_core.reconciliation_run_command FROM PUBLIC;

-- A reconciled status is authority-bearing close evidence. Persist a separate
-- immutable lifecycle command rather than treating a direct status UPDATE as a
-- supported owner-control path. The transition binds the exact source/review
-- snapshot digest calculated by the application from database-owned facts plus
-- actor, purpose, effective time, and idempotency evidence.
CREATE TABLE accounting_core.reconciliation_run_transition_command (
    reconciliation_run_transition_command_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    reconciliation_transition_idempotency_key text NOT NULL
        CHECK (btrim(reconciliation_transition_idempotency_key) <> ''),
    target_run_status_code text NOT NULL
        CHECK (target_run_status_code = 'reconciled'),
    reconciliation_snapshot_hash text NOT NULL
        CHECK (reconciliation_snapshot_hash ~ '^sha256:[0-9a-f]{64}$'),
    reconciliation_transition_command_hash text NOT NULL
        CHECK (reconciliation_transition_command_hash ~ '^sha256:[0-9a-f]{64}$'),
    actor_reference text NOT NULL CHECK (btrim(actor_reference) <> ''),
    purpose_code text NOT NULL CHECK (btrim(purpose_code) <> ''),
    effective_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, reconciliation_run_id)
        REFERENCES accounting_core.reconciliation_run (
            tenant_account_id, reconciliation_run_id
        ),
    UNIQUE (tenant_account_id, reconciliation_run_transition_command_id),
    UNIQUE (tenant_account_id, reconciliation_transition_idempotency_key),
    UNIQUE (tenant_account_id, reconciliation_run_id, target_run_status_code)
);

CREATE INDEX reconciliation_run_transition_recorded_index
    ON accounting_core.reconciliation_run_transition_command (
        tenant_account_id,
        reconciliation_run_id,
        recorded_at,
        reconciliation_run_transition_command_id
    );

CREATE OR REPLACE FUNCTION accounting_core.acquire_reconciliation_run_lifecycle_lock(
    lifecycle_tenant_account_id uuid,
    lifecycle_reconciliation_run_id uuid
)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    tenant_reference text;
BEGIN
    SELECT tenant_account_code
    INTO tenant_reference
    FROM accounting_core.tenant_account
    WHERE tenant_account_id = lifecycle_tenant_account_id;

    IF tenant_reference IS NULL THEN
        RAISE EXCEPTION
            'reconciliation lifecycle tenant is not recorded (reconciliation_lifecycle_scope)'
            USING ERRCODE = '23514';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtext(tenant_reference),
        hashtext('reconciliation_run_lifecycle:' || lifecycle_reconciliation_run_id::text)
    );
END;
$$;

CREATE OR REPLACE FUNCTION accounting_core.assign_reconciliation_run_transition_hash()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    current_status text;
    canonical_command jsonb;
BEGIN
    PERFORM accounting_core.acquire_reconciliation_run_lifecycle_lock(
        NEW.tenant_account_id,
        NEW.reconciliation_run_id
    );

    SELECT run.run_status_code
    INTO current_status
    FROM accounting_core.reconciliation_run AS run
    WHERE run.tenant_account_id = NEW.tenant_account_id
      AND run.reconciliation_run_id = NEW.reconciliation_run_id
    FOR UPDATE;

    IF current_status IS NULL THEN
        RAISE EXCEPTION
            'reconciliation lifecycle run is not recorded (reconciliation_lifecycle_scope)'
            USING ERRCODE = '23514';
    END IF;

    IF current_status NOT IN ('evaluating', 'review_required') THEN
        RAISE EXCEPTION
            'only evaluating or review_required reconciliation runs may transition to reconciled (reconciliation_lifecycle_state)'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_exception AS exception
        WHERE exception.tenant_account_id = NEW.tenant_account_id
          AND exception.reconciliation_run_id = NEW.reconciliation_run_id
          AND exception.resolution_status_code = 'open'
    ) THEN
        RAISE EXCEPTION
            'reconciliation run has an open exception and cannot be finalized (reconciliation_lifecycle_exception)'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_match AS reviewed_match
        WHERE reviewed_match.tenant_account_id = NEW.tenant_account_id
          AND reviewed_match.reconciliation_run_id = NEW.reconciliation_run_id
          AND reviewed_match.match_status_code = 'proposed'
    ) THEN
        RAISE EXCEPTION
            'reconciliation run has an unreviewed proposed match and cannot be finalized (reconciliation_lifecycle_review)'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_match AS reviewed_match
        LEFT JOIN accounting_core.reconciliation_approval AS approval
          ON approval.tenant_account_id = reviewed_match.tenant_account_id
         AND approval.reconciliation_run_id = reviewed_match.reconciliation_run_id
         AND approval.reconciliation_match_id = reviewed_match.reconciliation_match_id
        WHERE reviewed_match.tenant_account_id = NEW.tenant_account_id
          AND reviewed_match.reconciliation_run_id = NEW.reconciliation_run_id
          AND reviewed_match.match_status_code IN ('approved', 'rejected')
          AND (
              approval.reconciliation_approval_id IS NULL
              OR approval.approval_decision_code IS DISTINCT FROM reviewed_match.match_status_code
              OR approval.reconciliation_snapshot_hash IS DISTINCT FROM
                 accounting_core.reconciliation_match_snapshot_hash(
                     reviewed_match.tenant_account_id,
                     reviewed_match.reconciliation_run_id,
                     reviewed_match.reconciliation_match_id
                 )
          )
    ) THEN
        RAISE EXCEPTION
            'reconciliation reviewed match lacks current decision-consistent approval evidence (reconciliation_lifecycle_review)'
            USING ERRCODE = '23514';
    END IF;

    SELECT jsonb_build_object(
        'actor_reference', NEW.actor_reference,
        'effective_at', to_char(
            NEW.effective_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
        ),
        'opening_command_hash', opening_command.reconciliation_command_hash,
        'purpose_code', NEW.purpose_code,
        'reconciliation_idempotency_key', NEW.reconciliation_transition_idempotency_key,
        'reconciliation_run_id', NEW.reconciliation_run_id::text,
        'reconciliation_snapshot_hash', NEW.reconciliation_snapshot_hash,
        'target_run_status_code', NEW.target_run_status_code,
        'tenant_reference', tenant.tenant_account_code
    )
    INTO canonical_command
    FROM accounting_core.reconciliation_run_command AS opening_command
    JOIN accounting_core.tenant_account AS tenant
      ON tenant.tenant_account_id = opening_command.tenant_account_id
    WHERE opening_command.tenant_account_id = NEW.tenant_account_id
      AND opening_command.reconciliation_run_id = NEW.reconciliation_run_id;

    IF canonical_command IS NULL THEN
        RAISE EXCEPTION
            'reconciliation lifecycle opening command evidence is missing (reconciliation_lifecycle_provenance)'
            USING ERRCODE = '23514';
    END IF;

    NEW.reconciliation_transition_command_hash := 'sha256:' || encode(
        sha256(
            convert_to(
                'reconciliation_run_transition_command:v1|' || canonical_command::text,
                'UTF8'
            )
        ),
        'hex'
    );
    RETURN NEW;
END;
$$;

CREATE TRIGGER accounting_reconciliation_transition_hash_guard
    BEFORE INSERT ON accounting_core.reconciliation_run_transition_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.assign_reconciliation_run_transition_hash();

CREATE OR REPLACE FUNCTION accounting_core.reject_reconciliation_run_transition_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'reconciliation lifecycle command evidence is immutable once recorded'
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE TRIGGER reconciliation_run_transition_immutable_guard
    BEFORE UPDATE OR DELETE ON accounting_core.reconciliation_run_transition_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.reject_reconciliation_run_transition_mutation();

-- A transition command and the reconciled aggregate state are one commit-time
-- fact. A command cannot be parked on an evaluating run for a later raw status
-- update, and a status update cannot exist without its immutable command.
CREATE OR REPLACE FUNCTION accounting_core.enforce_reconciliation_transition_status_pair()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    paired_status text;
BEGIN
    SELECT run_status_code
    INTO paired_status
    FROM accounting_core.reconciliation_run
    WHERE tenant_account_id = NEW.tenant_account_id
      AND reconciliation_run_id = NEW.reconciliation_run_id;

    IF paired_status IS DISTINCT FROM 'reconciled' THEN
        RAISE EXCEPTION
            'reconciliation lifecycle command and reconciled status must commit atomically (reconciliation_lifecycle_atomic_pair)'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER reconciliation_run_transition_status_pair_guard
    AFTER INSERT ON accounting_core.reconciliation_run_transition_command
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.enforce_reconciliation_transition_status_pair();

CREATE OR REPLACE FUNCTION accounting_core.enforce_reconciliation_run_reconciled_transition()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    transition_count integer;
BEGIN
    IF NEW.run_status_code IS NOT DISTINCT FROM OLD.run_status_code THEN
        RETURN NEW;
    END IF;

    -- This migration introduces exactly one named status command: reconciliation.
    -- Other lifecycle targets must arrive with their own command evidence and a
    -- deliberate evolution of this state-machine guard; raw SQL is never that
    -- authority, including attempts to move a reconciled run away from evidence.
    IF NEW.run_status_code <> 'reconciled' THEN
        RAISE EXCEPTION
            'reconciliation run status changes require a named lifecycle command (reconciliation_lifecycle_target_forbidden)'
            USING ERRCODE = '42501';
    END IF;

    PERFORM accounting_core.acquire_reconciliation_run_lifecycle_lock(
        NEW.tenant_account_id,
        NEW.reconciliation_run_id
    );

    IF OLD.run_status_code NOT IN ('evaluating', 'review_required') THEN
        RAISE EXCEPTION
            'unsupported reconciliation status transition to reconciled (reconciliation_lifecycle_state)'
            USING ERRCODE = '23514';
    END IF;

    SELECT count(*)
    INTO transition_count
    FROM accounting_core.reconciliation_run_transition_command AS transition
    WHERE transition.tenant_account_id = NEW.tenant_account_id
      AND transition.reconciliation_run_id = NEW.reconciliation_run_id
      AND transition.target_run_status_code = 'reconciled';

    IF transition_count <> 1 THEN
        RAISE EXCEPTION
            'reconciled status requires exactly one immutable lifecycle command (reconciliation_lifecycle_command_required)'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER accounting_reconciliation_run_transition_guard
    BEFORE UPDATE OF run_status_code ON accounting_core.reconciliation_run
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.enforce_reconciliation_run_reconciled_transition();

-- Serialize all evidence that can change reconciliation eligibility on the same
-- run lifecycle lock. Once a transition command exists, that command's snapshot
-- is frozen even before the paired status UPDATE executes later in the same
-- transaction. Once reconciled, corrections require a new/superseding run.
CREATE OR REPLACE FUNCTION accounting_core.guard_reconciled_run_evidence_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    lifecycle_tenant_account_id uuid;
    lifecycle_reconciliation_run_id uuid;
    current_status text;
    transition_exists boolean;
BEGIN
    IF TG_OP = 'DELETE' THEN
        lifecycle_tenant_account_id := OLD.tenant_account_id;
        lifecycle_reconciliation_run_id := OLD.reconciliation_run_id;
    ELSE
        lifecycle_tenant_account_id := NEW.tenant_account_id;
        lifecycle_reconciliation_run_id := NEW.reconciliation_run_id;
    END IF;

    PERFORM accounting_core.acquire_reconciliation_run_lifecycle_lock(
        lifecycle_tenant_account_id,
        lifecycle_reconciliation_run_id
    );

    SELECT run_status_code
    INTO current_status
    FROM accounting_core.reconciliation_run
    WHERE tenant_account_id = lifecycle_tenant_account_id
      AND reconciliation_run_id = lifecycle_reconciliation_run_id;

    SELECT EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_run_transition_command AS transition
        WHERE transition.tenant_account_id = lifecycle_tenant_account_id
          AND transition.reconciliation_run_id = lifecycle_reconciliation_run_id
          AND transition.target_run_status_code = 'reconciled'
    )
    INTO transition_exists;

    IF current_status = 'reconciled' OR transition_exists THEN
        RAISE EXCEPTION
            'reconciliation transition/reconciled run evidence is frozen; create a new reconciliation run instead (reconciliation_lifecycle_frozen)'
            USING ERRCODE = '23514';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER accounting_reconciliation_lifecycle_candidate_guard
    BEFORE INSERT OR UPDATE OR DELETE ON accounting_core.reconciliation_candidate
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_reconciled_run_evidence_mutation();
CREATE TRIGGER accounting_reconciliation_lifecycle_match_guard
    BEFORE INSERT OR UPDATE OR DELETE ON accounting_core.reconciliation_match
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_reconciled_run_evidence_mutation();
CREATE TRIGGER accounting_reconciliation_lifecycle_statement_allocation_guard
    BEFORE INSERT OR UPDATE OR DELETE ON accounting_core.statement_match_allocation
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_reconciled_run_evidence_mutation();
CREATE TRIGGER accounting_reconciliation_lifecycle_journal_allocation_guard
    BEFORE INSERT OR UPDATE OR DELETE ON accounting_core.journal_match_allocation
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_reconciled_run_evidence_mutation();
CREATE TRIGGER accounting_reconciliation_lifecycle_approval_guard
    BEFORE INSERT OR UPDATE OR DELETE ON accounting_core.reconciliation_approval
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_reconciled_run_evidence_mutation();
CREATE TRIGGER accounting_reconciliation_lifecycle_exception_guard
    BEFORE INSERT OR UPDATE OR DELETE ON accounting_core.reconciliation_exception
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_reconciled_run_evidence_mutation();

ALTER TABLE accounting_core.reconciliation_run_transition_command ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.reconciliation_run_transition_command FORCE ROW LEVEL SECURITY;
CREATE POLICY reconciliation_run_transition_command_isolation
    ON accounting_core.reconciliation_run_transition_command
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

REVOKE ALL ON accounting_core.reconciliation_run_transition_command FROM PUBLIC;

COMMIT;