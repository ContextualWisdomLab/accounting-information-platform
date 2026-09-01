BEGIN;

-- Purpose-bound application decisions are append-only evidence. The host identity adapter validates
-- credentials; this table retains only the opaque claims and decision needed for accounting audit.
-- Identity references are normalized CWL URNs and use the 255-octet authorization profile ceiling;
-- raw external claims remain at the trusted identity-provider boundary. Operation/purpose limits
-- mirror the executable code contract, permission is two bounded code components, and the existing
-- 512-character correlation evidence ceiling is enforced again at PostgreSQL so direct SQL cannot
-- inflate storage while multibyte command identities retain the same contract as the HTTP boundary.
CREATE TABLE accounting_integration.authorization_decision_record (
    authorization_decision_record_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    principal_reference text NOT NULL
        CHECK (
            btrim(principal_reference) <> ''
            AND octet_length(principal_reference) <= 255
            AND principal_reference ~ '^urn:cwl:[A-Za-z0-9_:.-]+$'
        ),
    principal_tenant_reference text NOT NULL
        CHECK (
            btrim(principal_tenant_reference) <> ''
            AND octet_length(principal_tenant_reference) <= 255
            AND principal_tenant_reference ~ '^urn:cwl:[A-Za-z0-9_:.-]+$'
        ),
    requested_tenant_reference text NOT NULL
        CHECK (
            btrim(requested_tenant_reference) <> ''
            AND octet_length(requested_tenant_reference) <= 255
            AND requested_tenant_reference ~ '^urn:cwl:[A-Za-z0-9_:.-]+$'
        ),
    authentication_context_reference text NOT NULL
        CHECK (
            btrim(authentication_context_reference) <> ''
            AND octet_length(authentication_context_reference) <= 255
            AND authentication_context_reference ~ '^urn:cwl:[A-Za-z0-9_:.-]+$'
        ),
    credential_evidence_reference text NOT NULL
        CHECK (
            btrim(credential_evidence_reference) <> ''
            AND octet_length(credential_evidence_reference) <= 255
            AND credential_evidence_reference ~ '^urn:cwl:[A-Za-z0-9_:.-]+$'
        ),
    operation_code text NOT NULL
        CHECK (
            octet_length(operation_code) <= 64
            AND operation_code ~ '^[a-z][a-z0-9_]{1,63}$'
        ),
    permission_code text NOT NULL
        CHECK (
            octet_length(permission_code) <= 129
            AND (
                permission_code = ''
                OR permission_code ~ '^[a-z][a-z0-9_]{1,63}\.[a-z][a-z0-9_]{1,63}$'
            )
        ),
    purpose_code text NOT NULL
        CHECK (
            octet_length(purpose_code) <= 64
            AND purpose_code ~ '^[a-z][a-z0-9_]{1,63}$'
        ),
    policy_version text NOT NULL
        CHECK (btrim(policy_version) <> '' AND octet_length(policy_version) <= 64),
    decision_code text NOT NULL CHECK (decision_code IN ('allowed', 'denied')),
    correlation_reference text NOT NULL
        CHECK (btrim(correlation_reference) <> '' AND char_length(correlation_reference) <= 512),
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
