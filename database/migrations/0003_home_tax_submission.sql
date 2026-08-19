BEGIN;

CREATE TABLE accounting_integration.home_tax_submission (
    home_tax_submission_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    legal_entity_id uuid NOT NULL,
    accounting_book_id uuid NOT NULL,
    fiscal_period_id uuid NOT NULL,
    submission_idempotency_key text NOT NULL
        CHECK (btrim(submission_idempotency_key) <> ''),
    submission_status_code text NOT NULL CHECK (submission_status_code IN ('rejected')),
    rejection_reason_code text NOT NULL CHECK (
        rejection_reason_code IN (
            'register_unavailable',
            'hometax_credential_missing',
            'hometax_transport_unavailable'
        )
    ),
    as_of_date date NOT NULL,
    closing_amount numeric(38, 6) NOT NULL,
    register_payload_hash text NOT NULL CHECK (register_payload_hash ~ '^sha256:[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, legal_entity_id)
        REFERENCES accounting_core.legal_entity_record (tenant_account_id, legal_entity_id),
    FOREIGN KEY (tenant_account_id, accounting_book_id)
        REFERENCES accounting_core.accounting_book (tenant_account_id, accounting_book_id),
    FOREIGN KEY (tenant_account_id, fiscal_period_id)
        REFERENCES accounting_core.fiscal_period (tenant_account_id, fiscal_period_id),
    UNIQUE (tenant_account_id, submission_idempotency_key),
    UNIQUE (tenant_account_id, home_tax_submission_id)
);

ALTER TABLE accounting_integration.home_tax_submission ENABLE ROW LEVEL SECURITY;

CREATE POLICY home_tax_submission_isolation ON accounting_integration.home_tax_submission
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

COMMIT;
