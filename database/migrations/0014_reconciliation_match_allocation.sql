BEGIN;

ALTER TABLE accounting_core.reconciliation_run
    ADD CONSTRAINT reconciliation_run_currency_identity_unique
    UNIQUE (tenant_account_id, reconciliation_run_id, currency_code);

CREATE TABLE accounting_core.reconciliation_match (
    reconciliation_match_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    currency_code text NOT NULL CHECK (currency_code ~ '^[A-Z]{3}$'),
    matching_rule_code text NOT NULL CHECK (btrim(matching_rule_code) <> ''),
    proposed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, reconciliation_run_id, currency_code)
        REFERENCES accounting_core.reconciliation_run (
            tenant_account_id, reconciliation_run_id, currency_code
        ),
    UNIQUE (tenant_account_id, reconciliation_run_id, reconciliation_match_id),
    UNIQUE (
        tenant_account_id, reconciliation_run_id, reconciliation_match_id, currency_code
    )
);

CREATE TABLE accounting_core.statement_match_allocation (
    statement_match_allocation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    reconciliation_match_id uuid NOT NULL,
    bank_statement_entry_id uuid NOT NULL,
    allocated_amount numeric(38, 6) NOT NULL CHECK (allocated_amount > 0),
    currency_code text NOT NULL CHECK (currency_code ~ '^[A-Z]{3}$'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, reconciliation_run_id, reconciliation_match_id)
        REFERENCES accounting_core.reconciliation_match (
            tenant_account_id, reconciliation_run_id, reconciliation_match_id
        ),
    FOREIGN KEY (
        tenant_account_id, reconciliation_run_id, reconciliation_match_id, currency_code
    ) REFERENCES accounting_core.reconciliation_match (
        tenant_account_id, reconciliation_run_id, reconciliation_match_id, currency_code
    ),
    FOREIGN KEY (tenant_account_id, bank_statement_entry_id)
        REFERENCES accounting_integration.bank_statement_entry (
            tenant_account_id, bank_statement_entry_id
        ),
    UNIQUE (
        tenant_account_id, reconciliation_run_id, reconciliation_match_id,
        bank_statement_entry_id
    )
);

CREATE TABLE accounting_core.journal_match_allocation (
    journal_match_allocation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    reconciliation_match_id uuid NOT NULL,
    general_journal_id uuid NOT NULL,
    allocated_amount numeric(38, 6) NOT NULL CHECK (allocated_amount > 0),
    currency_code text NOT NULL CHECK (currency_code ~ '^[A-Z]{3}$'),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, reconciliation_run_id, reconciliation_match_id)
        REFERENCES accounting_core.reconciliation_match (
            tenant_account_id, reconciliation_run_id, reconciliation_match_id
        ),
    FOREIGN KEY (
        tenant_account_id, reconciliation_run_id, reconciliation_match_id, currency_code
    ) REFERENCES accounting_core.reconciliation_match (
        tenant_account_id, reconciliation_run_id, reconciliation_match_id, currency_code
    ),
    FOREIGN KEY (tenant_account_id, general_journal_id)
        REFERENCES accounting_core.general_journal (tenant_account_id, general_journal_id),
    UNIQUE (
        tenant_account_id, reconciliation_run_id, reconciliation_match_id,
        general_journal_id
    )
);

CREATE INDEX reconciliation_match_run_order_index
    ON accounting_core.reconciliation_match (
        tenant_account_id, reconciliation_run_id, proposed_at, reconciliation_match_id
    );

CREATE INDEX statement_match_allocation_source_index
    ON accounting_core.statement_match_allocation (
        tenant_account_id, bank_statement_entry_id, reconciliation_run_id,
        reconciliation_match_id
    );

CREATE INDEX journal_match_allocation_source_index
    ON accounting_core.journal_match_allocation (
        tenant_account_id, general_journal_id, reconciliation_run_id,
        reconciliation_match_id
    );

CREATE OR REPLACE FUNCTION accounting_core.guard_statement_match_allocation_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    run_assignment_id uuid;
    run_currency_code text;
    assigned_bank_account_id uuid;
    statement_bank_account_id uuid;
    statement_currency_code text;
BEGIN
    SELECT bank_account_assignment_id, currency_code
    INTO run_assignment_id, run_currency_code
    FROM accounting_core.reconciliation_run
    WHERE tenant_account_id = NEW.tenant_account_id
      AND reconciliation_run_id = NEW.reconciliation_run_id;

    SELECT bank_account_record_id
    INTO assigned_bank_account_id
    FROM accounting_core.bank_account_assignment
    WHERE tenant_account_id = NEW.tenant_account_id
      AND bank_account_assignment_id = run_assignment_id;

    SELECT bank_statement_record.bank_account_record_id,
           bank_statement_entry.entry_currency_code
    INTO statement_bank_account_id, statement_currency_code
    FROM accounting_integration.bank_statement_entry
    JOIN accounting_integration.bank_statement_record
      ON bank_statement_record.tenant_account_id
         = bank_statement_entry.tenant_account_id
     AND bank_statement_record.bank_statement_record_id
         = bank_statement_entry.bank_statement_record_id
    WHERE bank_statement_entry.tenant_account_id = NEW.tenant_account_id
      AND bank_statement_entry.bank_statement_entry_id = NEW.bank_statement_entry_id;

    IF assigned_bank_account_id IS NULL
       OR statement_bank_account_id IS NULL
       OR assigned_bank_account_id <> statement_bank_account_id
       OR run_currency_code <> NEW.currency_code
       OR statement_currency_code <> NEW.currency_code THEN
        RAISE EXCEPTION
            'statement allocation must belong to the reconciliation run bank account and currency (reconciliation_scope_mismatch)'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION accounting_core.guard_journal_match_allocation_scope()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    run_legal_entity_id uuid;
    run_accounting_book_id uuid;
    run_currency_code text;
    journal_legal_entity_id uuid;
    journal_accounting_book_id uuid;
    journal_currency_code text;
BEGIN
    SELECT legal_entity_id, accounting_book_id, currency_code
    INTO run_legal_entity_id, run_accounting_book_id, run_currency_code
    FROM accounting_core.reconciliation_run
    WHERE tenant_account_id = NEW.tenant_account_id
      AND reconciliation_run_id = NEW.reconciliation_run_id;

    SELECT legal_entity_id, accounting_book_id, transaction_currency_code
    INTO journal_legal_entity_id, journal_accounting_book_id, journal_currency_code
    FROM accounting_core.general_journal
    WHERE tenant_account_id = NEW.tenant_account_id
      AND general_journal_id = NEW.general_journal_id;

    IF journal_legal_entity_id IS NULL
       OR run_legal_entity_id <> journal_legal_entity_id
       OR run_accounting_book_id <> journal_accounting_book_id
       OR run_currency_code <> NEW.currency_code
       OR journal_currency_code <> NEW.currency_code THEN
        RAISE EXCEPTION
            'journal allocation must belong to the reconciliation run legal entity, book, and currency (reconciliation_scope_mismatch)'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER statement_match_allocation_scope_guard
BEFORE INSERT ON accounting_core.statement_match_allocation
FOR EACH ROW EXECUTE FUNCTION accounting_core.guard_statement_match_allocation_scope();

CREATE TRIGGER journal_match_allocation_scope_guard
BEFORE INSERT ON accounting_core.journal_match_allocation
FOR EACH ROW EXECUTE FUNCTION accounting_core.guard_journal_match_allocation_scope();

CREATE OR REPLACE FUNCTION accounting_core.assert_reconciliation_match_conservation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    statement_count bigint;
    journal_count bigint;
    statement_total numeric(38, 6);
    journal_total numeric(38, 6);
BEGIN
    SELECT COUNT(*), COALESCE(SUM(allocated_amount), 0)
    INTO statement_count, statement_total
    FROM accounting_core.statement_match_allocation
    WHERE tenant_account_id = NEW.tenant_account_id
      AND reconciliation_run_id = NEW.reconciliation_run_id
      AND reconciliation_match_id = NEW.reconciliation_match_id;

    SELECT COUNT(*), COALESCE(SUM(allocated_amount), 0)
    INTO journal_count, journal_total
    FROM accounting_core.journal_match_allocation
    WHERE tenant_account_id = NEW.tenant_account_id
      AND reconciliation_run_id = NEW.reconciliation_run_id
      AND reconciliation_match_id = NEW.reconciliation_match_id;

    IF statement_count = 0
       OR journal_count = 0
       OR statement_total <> journal_total THEN
        RAISE EXCEPTION
            'reconciliation match allocations must conserve exact statement and journal totals (reconciliation_allocation_unbalanced)'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER reconciliation_match_conservation_guard
AFTER INSERT ON accounting_core.reconciliation_match
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION accounting_core.assert_reconciliation_match_conservation();

CREATE CONSTRAINT TRIGGER statement_match_conservation_guard
AFTER INSERT ON accounting_core.statement_match_allocation
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION accounting_core.assert_reconciliation_match_conservation();

CREATE CONSTRAINT TRIGGER journal_match_conservation_guard
AFTER INSERT ON accounting_core.journal_match_allocation
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION accounting_core.assert_reconciliation_match_conservation();

CREATE OR REPLACE FUNCTION accounting_core.reject_reconciliation_allocation_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'reconciliation match and allocation evidence is append-only; record superseding evidence instead'
        USING ERRCODE = 'check_violation';
END;
$$;

CREATE TRIGGER reconciliation_match_immutable_guard
BEFORE UPDATE OR DELETE ON accounting_core.reconciliation_match
FOR EACH ROW EXECUTE FUNCTION accounting_core.reject_reconciliation_allocation_mutation();

CREATE TRIGGER statement_match_allocation_immutable_guard
BEFORE UPDATE OR DELETE ON accounting_core.statement_match_allocation
FOR EACH ROW EXECUTE FUNCTION accounting_core.reject_reconciliation_allocation_mutation();

CREATE TRIGGER journal_match_allocation_immutable_guard
BEFORE UPDATE OR DELETE ON accounting_core.journal_match_allocation
FOR EACH ROW EXECUTE FUNCTION accounting_core.reject_reconciliation_allocation_mutation();

ALTER TABLE accounting_core.reconciliation_match ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.statement_match_allocation ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.journal_match_allocation ENABLE ROW LEVEL SECURITY;

ALTER TABLE accounting_core.reconciliation_match FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.statement_match_allocation FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.journal_match_allocation FORCE ROW LEVEL SECURITY;

CREATE POLICY reconciliation_match_tenant_isolation
ON accounting_core.reconciliation_match
USING (
    tenant_account_id = accounting_core.current_tenant_account_id()
)
WITH CHECK (
    tenant_account_id = accounting_core.current_tenant_account_id()
);

CREATE POLICY statement_match_allocation_tenant_isolation
ON accounting_core.statement_match_allocation
USING (
    tenant_account_id = accounting_core.current_tenant_account_id()
)
WITH CHECK (
    tenant_account_id = accounting_core.current_tenant_account_id()
);

CREATE POLICY journal_match_allocation_tenant_isolation
ON accounting_core.journal_match_allocation
USING (
    tenant_account_id = accounting_core.current_tenant_account_id()
)
WITH CHECK (
    tenant_account_id = accounting_core.current_tenant_account_id()
);

REVOKE ALL ON accounting_core.reconciliation_match FROM PUBLIC;
REVOKE ALL ON accounting_core.statement_match_allocation FROM PUBLIC;
REVOKE ALL ON accounting_core.journal_match_allocation FROM PUBLIC;

COMMIT;
