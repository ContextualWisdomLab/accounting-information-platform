BEGIN;

-- Database-owned source authority for financial-report construction.
--
-- This migration binds a report run to an AIS tenant, legal entity, accounting
-- book, book-scoped fiscal period state, database observation cutoff, and one or
-- more retained trial-balance snapshots. It deliberately stops before
-- independent XBRL validation, maker-checker workflow, filing readiness, or
-- regulator/customer receipts. A caller-supplied report proposal, digest,
-- currency, period status, cutoff, recording time, or status label cannot
-- satisfy these database-owned controls.

CREATE TABLE accounting_reporting.financial_report_run (
    financial_report_run_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    legal_entity_id uuid NOT NULL,
    accounting_book_id uuid NOT NULL,
    fiscal_period_id uuid NOT NULL,
    comparison_fiscal_period_id uuid,
    reporting_currency_code text NOT NULL CHECK (reporting_currency_code ~ '^[A-Z]{3}$'),
    source_period_status_code text NOT NULL
        CHECK (source_period_status_code IN ('open', 'soft_closed', 'hard_closed')),
    knowledge_cutoff_at timestamptz NOT NULL,
    report_purpose_code text NOT NULL CHECK (btrim(report_purpose_code) <> ''),
    run_status_code text NOT NULL
        CHECK (run_status_code IN ('collecting_sources', 'superseded')),
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
    FOREIGN KEY (tenant_account_id, fiscal_period_id)
        REFERENCES accounting_core.fiscal_period (
            tenant_account_id,
            fiscal_period_id
        ),
    FOREIGN KEY (tenant_account_id, comparison_fiscal_period_id)
        REFERENCES accounting_core.fiscal_period (
            tenant_account_id,
            fiscal_period_id
        ),
    FOREIGN KEY (tenant_account_id, accounting_book_id, fiscal_period_id)
        REFERENCES accounting_core.accounting_book_period_control (
            tenant_account_id,
            accounting_book_id,
            fiscal_period_id
        ),
    FOREIGN KEY (
        tenant_account_id,
        accounting_book_id,
        comparison_fiscal_period_id
    )
        REFERENCES accounting_core.accounting_book_period_control (
            tenant_account_id,
            accounting_book_id,
            fiscal_period_id
        ),
    UNIQUE (tenant_account_id, financial_report_run_id),
    CHECK (
        comparison_fiscal_period_id IS NULL
        OR comparison_fiscal_period_id <> fiscal_period_id
    )
);

CREATE TABLE accounting_reporting.financial_report_source (
    financial_report_source_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    financial_report_run_id uuid NOT NULL,
    period_context_code text NOT NULL
        CHECK (period_context_code IN ('current', 'comparison')),
    trial_balance_snapshot_id uuid NOT NULL,
    source_role_code text NOT NULL
        CHECK (source_role_code IN ('financial_statement_population')),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, financial_report_run_id)
        REFERENCES accounting_reporting.financial_report_run (
            tenant_account_id,
            financial_report_run_id
        ),
    FOREIGN KEY (tenant_account_id, trial_balance_snapshot_id)
        REFERENCES accounting_reporting.trial_balance_snapshot (
            tenant_account_id,
            trial_balance_snapshot_id
        ),
    UNIQUE (tenant_account_id, financial_report_run_id, period_context_code),
    UNIQUE (tenant_account_id, financial_report_source_id)
);

CREATE INDEX financial_report_run_scope_index
    ON accounting_reporting.financial_report_run (
        tenant_account_id,
        legal_entity_id,
        accounting_book_id,
        fiscal_period_id,
        knowledge_cutoff_at,
        financial_report_run_id
    );

CREATE INDEX financial_report_source_run_index
    ON accounting_reporting.financial_report_source (
        tenant_account_id,
        financial_report_run_id,
        period_context_code,
        financial_report_source_id
    );

CREATE OR REPLACE FUNCTION accounting_reporting.bind_financial_report_run_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    book_currency_value text;
    period_status_value text;
BEGIN
    SELECT book_record.reporting_currency_code
      INTO book_currency_value
      FROM accounting_core.accounting_book AS book_record
     WHERE book_record.tenant_account_id = NEW.tenant_account_id
       AND book_record.legal_entity_id = NEW.legal_entity_id
       AND book_record.accounting_book_id = NEW.accounting_book_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'financial report accounting scope does not exist (financial_report_scope_invalid)'
            USING ERRCODE = '23503';
    END IF;

    SELECT period_control.period_status_code
      INTO period_status_value
      FROM accounting_core.accounting_book_period_control AS period_control
     WHERE period_control.tenant_account_id = NEW.tenant_account_id
       AND period_control.accounting_book_id = NEW.accounting_book_id
       AND period_control.fiscal_period_id = NEW.fiscal_period_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'financial report accounting-book fiscal period does not exist (financial_report_book_period_invalid)'
            USING ERRCODE = '23503';
    END IF;

    -- Currency, current book-period state, observation cutoff, system recording
    -- time, and initial lifecycle are database evidence. Caller-supplied values
    -- are overwritten before insert.
    NEW.reporting_currency_code := book_currency_value;
    NEW.source_period_status_code := period_status_value;
    NEW.knowledge_cutoff_at := clock_timestamp();
    NEW.recorded_at := clock_timestamp();
    NEW.run_status_code := 'collecting_sources';
    RETURN NEW;
END;
$$;

CREATE TRIGGER financial_report_run_binding_guard
    BEFORE INSERT ON accounting_reporting.financial_report_run
    FOR EACH ROW
    EXECUTE FUNCTION accounting_reporting.bind_financial_report_run_scope();

CREATE OR REPLACE FUNCTION accounting_reporting.reject_financial_report_run_scope_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.tenant_account_id IS DISTINCT FROM OLD.tenant_account_id
       OR NEW.legal_entity_id IS DISTINCT FROM OLD.legal_entity_id
       OR NEW.accounting_book_id IS DISTINCT FROM OLD.accounting_book_id
       OR NEW.fiscal_period_id IS DISTINCT FROM OLD.fiscal_period_id
       OR NEW.comparison_fiscal_period_id IS DISTINCT FROM OLD.comparison_fiscal_period_id
       OR NEW.reporting_currency_code IS DISTINCT FROM OLD.reporting_currency_code
       OR NEW.source_period_status_code IS DISTINCT FROM OLD.source_period_status_code
       OR NEW.knowledge_cutoff_at IS DISTINCT FROM OLD.knowledge_cutoff_at
       OR NEW.report_purpose_code IS DISTINCT FROM OLD.report_purpose_code
       OR NEW.recorded_at IS DISTINCT FROM OLD.recorded_at THEN
        RAISE EXCEPTION
            'financial report source scope is immutable; create a new run instead (financial_report_scope_immutable)'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER financial_report_run_scope_guard
    BEFORE UPDATE OF
        tenant_account_id,
        legal_entity_id,
        accounting_book_id,
        fiscal_period_id,
        comparison_fiscal_period_id,
        reporting_currency_code,
        source_period_status_code,
        knowledge_cutoff_at,
        report_purpose_code,
        recorded_at
    ON accounting_reporting.financial_report_run
    FOR EACH ROW
    EXECUTE FUNCTION accounting_reporting.reject_financial_report_run_scope_mutation();

CREATE OR REPLACE FUNCTION accounting_reporting.validate_financial_report_source_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    report_record accounting_reporting.financial_report_run%ROWTYPE;
    snapshot_record accounting_reporting.trial_balance_snapshot%ROWTYPE;
    expected_period_id uuid;
BEGIN
    SELECT *
      INTO report_record
      FROM accounting_reporting.financial_report_run
     WHERE tenant_account_id = NEW.tenant_account_id
       AND financial_report_run_id = NEW.financial_report_run_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'financial report run does not exist (financial_report_run_missing)'
            USING ERRCODE = '23503';
    END IF;

    SELECT *
      INTO snapshot_record
      FROM accounting_reporting.trial_balance_snapshot
     WHERE tenant_account_id = NEW.tenant_account_id
       AND trial_balance_snapshot_id = NEW.trial_balance_snapshot_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'financial report source snapshot does not exist (financial_report_snapshot_missing)'
            USING ERRCODE = '23503';
    END IF;

    IF NEW.period_context_code = 'current' THEN
        expected_period_id := report_record.fiscal_period_id;
    ELSE
        expected_period_id := report_record.comparison_fiscal_period_id;
        IF expected_period_id IS NULL THEN
            RAISE EXCEPTION
                'comparison source requires a comparison fiscal period (financial_report_comparison_period_missing)'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    IF snapshot_record.legal_entity_id <> report_record.legal_entity_id
       OR snapshot_record.accounting_book_id <> report_record.accounting_book_id
       OR snapshot_record.fiscal_period_id <> expected_period_id THEN
        RAISE EXCEPTION
            'source snapshot scope does not match financial report run (financial_report_source_scope_invalid)'
            USING ERRCODE = '23514';
    END IF;

    IF snapshot_record.snapshot_currency_code <> report_record.reporting_currency_code THEN
        RAISE EXCEPTION
            'source snapshot currency does not match financial report run (financial_report_source_currency_invalid)'
            USING ERRCODE = '23514';
    END IF;

    IF snapshot_record.snapshot_generated_at > report_record.knowledge_cutoff_at THEN
        RAISE EXCEPTION
            'source snapshot is newer than the report knowledge cutoff (financial_report_future_source)'
            USING ERRCODE = '23514';
    END IF;

    -- The source-link recording timestamp is system time, not caller chronology.
    NEW.recorded_at := clock_timestamp();
    RETURN NEW;
END;
$$;

CREATE TRIGGER financial_report_source_scope_guard
    BEFORE INSERT ON accounting_reporting.financial_report_source
    FOR EACH ROW
    EXECUTE FUNCTION accounting_reporting.validate_financial_report_source_scope();

CREATE OR REPLACE FUNCTION accounting_reporting.reject_financial_report_source_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'financial report source evidence is append-only; create a new report run instead (financial_report_source_immutable)'
        USING ERRCODE = '23514';
END;
$$;

CREATE TRIGGER financial_report_source_immutability_guard
    BEFORE UPDATE OR DELETE ON accounting_reporting.financial_report_source
    FOR EACH ROW
    EXECUTE FUNCTION accounting_reporting.reject_financial_report_source_mutation();

ALTER TABLE accounting_reporting.financial_report_run ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_reporting.financial_report_run FORCE ROW LEVEL SECURITY;
CREATE POLICY financial_report_run_isolation ON accounting_reporting.financial_report_run
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

ALTER TABLE accounting_reporting.financial_report_source ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_reporting.financial_report_source FORCE ROW LEVEL SECURITY;
CREATE POLICY financial_report_source_isolation ON accounting_reporting.financial_report_source
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

REVOKE ALL ON accounting_reporting.financial_report_run FROM PUBLIC;
REVOKE ALL ON accounting_reporting.financial_report_source FROM PUBLIC;

COMMIT;
