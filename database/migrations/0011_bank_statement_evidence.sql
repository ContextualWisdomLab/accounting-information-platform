BEGIN;

ALTER TABLE accounting_core.chart_account
    ADD CONSTRAINT chart_account_book_identity
    UNIQUE (tenant_account_id, accounting_book_id, chart_account_id);

CREATE TABLE accounting_core.bank_account_record (
    bank_account_record_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL REFERENCES accounting_core.tenant_account (tenant_account_id),
    bank_account_reference text NOT NULL
        CHECK (btrim(bank_account_reference) <> ''),
    account_currency_code text NOT NULL CHECK (account_currency_code ~ '^[A-Z]{3}$'),
    account_identifier_hash text NOT NULL CHECK (account_identifier_hash ~ '^sha256:[0-9a-f]{64}$'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (tenant_account_id, bank_account_reference),
    UNIQUE (tenant_account_id, bank_account_record_id)
);

CREATE TABLE accounting_core.bank_account_assignment (
    bank_account_assignment_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    bank_account_record_id uuid NOT NULL,
    legal_entity_id uuid NOT NULL,
    accounting_book_id uuid NOT NULL,
    chart_account_id uuid NOT NULL,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, bank_account_record_id)
        REFERENCES accounting_core.bank_account_record (tenant_account_id, bank_account_record_id),
    FOREIGN KEY (tenant_account_id, legal_entity_id)
        REFERENCES accounting_core.legal_entity_record (tenant_account_id, legal_entity_id),
    FOREIGN KEY (tenant_account_id, accounting_book_id)
        REFERENCES accounting_core.accounting_book (tenant_account_id, accounting_book_id),
    FOREIGN KEY (tenant_account_id, accounting_book_id, chart_account_id)
        REFERENCES accounting_core.chart_account (tenant_account_id, accounting_book_id, chart_account_id),
    UNIQUE (tenant_account_id, bank_account_assignment_id),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE TABLE accounting_integration.bank_statement_artifact (
    bank_statement_artifact_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL REFERENCES accounting_core.tenant_account (tenant_account_id),
    source_artifact_hash text NOT NULL CHECK (source_artifact_hash ~ '^sha256:[0-9a-f]{64}$'),
    artifact_store_reference text NOT NULL
        CHECK (btrim(artifact_store_reference) <> ''),
    artifact_byte_length integer NOT NULL CHECK (artifact_byte_length > 0),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (tenant_account_id, source_artifact_hash),
    UNIQUE (tenant_account_id, bank_statement_artifact_id)
);

CREATE TABLE accounting_integration.bank_statement_record (
    bank_statement_record_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    bank_account_record_id uuid NOT NULL,
    bank_statement_artifact_id uuid NOT NULL,
    message_definition_identifier text NOT NULL
        CHECK (message_definition_identifier = 'camt.053.001.14'),
    statement_identity_reference text NOT NULL
        CHECK (btrim(statement_identity_reference) <> ''),
    electronic_sequence_number text,
    legal_sequence_number text,
    period_start_at timestamptz,
    period_end_at timestamptz,
    opening_balance_hash text
        CHECK (opening_balance_hash IS NULL OR opening_balance_hash ~ '^sha256:[0-9a-f]{64}$'),
    closing_balance_hash text
        CHECK (closing_balance_hash IS NULL OR closing_balance_hash ~ '^sha256:[0-9a-f]{64}$'),
    source_artifact_hash text NOT NULL CHECK (source_artifact_hash ~ '^sha256:[0-9a-f]{64}$'),
    normalized_payload_hash text NOT NULL CHECK (normalized_payload_hash ~ '^sha256:[0-9a-f]{64}$'),
    ingestion_idempotency_key text NOT NULL
        CHECK (btrim(ingestion_idempotency_key) <> ''),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, bank_account_record_id)
        REFERENCES accounting_core.bank_account_record (tenant_account_id, bank_account_record_id),
    FOREIGN KEY (tenant_account_id, bank_statement_artifact_id)
        REFERENCES accounting_integration.bank_statement_artifact (tenant_account_id, bank_statement_artifact_id),
    UNIQUE (tenant_account_id, ingestion_idempotency_key),
    UNIQUE (tenant_account_id, source_artifact_hash),
    UNIQUE (tenant_account_id, bank_account_record_id, statement_identity_reference),
    UNIQUE (tenant_account_id, bank_statement_record_id)
);

CREATE TABLE accounting_integration.bank_statement_entry (
    bank_statement_entry_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    bank_statement_record_id uuid NOT NULL,
    source_entry_identity text,
    entry_sequence_number integer NOT NULL CHECK (entry_sequence_number > 0),
    source_locator_path text NOT NULL
        CHECK (btrim(source_locator_path) <> ''),
    booking_occurred_at timestamptz,
    value_occurred_at timestamptz,
    entry_amount numeric(38, 6) NOT NULL CHECK (entry_amount > 0),
    entry_currency_code text NOT NULL CHECK (entry_currency_code ~ '^[A-Z]{3}$'),
    credit_debit_code text NOT NULL CHECK (credit_debit_code IN ('CRDT', 'DBIT')),
    reversal_indicator boolean NOT NULL DEFAULT false,
    bank_transaction_domain_code text,
    bank_transaction_family_code text,
    bank_transaction_subfamily_code text,
    end_to_end_reference text,
    account_servicer_reference text,
    mandate_reference text,
    cheque_reference text,
    remittance_evidence_text text,
    counterparty_evidence_hash text
        CHECK (
            counterparty_evidence_hash IS NULL
            OR counterparty_evidence_hash ~ '^sha256:[0-9a-f]{64}$'
        ),
    source_entry_hash text NOT NULL CHECK (source_entry_hash ~ '^sha256:[0-9a-f]{64}$'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, bank_statement_record_id)
        REFERENCES accounting_integration.bank_statement_record (tenant_account_id, bank_statement_record_id),
    UNIQUE (tenant_account_id, bank_statement_record_id, entry_sequence_number),
    UNIQUE (tenant_account_id, bank_statement_entry_id)
);

CREATE TABLE accounting_integration.bank_statement_entry_detail (
    bank_statement_entry_detail_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    bank_statement_entry_id uuid NOT NULL,
    detail_sequence_number integer NOT NULL CHECK (detail_sequence_number > 0),
    source_locator_path text NOT NULL
        CHECK (btrim(source_locator_path) <> ''),
    detail_amount numeric(38, 6) NOT NULL CHECK (detail_amount > 0),
    detail_currency_code text NOT NULL CHECK (detail_currency_code ~ '^[A-Z]{3}$'),
    credit_debit_code text NOT NULL CHECK (credit_debit_code IN ('CRDT', 'DBIT')),
    end_to_end_reference text,
    account_servicer_reference text,
    remittance_evidence_text text,
    source_detail_hash text NOT NULL CHECK (source_detail_hash ~ '^sha256:[0-9a-f]{64}$'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, bank_statement_entry_id)
        REFERENCES accounting_integration.bank_statement_entry (tenant_account_id, bank_statement_entry_id),
    UNIQUE (tenant_account_id, bank_statement_entry_id, detail_sequence_number),
    UNIQUE (tenant_account_id, bank_statement_entry_detail_id)
);

CREATE INDEX bank_statement_account_period_index
    ON accounting_integration.bank_statement_record (
        tenant_account_id,
        bank_account_record_id,
        period_start_at,
        recorded_at,
        bank_statement_record_id
    );

CREATE INDEX bank_statement_entry_order_index
    ON accounting_integration.bank_statement_entry (
        tenant_account_id,
        bank_statement_record_id,
        entry_sequence_number,
        bank_statement_entry_id
    );

CREATE OR REPLACE FUNCTION accounting_integration.reject_statement_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'bank statement evidence is immutable once recorded'
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE TRIGGER bank_statement_artifact_immutable_guard
    BEFORE UPDATE OR DELETE ON accounting_integration.bank_statement_artifact
    FOR EACH ROW
    EXECUTE FUNCTION accounting_integration.reject_statement_mutation();

CREATE TRIGGER bank_statement_record_immutable_guard
    BEFORE UPDATE OR DELETE ON accounting_integration.bank_statement_record
    FOR EACH ROW
    EXECUTE FUNCTION accounting_integration.reject_statement_mutation();

CREATE TRIGGER bank_statement_entry_immutable_guard
    BEFORE UPDATE OR DELETE ON accounting_integration.bank_statement_entry
    FOR EACH ROW
    EXECUTE FUNCTION accounting_integration.reject_statement_mutation();

CREATE TRIGGER bank_statement_entry_detail_immutable_guard
    BEFORE UPDATE OR DELETE ON accounting_integration.bank_statement_entry_detail
    FOR EACH ROW
    EXECUTE FUNCTION accounting_integration.reject_statement_mutation();

ALTER TABLE accounting_core.bank_account_record ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.bank_account_record FORCE ROW LEVEL SECURITY;
CREATE POLICY bank_account_record_isolation ON accounting_core.bank_account_record
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

ALTER TABLE accounting_core.bank_account_assignment ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.bank_account_assignment FORCE ROW LEVEL SECURITY;
CREATE POLICY bank_account_assignment_isolation ON accounting_core.bank_account_assignment
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

ALTER TABLE accounting_integration.bank_statement_artifact ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_integration.bank_statement_artifact FORCE ROW LEVEL SECURITY;
CREATE POLICY bank_statement_artifact_isolation ON accounting_integration.bank_statement_artifact
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

ALTER TABLE accounting_integration.bank_statement_record ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_integration.bank_statement_record FORCE ROW LEVEL SECURITY;
CREATE POLICY bank_statement_record_isolation ON accounting_integration.bank_statement_record
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

ALTER TABLE accounting_integration.bank_statement_entry ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_integration.bank_statement_entry FORCE ROW LEVEL SECURITY;
CREATE POLICY bank_statement_entry_isolation ON accounting_integration.bank_statement_entry
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

ALTER TABLE accounting_integration.bank_statement_entry_detail ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_integration.bank_statement_entry_detail FORCE ROW LEVEL SECURITY;
CREATE POLICY bank_statement_detail_isolation ON accounting_integration.bank_statement_entry_detail
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

COMMIT;
