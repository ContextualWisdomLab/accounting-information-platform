BEGIN;

-- Preserve the exact numeric balance facts that migration 0011 previously
-- reduced to hashes. These are evidence only; reconciliation may consume them
-- for a bridge but this table cannot post, reverse, or change a journal.
CREATE TABLE accounting_integration.bank_statement_balance (
    bank_statement_balance_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    bank_statement_record_id uuid NOT NULL,
    balance_sequence_number integer NOT NULL CHECK (balance_sequence_number > 0),
    balance_type_code text
        CHECK (balance_type_code IS NULL OR btrim(balance_type_code) <> ''),
    balance_type_source_code text
        CHECK (
            (balance_type_code IS NULL AND balance_type_source_code IS NULL)
            OR (balance_type_code IS NOT NULL AND balance_type_source_code IN ('cd', 'prtry'))
        ),
    balance_amount numeric(38, 6) NOT NULL CHECK (balance_amount >= 0),
    balance_currency_code text NOT NULL CHECK (balance_currency_code ~ '^[A-Z]{3}$'),
    credit_debit_code text NOT NULL CHECK (credit_debit_code IN ('CRDT', 'DBIT')),
    balance_effective_at timestamptz,
    source_locator_path text NOT NULL CHECK (btrim(source_locator_path) <> ''),
    source_balance_hash text NOT NULL CHECK (source_balance_hash ~ '^sha256:[0-9a-f]{64}$'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, bank_statement_record_id)
        REFERENCES accounting_integration.bank_statement_record (
            tenant_account_id, bank_statement_record_id
        ),
    UNIQUE (tenant_account_id, bank_statement_record_id, balance_sequence_number),
    UNIQUE (tenant_account_id, bank_statement_balance_id)
);

CREATE INDEX bank_statement_balance_order_index
    ON accounting_integration.bank_statement_balance (
        tenant_account_id,
        bank_statement_record_id,
        balance_sequence_number,
        bank_statement_balance_id
    );

CREATE TRIGGER bank_statement_balance_immutable_guard
    BEFORE UPDATE OR DELETE ON accounting_integration.bank_statement_balance
    FOR EACH ROW
    EXECUTE FUNCTION accounting_integration.reject_statement_mutation();

ALTER TABLE accounting_integration.bank_statement_balance ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_integration.bank_statement_balance FORCE ROW LEVEL SECURITY;
CREATE POLICY bank_statement_balance_isolation
    ON accounting_integration.bank_statement_balance
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

REVOKE ALL ON accounting_integration.bank_statement_balance FROM PUBLIC;

COMMIT;
