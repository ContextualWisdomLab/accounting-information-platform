BEGIN;

-- Exception resolution is a named accounting control command, not a mutable
-- status shortcut. It shares the tenant-wide reconciliation idempotency
-- namespace so a key cannot identify both an opening, run-finalization, and
-- exception-resolution command.
--
-- Migration 0019 permitted direct terminal exception status updates. Do not
-- silently reinterpret those legacy rows as if they had maker-checker command
-- evidence. Operators must audit/remediate them before this authority boundary
-- is installed; the transaction abort leaves the previous schema unchanged.
-- Migration 0013 forces RLS on reconciliation_exception. Give only the current
-- migration user transaction-scoped SELECT visibility for this all-tenant
-- upgrade preflight, mirroring migration 0016's reviewed-match upgrade guard;
-- remove the policy before any durable authority change is installed.
CREATE POLICY reconciliation_exception_resolution_upgrade_visibility
    ON accounting_core.reconciliation_exception
    FOR SELECT
    TO current_user
    USING (true);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_exception
        WHERE resolution_status_code <> 'open'
    ) THEN
        RAISE EXCEPTION
            'legacy terminal reconciliation exceptions require audited remediation before migration 0020 (reconciliation_exception_resolution_legacy_terminal_preflight)'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

DROP POLICY reconciliation_exception_resolution_upgrade_visibility
    ON accounting_core.reconciliation_exception;

ALTER TABLE accounting_core.reconciliation_command_identity
    DROP CONSTRAINT reconciliation_command_identity_command_family_code_check;
ALTER TABLE accounting_core.reconciliation_command_identity
    ADD CONSTRAINT reconciliation_command_identity_command_family_code_check
    CHECK (
        command_family_code IN (
            'run_opening',
            'run_reconciliation',
            'exception_resolution'
        )
    );

CREATE TABLE accounting_core.reconciliation_exception_resolution_command (
    reconciliation_exception_resolution_command_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    reconciliation_exception_id uuid NOT NULL,
    reconciliation_evidence_id uuid NOT NULL,
    reconciliation_resolution_idempotency_key text NOT NULL
        CHECK (btrim(reconciliation_resolution_idempotency_key) <> ''),
    target_resolution_status_code text NOT NULL
        CHECK (target_resolution_status_code IN ('resolved', 'superseded')),
    resolution_evidence_reference text NOT NULL
        CHECK (btrim(resolution_evidence_reference) <> ''),
    resolution_evidence_hash text NOT NULL
        CHECK (resolution_evidence_hash ~ '^sha256:[0-9a-f]{64}$'),
    source_payload_hash text NOT NULL
        CHECK (source_payload_hash ~ '^sha256:[0-9a-f]{64}$'),
    reconciliation_exception_resolution_command_hash text NOT NULL
        CHECK (
            reconciliation_exception_resolution_command_hash
                ~ '^sha256:[0-9a-f]{64}$'
        ),
    actor_reference text NOT NULL CHECK (btrim(actor_reference) <> ''),
    purpose_code text NOT NULL CHECK (btrim(purpose_code) <> ''),
    effective_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (
        tenant_account_id,
        reconciliation_run_id,
        reconciliation_exception_id
    )
        REFERENCES accounting_core.reconciliation_exception (
            tenant_account_id,
            reconciliation_run_id,
            reconciliation_exception_id
        ),
    FOREIGN KEY (reconciliation_evidence_id)
        REFERENCES accounting_core.reconciliation_evidence (
            reconciliation_evidence_id
        ),
    UNIQUE (
        tenant_account_id,
        reconciliation_exception_resolution_command_id
    ),
    UNIQUE (tenant_account_id, reconciliation_resolution_idempotency_key),
    UNIQUE (
        tenant_account_id,
        reconciliation_run_id,
        reconciliation_exception_id
    )
);

CREATE INDEX reconciliation_exception_resolution_recorded_index
    ON accounting_core.reconciliation_exception_resolution_command (
        tenant_account_id,
        reconciliation_run_id,
        recorded_at,
        reconciliation_exception_resolution_command_id
    );

-- Reconciliation evidence is retained source/control evidence, not mutable
-- operational state. Once migration 0020 is installed, changing or deleting a
-- retained artifact would invalidate any later command that relies on it.
CREATE OR REPLACE FUNCTION accounting_core.reject_reconciliation_evidence_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'reconciliation evidence is immutable once retained (reconciliation_evidence_immutable)'
        USING ERRCODE = '23514';
END;
$$;

CREATE TRIGGER accounting_reconciliation_evidence_immutable_guard
    BEFORE UPDATE OR DELETE ON accounting_core.reconciliation_evidence
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.reject_reconciliation_evidence_mutation();

CREATE OR REPLACE FUNCTION accounting_core.reserve_reconciliation_exception_resolution_identity()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM accounting_core.reserve_reconciliation_command_identity(
        NEW.tenant_account_id,
        NEW.reconciliation_resolution_idempotency_key,
        'exception_resolution'
    );
    RETURN NEW;
END;
$$;

CREATE TRIGGER accounting_reconciliation_exception_resolution_identity_guard
    BEFORE INSERT ON accounting_core.reconciliation_exception_resolution_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.reserve_reconciliation_exception_resolution_identity();

CREATE OR REPLACE FUNCTION accounting_core.assign_reconciliation_exception_resolution_hash()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    current_run_status text;
    current_exception_status text;
    exception_code_value text;
    exception_owner_reference text;
    exception_effective_at timestamptz;
    opening_command_hash text;
    tenant_reference text;
    retained_evidence_id uuid;
    retained_evidence_effective_at timestamptz;
    retained_evidence_recorded_at timestamptz;
    canonical_command jsonb;
BEGIN
    -- System time is database-owned. Do not let a caller backdate or future-date
    -- the recording timestamp to make an otherwise future-effective decision
    -- appear authoritative before the review actually exists.
    NEW.recorded_at := clock_timestamp();

    PERFORM accounting_core.acquire_reconciliation_run_lifecycle_lock(
        NEW.tenant_account_id,
        NEW.reconciliation_run_id
    );

    SELECT run.run_status_code,
           exception.resolution_status_code,
           exception.exception_code,
           exception.owner_reference,
           exception.effective_at,
           opening_command.reconciliation_command_hash,
           tenant.tenant_account_code
    INTO current_run_status,
         current_exception_status,
         exception_code_value,
         exception_owner_reference,
         exception_effective_at,
         opening_command_hash,
         tenant_reference
    FROM accounting_core.reconciliation_exception AS exception
    JOIN accounting_core.reconciliation_run AS run
      ON run.tenant_account_id = exception.tenant_account_id
     AND run.reconciliation_run_id = exception.reconciliation_run_id
    JOIN accounting_core.reconciliation_run_command AS opening_command
      ON opening_command.tenant_account_id = run.tenant_account_id
     AND opening_command.reconciliation_run_id = run.reconciliation_run_id
    JOIN accounting_core.tenant_account AS tenant
      ON tenant.tenant_account_id = run.tenant_account_id
    WHERE exception.tenant_account_id = NEW.tenant_account_id
      AND exception.reconciliation_run_id = NEW.reconciliation_run_id
      AND exception.reconciliation_exception_id = NEW.reconciliation_exception_id
    FOR UPDATE OF exception, run;

    IF current_run_status IS NULL OR current_exception_status IS NULL THEN
        RAISE EXCEPTION
            'reconciliation exception is not recorded in the requested run (reconciliation_exception_resolution_scope)'
            USING ERRCODE = '23514';
    END IF;

    IF current_run_status NOT IN ('evaluating', 'review_required') THEN
        RAISE EXCEPTION
            'only evaluating or review_required runs permit exception resolution (reconciliation_exception_resolution_state)'
            USING ERRCODE = '23514';
    END IF;

    IF current_exception_status <> 'open' THEN
        RAISE EXCEPTION
            'reconciliation exception is already terminal; replay its original resolution command (reconciliation_exception_resolution_state)'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.actor_reference = exception_owner_reference THEN
        RAISE EXCEPTION
            'exception owner and resolution actor must differ (reconciliation_exception_maker_checker_required)'
            USING ERRCODE = '42501';
    END IF;

    IF NEW.effective_at < exception_effective_at THEN
        RAISE EXCEPTION
            'reconciliation exception resolution effective time cannot precede the exception (reconciliation_exception_resolution_time)'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.effective_at > NEW.recorded_at THEN
        RAISE EXCEPTION
            'reconciliation exception resolution effective time cannot be in the future relative to database recording time (reconciliation_exception_resolution_future_time)'
            USING ERRCODE = '23514';
    END IF;

    SELECT evidence.reconciliation_evidence_id,
           evidence.effective_at,
           evidence.recorded_at
    INTO retained_evidence_id,
         retained_evidence_effective_at,
         retained_evidence_recorded_at
    FROM accounting_core.reconciliation_evidence AS evidence
    WHERE evidence.tenant_account_id = NEW.tenant_account_id
      AND evidence.reconciliation_run_id = NEW.reconciliation_run_id
      AND evidence.reconciliation_exception_id = NEW.reconciliation_exception_id
      AND evidence.evidence_type_code = 'exception_resolution_review'
      AND evidence.evidence_reference = NEW.resolution_evidence_reference
      AND evidence.evidence_payload_hash = NEW.resolution_evidence_hash;

    IF retained_evidence_id IS NULL THEN
        RAISE EXCEPTION
            'exception resolution requires one retained exception-scoped reviewed artifact whose reference and digest match the command (reconciliation_exception_resolution_evidence_required)'
            USING ERRCODE = '23514';
    END IF;

    IF retained_evidence_effective_at > NEW.effective_at
       OR retained_evidence_recorded_at > NEW.recorded_at THEN
        RAISE EXCEPTION
            'exception resolution evidence cannot become effective or be recorded after the resolution decision boundary (reconciliation_exception_resolution_evidence_time)'
            USING ERRCODE = '23514';
    END IF;

    NEW.reconciliation_evidence_id := retained_evidence_id;

    SELECT jsonb_build_object(
        'actor_reference', NEW.actor_reference,
        'exception_code', exception_code_value,
        'exception_owner_reference', exception_owner_reference,
        'opening_command_hash', opening_command_hash,
        'purpose_code', NEW.purpose_code,
        'reconciliation_evidence_id', NEW.reconciliation_evidence_id::text,
        'reconciliation_exception_id', NEW.reconciliation_exception_id::text,
        'reconciliation_idempotency_key', NEW.reconciliation_resolution_idempotency_key,
        'reconciliation_run_id', NEW.reconciliation_run_id::text,
        'resolution_evidence_hash', NEW.resolution_evidence_hash,
        'resolution_evidence_reference', NEW.resolution_evidence_reference,
        'source_payload_hash', NEW.source_payload_hash,
        'target_resolution_status_code', NEW.target_resolution_status_code,
        'tenant_reference', tenant_reference,
        'effective_at', to_char(
            NEW.effective_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
        )
    )
    INTO canonical_command;

    NEW.reconciliation_exception_resolution_command_hash := 'sha256:' || encode(
        sha256(
            convert_to(
                'reconciliation_exception_resolution_command:v1|'
                || canonical_command::text,
                'UTF8'
            )
        ),
        'hex'
    );
    RETURN NEW;
END;
$$;

CREATE TRIGGER accounting_reconciliation_exception_resolution_hash_guard
    BEFORE INSERT ON accounting_core.reconciliation_exception_resolution_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.assign_reconciliation_exception_resolution_hash();

CREATE OR REPLACE FUNCTION accounting_core.reject_reconciliation_exception_resolution_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'reconciliation exception resolution command evidence is immutable once recorded'
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE TRIGGER reconciliation_exception_resolution_immutable_guard
    BEFORE UPDATE OR DELETE ON accounting_core.reconciliation_exception_resolution_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.reject_reconciliation_exception_resolution_mutation();

-- Exception control evidence is immutable from creation. A maker/checker command
-- may change only resolution_status_code, and only when the matching immutable
-- command is already present in the same transaction. Reassignment or other
-- operational changes require a future named command rather than raw DML.
CREATE OR REPLACE FUNCTION accounting_core.enforce_reconciliation_exception_resolution_authority()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    matching_command_count integer;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'reconciliation exception evidence is immutable from creation (reconciliation_exception_evidence_immutable)'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.tenant_account_id IS DISTINCT FROM OLD.tenant_account_id
       OR NEW.reconciliation_run_id IS DISTINCT FROM OLD.reconciliation_run_id
       OR NEW.reconciliation_exception_id IS DISTINCT FROM OLD.reconciliation_exception_id
       OR NEW.exception_code IS DISTINCT FROM OLD.exception_code
       OR NEW.owner_reference IS DISTINCT FROM OLD.owner_reference
       OR NEW.next_action IS DISTINCT FROM OLD.next_action
       OR NEW.effective_at IS DISTINCT FROM OLD.effective_at
       OR NEW.recorded_at IS DISTINCT FROM OLD.recorded_at THEN
        RAISE EXCEPTION
            'reconciliation exception evidence is immutable from creation (reconciliation_exception_evidence_immutable)'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.resolution_status_code IS NOT DISTINCT FROM OLD.resolution_status_code THEN
        RETURN NEW;
    END IF;

    IF OLD.resolution_status_code <> 'open'
       OR NEW.resolution_status_code NOT IN ('resolved', 'superseded') THEN
        RAISE EXCEPTION
            'reconciliation exception status changes require the original named resolution command (reconciliation_exception_resolution_state)'
            USING ERRCODE = '23514';
    END IF;

    PERFORM accounting_core.acquire_reconciliation_run_lifecycle_lock(
        NEW.tenant_account_id,
        NEW.reconciliation_run_id
    );

    SELECT count(*)
    INTO matching_command_count
    FROM accounting_core.reconciliation_exception_resolution_command AS command
    WHERE command.tenant_account_id = NEW.tenant_account_id
      AND command.reconciliation_run_id = NEW.reconciliation_run_id
      AND command.reconciliation_exception_id = NEW.reconciliation_exception_id
      AND command.target_resolution_status_code = NEW.resolution_status_code;

    IF matching_command_count <> 1 THEN
        RAISE EXCEPTION
            'terminal reconciliation exception status requires one immutable maker-checker resolution command (reconciliation_exception_resolution_command_required)'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER accounting_reconciliation_exception_resolution_authority_guard
    BEFORE UPDATE OR DELETE ON accounting_core.reconciliation_exception
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.enforce_reconciliation_exception_resolution_authority();

CREATE OR REPLACE FUNCTION accounting_core.enforce_reconciliation_exception_resolution_pair()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    paired_status text;
BEGIN
    SELECT resolution_status_code
    INTO paired_status
    FROM accounting_core.reconciliation_exception
    WHERE tenant_account_id = NEW.tenant_account_id
      AND reconciliation_run_id = NEW.reconciliation_run_id
      AND reconciliation_exception_id = NEW.reconciliation_exception_id;

    IF paired_status IS DISTINCT FROM NEW.target_resolution_status_code THEN
        RAISE EXCEPTION
            'reconciliation exception resolution command and terminal status must commit atomically (reconciliation_exception_resolution_atomic_pair)'
            USING ERRCODE = '23514';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER reconciliation_exception_resolution_status_pair_guard
    AFTER INSERT ON accounting_core.reconciliation_exception_resolution_command
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.enforce_reconciliation_exception_resolution_pair();

ALTER TABLE accounting_core.reconciliation_exception_resolution_command ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.reconciliation_exception_resolution_command FORCE ROW LEVEL SECURITY;
CREATE POLICY reconciliation_exception_resolution_command_isolation
    ON accounting_core.reconciliation_exception_resolution_command
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

REVOKE ALL ON accounting_core.reconciliation_exception_resolution_command FROM PUBLIC;

-- Replace the interim all-exception rejection from migration 0019. A run may
-- now finalize only when every exception is terminal under exactly one durable
-- command whose target status matches the exception row. The transition hash
-- remains database-owned and all other reviewed-match controls are unchanged.
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
        LEFT JOIN accounting_core.reconciliation_exception_resolution_command AS resolution
          ON resolution.tenant_account_id = exception.tenant_account_id
         AND resolution.reconciliation_run_id = exception.reconciliation_run_id
         AND resolution.reconciliation_exception_id = exception.reconciliation_exception_id
        WHERE exception.tenant_account_id = NEW.tenant_account_id
          AND exception.reconciliation_run_id = NEW.reconciliation_run_id
          AND (
              exception.resolution_status_code = 'open'
              OR resolution.reconciliation_exception_resolution_command_id IS NULL
              OR resolution.target_resolution_status_code
                   IS DISTINCT FROM exception.resolution_status_code
          )
    ) THEN
        RAISE EXCEPTION
            'reconciliation run has exception evidence without durable resolution-command authority (reconciliation_exception_resolution_command_required)'
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
        'book_population_reference', NEW.book_population_reference,
        'effective_at', to_char(
            NEW.effective_at AT TIME ZONE 'UTC',
            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
        ),
        'opening_command_hash', opening_command.reconciliation_command_hash,
        'purpose_code', NEW.purpose_code,
        'reconciliation_idempotency_key', NEW.reconciliation_transition_idempotency_key,
        'reconciliation_run_id', NEW.reconciliation_run_id::text,
        'reconciliation_snapshot_hash', NEW.reconciliation_snapshot_hash,
        'statement_population_reference', NEW.statement_population_reference,
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

COMMIT;
