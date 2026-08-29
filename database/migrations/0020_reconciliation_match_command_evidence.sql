BEGIN;

-- Immutable application command identity for a proposed reconciliation match.
-- This records reviewable candidate evidence only; it cannot approve, close, or
-- post a journal.

CREATE TABLE accounting_core.reconciliation_match_command (
    reconciliation_match_command_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    reconciliation_candidate_id uuid NOT NULL,
    reconciliation_match_id uuid NOT NULL,
    candidate_idempotency_key text NOT NULL
        CHECK (btrim(candidate_idempotency_key) <> ''),
    candidate_command_hash text NOT NULL
        CHECK (candidate_command_hash ~ '^sha256:[0-9a-f]{64}$'),
    source_payload_hash text NOT NULL
        CHECK (source_payload_hash ~ '^sha256:[0-9a-f]{64}$'),
    source_payload_reference text NOT NULL
        CHECK (btrim(source_payload_reference) <> ''),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (
        tenant_account_id,
        reconciliation_run_id,
        reconciliation_candidate_id
    ) REFERENCES accounting_core.reconciliation_candidate (
        tenant_account_id,
        reconciliation_run_id,
        reconciliation_candidate_id
    ),
    FOREIGN KEY (
        tenant_account_id,
        reconciliation_run_id,
        reconciliation_match_id
    ) REFERENCES accounting_core.reconciliation_match (
        tenant_account_id,
        reconciliation_run_id,
        reconciliation_match_id
    ),
    UNIQUE (tenant_account_id, candidate_idempotency_key),
    UNIQUE (tenant_account_id, reconciliation_run_id, reconciliation_match_id)
);

CREATE INDEX reconciliation_match_command_run_index
    ON accounting_core.reconciliation_match_command (
        tenant_account_id,
        reconciliation_run_id,
        recorded_at,
        reconciliation_match_command_id
    );

ALTER TABLE accounting_core.reconciliation_match_command ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.reconciliation_match_command FORCE ROW LEVEL SECURITY;

CREATE POLICY reconciliation_match_command_isolation
    ON accounting_core.reconciliation_match_command
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

REVOKE ALL ON accounting_core.reconciliation_match_command FROM PUBLIC;

CREATE OR REPLACE FUNCTION accounting_core.reject_reconciliation_match_command_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'recorded reconciliation match command evidence is immutable; create a new proposed match instead (reconciliation_match_command_immutable)'
        USING ERRCODE = '23514';
END;
$$;

CREATE TRIGGER reconciliation_match_command_immutability_guard
BEFORE UPDATE OR DELETE
ON accounting_core.reconciliation_match_command
FOR EACH ROW EXECUTE FUNCTION accounting_core.reject_reconciliation_match_command_mutation();

COMMIT;
