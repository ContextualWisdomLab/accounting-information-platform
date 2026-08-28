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

ALTER TABLE accounting_core.reconciliation_run_command ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.reconciliation_run_command FORCE ROW LEVEL SECURITY;
CREATE POLICY reconciliation_run_command_isolation
    ON accounting_core.reconciliation_run_command
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

REVOKE ALL ON accounting_core.reconciliation_run_command FROM PUBLIC;

COMMIT;
