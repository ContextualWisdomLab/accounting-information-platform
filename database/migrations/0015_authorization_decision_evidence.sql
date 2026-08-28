BEGIN;

-- Purpose-bound application decisions are append-only evidence. The host identity adapter validates
-- credentials; this table retains only the opaque claims and decision needed for accounting audit.
CREATE TABLE accounting_integration.authorization_decision_record (
    authorization_decision_record_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    principal_reference text NOT NULL CHECK (btrim(principal_reference) <> ''),
    principal_tenant_reference text NOT NULL CHECK (btrim(principal_tenant_reference) <> ''),
    requested_tenant_reference text NOT NULL CHECK (btrim(requested_tenant_reference) <> ''),
    authentication_context_reference text NOT NULL
        CHECK (btrim(authentication_context_reference) <> ''),
    credential_evidence_reference text NOT NULL
        CHECK (btrim(credential_evidence_reference) <> ''),
    operation_code text NOT NULL CHECK (btrim(operation_code) <> ''),
    permission_code text NOT NULL,
    purpose_code text NOT NULL CHECK (btrim(purpose_code) <> ''),
    policy_version text NOT NULL CHECK (btrim(policy_version) <> ''),
    decision_code text NOT NULL CHECK (decision_code IN ('allowed', 'denied')),
    correlation_reference text NOT NULL CHECK (btrim(correlation_reference) <> ''),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id)
        REFERENCES accounting_core.tenant_account (tenant_account_id),
    UNIQUE (tenant_account_id, authorization_decision_record_id)
);

CREATE INDEX authorization_decision_scope_index
    ON accounting_integration.authorization_decision_record (
        tenant_account_id, recorded_at, authorization_decision_record_id
    );

CREATE OR REPLACE FUNCTION accounting_core.reject_authorization_decision_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'authorization decision evidence is append-only (authorization_evidence_immutable)'
        USING ERRCODE = '23514';
END;
$$;

CREATE TRIGGER authorization_decision_immutable_guard
    BEFORE UPDATE OR DELETE ON accounting_integration.authorization_decision_record
    FOR EACH ROW EXECUTE FUNCTION accounting_core.reject_authorization_decision_mutation();

ALTER TABLE accounting_integration.authorization_decision_record ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_integration.authorization_decision_record FORCE ROW LEVEL SECURITY;
CREATE POLICY authorization_decision_tenant_isolation
    ON accounting_integration.authorization_decision_record
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

REVOKE ALL ON accounting_integration.authorization_decision_record FROM PUBLIC;

COMMIT;
