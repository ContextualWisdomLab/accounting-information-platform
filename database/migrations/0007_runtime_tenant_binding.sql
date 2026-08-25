BEGIN;

CREATE TABLE accounting_core.runtime_tenant_binding (
    runtime_tenant_binding_id uuid PRIMARY KEY DEFAULT uuidv7(),
    runtime_role_oid oid NOT NULL,
    runtime_role_name name NOT NULL,
    tenant_account_id uuid NOT NULL,
    valid_from timestamptz NOT NULL DEFAULT clock_timestamp(),
    valid_to timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id)
        REFERENCES accounting_core.tenant_account (tenant_account_id),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    UNIQUE (runtime_tenant_binding_id, tenant_account_id)
);

CREATE UNIQUE INDEX runtime_tenant_binding_active_index
    ON accounting_core.runtime_tenant_binding (runtime_role_oid)
    WHERE valid_to IS NULL;

REVOKE ALL ON accounting_core.runtime_tenant_binding FROM PUBLIC;

CREATE OR REPLACE FUNCTION accounting_core.current_tenant_account_id()
RETURNS uuid
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, accounting_core
AS $$
    SELECT runtime_tenant_binding.tenant_account_id
    FROM accounting_core.runtime_tenant_binding
    JOIN pg_catalog.pg_roles
      ON pg_roles.oid = runtime_tenant_binding.runtime_role_oid
     AND pg_roles.rolname = runtime_tenant_binding.runtime_role_name
    WHERE runtime_tenant_binding.runtime_role_name = session_user
      AND runtime_tenant_binding.valid_from <= transaction_timestamp()
      AND (
            runtime_tenant_binding.valid_to IS NULL
            OR runtime_tenant_binding.valid_to > transaction_timestamp()
          )
    ORDER BY runtime_tenant_binding.valid_from DESC,
             runtime_tenant_binding.runtime_tenant_binding_id DESC
    LIMIT 1
$$;

COMMIT;
