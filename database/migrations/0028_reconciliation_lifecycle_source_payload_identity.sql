BEGIN;

-- A reconciliation lifecycle idempotency key identifies the complete received
-- command, not only the normalized fields that participate in accounting
-- authority. Historical transition rows created before this migration do not
-- retain that complete source identity, so do not synthesize it from the
-- normalized command hash or later state.
CREATE POLICY reconciliation_lifecycle_source_payload_upgrade_visibility
    ON accounting_core.reconciliation_run_transition_command
    FOR SELECT
    TO current_user
    USING (true);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_run_transition_command
    ) THEN
        RAISE EXCEPTION
            'pre-0026 reconciliation lifecycle transitions lack complete source-payload identity; perform audited remediation before migration 0026 (reconciliation_lifecycle_source_payload_preflight)'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

DROP POLICY reconciliation_lifecycle_source_payload_upgrade_visibility
    ON accounting_core.reconciliation_run_transition_command;

ALTER TABLE accounting_core.reconciliation_run_transition_command
    ADD COLUMN source_payload_hash text NOT NULL
        CHECK (source_payload_hash ~ '^sha256:[0-9a-f]{64}$');

-- Bind the immutable source-payload identity into the database-derived command
-- hash. The database-owned reconciliation snapshot and population references
-- are still supplied by the earlier authority triggers before this function is
-- reached; this change does not move monetary or review authority back to the
-- caller.
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
        'source_payload_hash', NEW.source_payload_hash,
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
                'reconciliation_run_transition_command:v2|' || canonical_command::text,
                'UTF8'
            )
        ),
        'hex'
    );
    RETURN NEW;
END;
$$;

COMMIT;
