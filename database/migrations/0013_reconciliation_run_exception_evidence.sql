BEGIN;

-- Durable bank-reconciliation control evidence.
--
-- This migration deliberately stops before candidate allocation, match approval,
-- or adjustment-posting authority.  It establishes only the immutable run scope,
-- explicit operator exception evidence, and normalized source-evidence references
-- required to make later reconciliation decisions auditable and tenant isolated.

ALTER TABLE accounting_core.bank_account_assignment
    ADD CONSTRAINT bank_account_assignment_reconciliation_scope_identity
    UNIQUE (
        tenant_account_id,
        legal_entity_id,
        accounting_book_id,
        bank_account_assignment_id
    );

CREATE TABLE accounting_core.reconciliation_run (
    reconciliation_run_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    legal_entity_id uuid NOT NULL,
    accounting_book_id uuid NOT NULL,
    bank_account_assignment_id uuid NOT NULL,
    currency_code text NOT NULL CHECK (currency_code ~ '^[A-Z]{3}$'),
    bank_cutoff_at timestamptz NOT NULL,
    book_cutoff_at timestamptz NOT NULL,
    matching_policy_version text NOT NULL
        CHECK (btrim(matching_policy_version) <> ''),
    knowledge_cutoff_at timestamptz NOT NULL,
    run_status_code text NOT NULL
        CHECK (
            run_status_code IN (
                'evaluating',
                'review_required',
                'reconciled',
                'not_reconciled',
                'superseded'
            )
        ),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, legal_entity_id)
        REFERENCES accounting_core.legal_entity_record (
            tenant_account_id,
            legal_entity_id
        ),
    FOREIGN KEY (tenant_account_id, legal_entity_id, accounting_book_id)
        REFERENCES accounting_core.accounting_book (
            tenant_account_id,
            legal_entity_id,
            accounting_book_id
        ),
    FOREIGN KEY (
        tenant_account_id,
        legal_entity_id,
        accounting_book_id,
        bank_account_assignment_id
    )
        REFERENCES accounting_core.bank_account_assignment (
            tenant_account_id,
            legal_entity_id,
            accounting_book_id,
            bank_account_assignment_id
        ),
    UNIQUE (tenant_account_id, reconciliation_run_id),
    CHECK (bank_cutoff_at <= knowledge_cutoff_at),
    CHECK (book_cutoff_at <= knowledge_cutoff_at)
);

CREATE TABLE accounting_core.reconciliation_exception (
    reconciliation_exception_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    exception_code text NOT NULL CHECK (btrim(exception_code) <> ''),
    owner_reference text NOT NULL CHECK (btrim(owner_reference) <> ''),
    next_action text NOT NULL CHECK (btrim(next_action) <> ''),
    effective_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    resolution_status_code text NOT NULL
        CHECK (resolution_status_code IN ('open', 'resolved', 'superseded')),
    FOREIGN KEY (tenant_account_id, reconciliation_run_id)
        REFERENCES accounting_core.reconciliation_run (
            tenant_account_id,
            reconciliation_run_id
        ),
    UNIQUE (tenant_account_id, reconciliation_exception_id)
);

CREATE TABLE accounting_core.reconciliation_evidence (
    reconciliation_evidence_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    reconciliation_exception_id uuid,
    evidence_type_code text NOT NULL CHECK (btrim(evidence_type_code) <> ''),
    evidence_reference text NOT NULL CHECK (btrim(evidence_reference) <> ''),
    evidence_payload_hash text
        CHECK (
            evidence_payload_hash IS NULL
            OR evidence_payload_hash ~ '^sha256:[0-9a-f]{64}$'
        ),
    effective_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, reconciliation_run_id)
        REFERENCES accounting_core.reconciliation_run (
            tenant_account_id,
            reconciliation_run_id
        ),
    FOREIGN KEY (tenant_account_id, reconciliation_exception_id)
        REFERENCES accounting_core.reconciliation_exception (
            tenant_account_id,
            reconciliation_exception_id
        ),
    UNIQUE (
        tenant_account_id,
        reconciliation_run_id,
        evidence_type_code,
        evidence_reference
    )
);

CREATE INDEX reconciliation_run_scope_index
    ON accounting_core.reconciliation_run (
        tenant_account_id,
        legal_entity_id,
        accounting_book_id,
        bank_account_assignment_id,
        currency_code,
        bank_cutoff_at,
        book_cutoff_at,
        reconciliation_run_id
    );

CREATE INDEX reconciliation_exception_run_index
    ON accounting_core.reconciliation_exception (
        tenant_account_id,
        reconciliation_run_id,
        resolution_status_code,
        recorded_at,
        reconciliation_exception_id
    );

CREATE INDEX reconciliation_evidence_run_index
    ON accounting_core.reconciliation_evidence (
        tenant_account_id,
        reconciliation_run_id,
        recorded_at,
        reconciliation_evidence_id
    );

CREATE OR REPLACE FUNCTION accounting_core.reject_reconciliation_run_scope_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.tenant_account_id IS DISTINCT FROM OLD.tenant_account_id
       OR NEW.legal_entity_id IS DISTINCT FROM OLD.legal_entity_id
       OR NEW.accounting_book_id IS DISTINCT FROM OLD.accounting_book_id
       OR NEW.bank_account_assignment_id IS DISTINCT FROM OLD.bank_account_assignment_id
       OR NEW.currency_code IS DISTINCT FROM OLD.currency_code
       OR NEW.bank_cutoff_at IS DISTINCT FROM OLD.bank_cutoff_at
       OR NEW.book_cutoff_at IS DISTINCT FROM OLD.book_cutoff_at
       OR NEW.matching_policy_version IS DISTINCT FROM OLD.matching_policy_version
       OR NEW.knowledge_cutoff_at IS DISTINCT FROM OLD.knowledge_cutoff_at THEN
        RAISE EXCEPTION
            'evaluated reconciliation run scope is immutable; create a new run instead (reconciliation_scope_immutable)'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER reconciliation_run_scope_guard
    BEFORE UPDATE OF
        tenant_account_id,
        legal_entity_id,
        accounting_book_id,
        bank_account_assignment_id,
        currency_code,
        bank_cutoff_at,
        book_cutoff_at,
        matching_policy_version,
        knowledge_cutoff_at
    ON accounting_core.reconciliation_run
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.reject_reconciliation_run_scope_mutation();

ALTER TABLE accounting_core.reconciliation_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.reconciliation_run FORCE ROW LEVEL SECURITY;
CREATE POLICY reconciliation_run_isolation ON accounting_core.reconciliation_run
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

ALTER TABLE accounting_core.reconciliation_exception ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.reconciliation_exception FORCE ROW LEVEL SECURITY;
CREATE POLICY reconciliation_exception_isolation ON accounting_core.reconciliation_exception
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

ALTER TABLE accounting_core.reconciliation_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.reconciliation_evidence FORCE ROW LEVEL SECURITY;
CREATE POLICY reconciliation_evidence_isolation ON accounting_core.reconciliation_evidence
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

REVOKE ALL ON accounting_core.reconciliation_run FROM PUBLIC;
REVOKE ALL ON accounting_core.reconciliation_exception FROM PUBLIC;
REVOKE ALL ON accounting_core.reconciliation_evidence FROM PUBLIC;

COMMIT;
