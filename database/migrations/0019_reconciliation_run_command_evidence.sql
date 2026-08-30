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

COMMIT;
