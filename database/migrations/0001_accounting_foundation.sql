BEGIN;

CREATE SCHEMA IF NOT EXISTS accounting_core;
CREATE SCHEMA IF NOT EXISTS accounting_integration;
CREATE SCHEMA IF NOT EXISTS accounting_reporting;

CREATE TABLE accounting_core.tenant_account (
    tenant_account_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_code text NOT NULL UNIQUE,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE TABLE accounting_core.legal_entity_record (
    legal_entity_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL REFERENCES accounting_core.tenant_account (tenant_account_id),
    legal_entity_code text NOT NULL,
    entity_name text NOT NULL,
    functional_currency_code text NOT NULL CHECK (functional_currency_code ~ '^[A-Z]{3}$'),
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (tenant_account_id, legal_entity_code, valid_from),
    UNIQUE (tenant_account_id, legal_entity_id),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE TABLE accounting_core.accounting_book (
    accounting_book_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    legal_entity_id uuid NOT NULL,
    book_role_code text NOT NULL,
    book_name text NOT NULL,
    reporting_currency_code text NOT NULL CHECK (reporting_currency_code ~ '^[A-Z]{3}$'),
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, legal_entity_id)
        REFERENCES accounting_core.legal_entity_record (tenant_account_id, legal_entity_id),
    UNIQUE (tenant_account_id, legal_entity_id, book_role_code, valid_from),
    UNIQUE (tenant_account_id, accounting_book_id),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE TABLE accounting_core.chart_account (
    chart_account_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    accounting_book_id uuid NOT NULL,
    chart_account_code text NOT NULL,
    account_name text NOT NULL,
    normal_balance_code text NOT NULL CHECK (normal_balance_code IN ('debit', 'credit')),
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, accounting_book_id)
        REFERENCES accounting_core.accounting_book (tenant_account_id, accounting_book_id),
    UNIQUE (tenant_account_id, accounting_book_id, chart_account_code, valid_from),
    UNIQUE (tenant_account_id, chart_account_id),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE TABLE accounting_core.account_role_mapping (
    account_role_mapping_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    accounting_book_id uuid NOT NULL,
    account_role_code text NOT NULL,
    chart_account_id uuid NOT NULL,
    accounting_policy_version text NOT NULL,
    posting_rule_version text NOT NULL,
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, accounting_book_id)
        REFERENCES accounting_core.accounting_book (tenant_account_id, accounting_book_id),
    FOREIGN KEY (tenant_account_id, chart_account_id)
        REFERENCES accounting_core.chart_account (tenant_account_id, chart_account_id),
    UNIQUE (
        tenant_account_id,
        accounting_book_id,
        account_role_code,
        accounting_policy_version,
        posting_rule_version,
        valid_from
    ),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE TABLE accounting_core.fiscal_calendar (
    fiscal_calendar_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL REFERENCES accounting_core.tenant_account (tenant_account_id),
    calendar_code text NOT NULL,
    calendar_name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (tenant_account_id, calendar_code),
    UNIQUE (tenant_account_id, fiscal_calendar_id)
);

CREATE TABLE accounting_core.fiscal_period (
    fiscal_period_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    fiscal_calendar_id uuid NOT NULL,
    period_code text NOT NULL,
    period_start_date date NOT NULL,
    period_end_date date NOT NULL,
    period_status_code text NOT NULL CHECK (period_status_code IN ('open', 'soft_closed', 'hard_closed')),
    period_closed_at timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, fiscal_calendar_id)
        REFERENCES accounting_core.fiscal_calendar (tenant_account_id, fiscal_calendar_id),
    UNIQUE (tenant_account_id, fiscal_calendar_id, period_code),
    UNIQUE (tenant_account_id, fiscal_period_id),
    CHECK (period_end_date >= period_start_date)
);

CREATE TABLE accounting_integration.journal_proposal_record (
    proposal_record_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL REFERENCES accounting_core.tenant_account (tenant_account_id),
    external_proposal_id uuid NOT NULL,
    proposal_contract_version integer NOT NULL CHECK (proposal_contract_version > 0),
    idempotency_key text NOT NULL,
    source_payload_hash text NOT NULL CHECK (source_payload_hash ~ '^sha256:[0-9a-f]{64}$'),
    proposal_status_code text NOT NULL CHECK (proposal_status_code IN ('received', 'validated', 'held', 'rejected', 'posted')),
    received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    processed_at timestamptz,
    UNIQUE (tenant_account_id, external_proposal_id),
    UNIQUE (tenant_account_id, idempotency_key),
    UNIQUE (tenant_account_id, proposal_record_id)
);

CREATE TABLE accounting_core.general_journal (
    general_journal_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    legal_entity_id uuid NOT NULL,
    accounting_book_id uuid NOT NULL,
    fiscal_period_id uuid NOT NULL,
    journal_reference text NOT NULL,
    journal_status_code text NOT NULL CHECK (journal_status_code IN ('posted', 'reversed')),
    transaction_currency_code text NOT NULL CHECK (transaction_currency_code ~ '^[A-Z]{3}$'),
    functional_currency_code text NOT NULL CHECK (functional_currency_code ~ '^[A-Z]{3}$'),
    transaction_date date NOT NULL,
    accounting_date date NOT NULL,
    source_proposal_record_id uuid NOT NULL,
    accounting_policy_version text NOT NULL,
    posting_rule_version text NOT NULL,
    posted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, legal_entity_id)
        REFERENCES accounting_core.legal_entity_record (tenant_account_id, legal_entity_id),
    FOREIGN KEY (tenant_account_id, accounting_book_id)
        REFERENCES accounting_core.accounting_book (tenant_account_id, accounting_book_id),
    FOREIGN KEY (tenant_account_id, fiscal_period_id)
        REFERENCES accounting_core.fiscal_period (tenant_account_id, fiscal_period_id),
    FOREIGN KEY (tenant_account_id, source_proposal_record_id)
        REFERENCES accounting_integration.journal_proposal_record (tenant_account_id, proposal_record_id),
    UNIQUE (tenant_account_id, journal_reference),
    UNIQUE (tenant_account_id, general_journal_id)
);

CREATE TABLE accounting_core.journal_entry_line (
    journal_entry_line_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    general_journal_id uuid NOT NULL,
    line_number integer NOT NULL CHECK (line_number > 0),
    chart_account_id uuid NOT NULL,
    account_role_code text NOT NULL,
    debit_amount numeric(38, 6) NOT NULL DEFAULT 0 CHECK (debit_amount >= 0),
    credit_amount numeric(38, 6) NOT NULL DEFAULT 0 CHECK (credit_amount >= 0),
    line_description text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, general_journal_id)
        REFERENCES accounting_core.general_journal (tenant_account_id, general_journal_id),
    FOREIGN KEY (tenant_account_id, chart_account_id)
        REFERENCES accounting_core.chart_account (tenant_account_id, chart_account_id),
    UNIQUE (tenant_account_id, general_journal_id, line_number),
    CHECK ((debit_amount > 0 AND credit_amount = 0) OR (debit_amount = 0 AND credit_amount > 0))
);

CREATE TABLE accounting_core.journal_source_reference (
    journal_source_reference_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    general_journal_id uuid NOT NULL,
    source_reference text NOT NULL,
    source_payload_hash text NOT NULL CHECK (source_payload_hash ~ '^sha256:[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, general_journal_id)
        REFERENCES accounting_core.general_journal (tenant_account_id, general_journal_id),
    UNIQUE (tenant_account_id, general_journal_id, source_reference)
);

CREATE TABLE accounting_core.journal_reversal (
    journal_reversal_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL REFERENCES accounting_core.tenant_account (tenant_account_id),
    original_journal_id uuid NOT NULL,
    reversal_journal_id uuid NOT NULL,
    reversal_reason_code text NOT NULL,
    reversed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, original_journal_id)
        REFERENCES accounting_core.general_journal (tenant_account_id, general_journal_id),
    FOREIGN KEY (tenant_account_id, reversal_journal_id)
        REFERENCES accounting_core.general_journal (tenant_account_id, general_journal_id),
    UNIQUE (tenant_account_id, original_journal_id),
    UNIQUE (tenant_account_id, reversal_journal_id),
    CHECK (original_journal_id <> reversal_journal_id)
);

CREATE TABLE accounting_integration.posting_receipt (
    posting_receipt_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    proposal_record_id uuid NOT NULL,
    general_journal_id uuid,
    receipt_status_code text NOT NULL CHECK (receipt_status_code IN ('posted', 'held', 'rejected', 'reversed')),
    receipt_payload_hash text NOT NULL CHECK (receipt_payload_hash ~ '^sha256:[0-9a-f]{64}$'),
    rejection_reason_code text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, proposal_record_id)
        REFERENCES accounting_integration.journal_proposal_record (tenant_account_id, proposal_record_id),
    FOREIGN KEY (tenant_account_id, general_journal_id)
        REFERENCES accounting_core.general_journal (tenant_account_id, general_journal_id),
    UNIQUE (tenant_account_id, proposal_record_id),
    UNIQUE (tenant_account_id, posting_receipt_id)
);

CREATE TABLE accounting_reporting.trial_balance_snapshot (
    trial_balance_snapshot_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    legal_entity_id uuid NOT NULL,
    accounting_book_id uuid NOT NULL,
    fiscal_period_id uuid NOT NULL,
    snapshot_currency_code text NOT NULL CHECK (snapshot_currency_code ~ '^[A-Z]{3}$'),
    snapshot_generated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    source_journal_count bigint NOT NULL CHECK (source_journal_count >= 0),
    source_payload_hash text NOT NULL CHECK (source_payload_hash ~ '^sha256:[0-9a-f]{64}$'),
    FOREIGN KEY (tenant_account_id, legal_entity_id)
        REFERENCES accounting_core.legal_entity_record (tenant_account_id, legal_entity_id),
    FOREIGN KEY (tenant_account_id, accounting_book_id)
        REFERENCES accounting_core.accounting_book (tenant_account_id, accounting_book_id),
    FOREIGN KEY (tenant_account_id, fiscal_period_id)
        REFERENCES accounting_core.fiscal_period (tenant_account_id, fiscal_period_id),
    UNIQUE (tenant_account_id, accounting_book_id, fiscal_period_id, snapshot_generated_at),
    UNIQUE (tenant_account_id, trial_balance_snapshot_id)
);

CREATE TABLE accounting_reporting.trial_balance_line (
    trial_balance_line_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    trial_balance_snapshot_id uuid NOT NULL,
    chart_account_id uuid NOT NULL,
    debit_total_amount numeric(38, 6) NOT NULL CHECK (debit_total_amount >= 0),
    credit_total_amount numeric(38, 6) NOT NULL CHECK (credit_total_amount >= 0),
    net_balance_amount numeric(38, 6) NOT NULL,
    FOREIGN KEY (tenant_account_id, trial_balance_snapshot_id)
        REFERENCES accounting_reporting.trial_balance_snapshot (tenant_account_id, trial_balance_snapshot_id),
    FOREIGN KEY (tenant_account_id, chart_account_id)
        REFERENCES accounting_core.chart_account (tenant_account_id, chart_account_id),
    UNIQUE (tenant_account_id, trial_balance_snapshot_id, chart_account_id)
);

CREATE TABLE accounting_integration.outbox_event (
    outbox_event_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid REFERENCES accounting_core.tenant_account (tenant_account_id),
    event_type_code text NOT NULL,
    aggregate_reference text NOT NULL,
    payload_reference text NOT NULL,
    payload_hash text NOT NULL CHECK (payload_hash ~ '^sha256:[0-9a-f]{64}$'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    published_at timestamptz
);

CREATE OR REPLACE FUNCTION accounting_core.current_tenant_account_id()
RETURNS uuid
LANGUAGE sql
STABLE
AS $$
    SELECT nullif(current_setting('app.tenant_account_id', true), '')::uuid
$$;

ALTER TABLE accounting_core.legal_entity_record ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.accounting_book ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.chart_account ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.account_role_mapping ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.fiscal_calendar ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.fiscal_period ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_integration.journal_proposal_record ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.general_journal ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.journal_entry_line ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.journal_source_reference ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.journal_reversal ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_integration.posting_receipt ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_reporting.trial_balance_snapshot ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_reporting.trial_balance_line ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_integration.outbox_event ENABLE ROW LEVEL SECURITY;

CREATE POLICY legal_entity_isolation ON accounting_core.legal_entity_record
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
CREATE POLICY accounting_book_isolation ON accounting_core.accounting_book
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
CREATE POLICY chart_account_isolation ON accounting_core.chart_account
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
CREATE POLICY account_mapping_isolation ON accounting_core.account_role_mapping
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
CREATE POLICY fiscal_calendar_isolation ON accounting_core.fiscal_calendar
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
CREATE POLICY fiscal_period_isolation ON accounting_core.fiscal_period
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
CREATE POLICY journal_proposal_isolation ON accounting_integration.journal_proposal_record
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
CREATE POLICY general_journal_isolation ON accounting_core.general_journal
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
CREATE POLICY journal_entry_isolation ON accounting_core.journal_entry_line
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
CREATE POLICY journal_source_isolation ON accounting_core.journal_source_reference
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
CREATE POLICY journal_reversal_isolation ON accounting_core.journal_reversal
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
CREATE POLICY posting_receipt_isolation ON accounting_integration.posting_receipt
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
CREATE POLICY trial_snapshot_isolation ON accounting_reporting.trial_balance_snapshot
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
CREATE POLICY trial_line_isolation ON accounting_reporting.trial_balance_line
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
CREATE POLICY outbox_event_isolation ON accounting_integration.outbox_event
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

COMMIT;
