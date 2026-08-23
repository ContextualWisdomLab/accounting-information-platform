BEGIN;

CREATE TABLE accounting_integration.fiscal_period_open_command (
    fiscal_period_open_command_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    legal_entity_id uuid NOT NULL,
    fiscal_period_id uuid NOT NULL,
    period_open_idempotency_key text NOT NULL
        CHECK (btrim(period_open_idempotency_key) <> ''),
    source_payload_hash text NOT NULL
        CHECK (source_payload_hash ~ '^sha256:[0-9a-f]{64}$'),
    requested_period_start_date date,
    requested_period_end_date date,
    command_recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, legal_entity_id)
        REFERENCES accounting_core.legal_entity_record (tenant_account_id, legal_entity_id),
    FOREIGN KEY (tenant_account_id, fiscal_period_id)
        REFERENCES accounting_core.fiscal_period (tenant_account_id, fiscal_period_id),
    UNIQUE (tenant_account_id, period_open_idempotency_key),
    UNIQUE (tenant_account_id, fiscal_period_open_command_id),
    CHECK (
        (requested_period_start_date IS NULL AND requested_period_end_date IS NULL)
        OR (
            requested_period_start_date IS NOT NULL
            AND requested_period_end_date IS NOT NULL
            AND requested_period_end_date >= requested_period_start_date
        )
    )
);

ALTER TABLE accounting_integration.fiscal_period_open_command ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_integration.fiscal_period_open_command FORCE ROW LEVEL SECURITY;

CREATE POLICY fiscal_period_open_command_isolation
    ON accounting_integration.fiscal_period_open_command
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

CREATE OR REPLACE FUNCTION accounting_integration.reject_period_open_command_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'fiscal period open command evidence is immutable (command_evidence_immutable)'
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE TRIGGER fiscal_period_open_command_immutable
    BEFORE UPDATE OR DELETE ON accounting_integration.fiscal_period_open_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_integration.reject_period_open_command_mutation();

REVOKE ALL ON accounting_integration.fiscal_period_open_command FROM PUBLIC;

COMMIT;
