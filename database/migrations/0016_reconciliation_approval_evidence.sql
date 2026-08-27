BEGIN;

-- Durable human reconciliation approval evidence.
--
-- An approval is an immutable accounting-control fact bound to one tenant/run/match
-- and one immutable command identity.  Approval evidence never posts, reverses,
-- closes, or changes accounting policy.  A match may become approved only after
-- the corresponding durable approved decision exists.

CREATE TABLE accounting_core.reconciliation_approval (
    reconciliation_approval_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    reconciliation_match_id uuid NOT NULL,
    approval_command_key text NOT NULL
        CHECK (btrim(approval_command_key) <> ''),
    source_payload_hash text NOT NULL
        CHECK (source_payload_hash ~ '^sha256:[0-9a-f]{64}$'),
    approver_reference text NOT NULL
        CHECK (btrim(approver_reference) <> ''),
    approval_purpose_code text NOT NULL
        CHECK (btrim(approval_purpose_code) <> ''),
    approval_decision_code text NOT NULL
        CHECK (approval_decision_code IN ('approved', 'rejected')),
    effective_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (
        tenant_account_id,
        reconciliation_run_id,
        reconciliation_match_id
    )
        REFERENCES accounting_core.reconciliation_match (
            tenant_account_id,
            reconciliation_run_id,
            reconciliation_match_id
        ),
    UNIQUE (tenant_account_id, approval_command_key),
    UNIQUE (
        tenant_account_id,
        reconciliation_run_id,
        reconciliation_match_id
    )
);

CREATE INDEX reconciliation_approval_run_index
    ON accounting_core.reconciliation_approval (
        tenant_account_id,
        reconciliation_run_id,
        approval_decision_code,
        recorded_at,
        reconciliation_approval_id
    );

ALTER TABLE accounting_core.reconciliation_approval ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.reconciliation_approval FORCE ROW LEVEL SECURITY;

CREATE POLICY reconciliation_approval_isolation
    ON accounting_core.reconciliation_approval
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

REVOKE ALL ON accounting_core.reconciliation_approval FROM PUBLIC;

CREATE OR REPLACE FUNCTION accounting_core.reject_reconciliation_approval_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'recorded reconciliation approval evidence is immutable; create a new reviewed match instead (reconciliation_approval_immutable)'
        USING ERRCODE = '23514';
END;
$$;

CREATE TRIGGER reconciliation_approval_immutability_guard
BEFORE UPDATE OR DELETE
ON accounting_core.reconciliation_approval
FOR EACH ROW EXECUTE FUNCTION accounting_core.reject_reconciliation_approval_mutation();

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_approval_insert_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    current_status text;
BEGIN
    SELECT match_status_code
    INTO current_status
    FROM accounting_core.reconciliation_match
    WHERE tenant_account_id = NEW.tenant_account_id
      AND reconciliation_run_id = NEW.reconciliation_run_id
      AND reconciliation_match_id = NEW.reconciliation_match_id;

    IF NOT FOUND OR current_status <> 'proposed' THEN
        RAISE EXCEPTION
            'reconciliation approval evidence requires a proposed match in the same tenant/run scope (reconciliation_approval_scope_mismatch)'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER reconciliation_approval_insert_guard
BEFORE INSERT
ON accounting_core.reconciliation_approval
FOR EACH ROW EXECUTE FUNCTION accounting_core.reconciliation_approval_insert_guard();

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_match_requires_approval()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.match_status_code <> 'approved'
       OR (TG_OP = 'UPDATE' AND OLD.match_status_code = 'approved') THEN
        RETURN NEW;
    END IF;

    IF NEW.approved_at IS NULL THEN
        RAISE EXCEPTION
            'approved reconciliation match requires approved_at and durable approval evidence (reconciliation_approval_required)'
            USING ERRCODE = '23514';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_approval AS approval
        WHERE approval.tenant_account_id = NEW.tenant_account_id
          AND approval.reconciliation_run_id = NEW.reconciliation_run_id
          AND approval.reconciliation_match_id = NEW.reconciliation_match_id
          AND approval.approval_decision_code = 'approved'
    ) THEN
        RAISE EXCEPTION
            'approved reconciliation match requires durable approved evidence (reconciliation_approval_required)'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER reconciliation_match_requires_approval_guard
BEFORE INSERT OR UPDATE
ON accounting_core.reconciliation_match
FOR EACH ROW EXECUTE FUNCTION accounting_core.reconciliation_match_requires_approval();

COMMIT;
