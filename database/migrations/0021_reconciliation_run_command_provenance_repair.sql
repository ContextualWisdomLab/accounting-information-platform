BEGIN;

-- Apply the run-command provenance guards to installations that already
-- executed migration 0019 before the command-insert guard was added.
CREATE OR REPLACE FUNCTION accounting_core.enforce_reconciliation_run_command_provenance()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    command_count integer;
BEGIN
    SELECT count(*)
    INTO command_count
    FROM accounting_core.reconciliation_run_command AS command
    WHERE command.tenant_account_id = NEW.tenant_account_id
      AND command.reconciliation_run_id = NEW.reconciliation_run_id;

    IF command_count <> 1 THEN
        RAISE EXCEPTION
            'reconciliation run must have exactly one command evidence row at commit (reconciliation_run_command_provenance)'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_run_command AS command
        JOIN accounting_integration.bank_statement_record AS statement
          ON statement.tenant_account_id = command.tenant_account_id
         AND statement.bank_statement_record_id = command.bank_statement_record_id
        JOIN accounting_core.reconciliation_run AS run
          ON run.tenant_account_id = command.tenant_account_id
         AND run.reconciliation_run_id = command.reconciliation_run_id
        JOIN accounting_core.bank_account_assignment AS assignment
          ON assignment.tenant_account_id = run.tenant_account_id
         AND assignment.legal_entity_id = run.legal_entity_id
         AND assignment.accounting_book_id = run.accounting_book_id
         AND assignment.bank_account_assignment_id = run.bank_account_assignment_id
        WHERE command.tenant_account_id = NEW.tenant_account_id
          AND command.reconciliation_run_id = NEW.reconciliation_run_id
          AND statement.bank_account_record_id IS DISTINCT FROM assignment.bank_account_record_id
    ) THEN
        RAISE EXCEPTION
            'reconciliation run command bank account provenance does not match the run assignment (reconciliation_run_command_provenance)'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS reconciliation_run_command_provenance_guard
    ON accounting_core.reconciliation_run;
CREATE CONSTRAINT TRIGGER reconciliation_run_command_provenance_guard
    AFTER INSERT ON accounting_core.reconciliation_run
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.enforce_reconciliation_run_command_provenance();

DROP TRIGGER IF EXISTS reconciliation_run_command_provenance_insert_guard
    ON accounting_core.reconciliation_run_command;
CREATE TRIGGER reconciliation_run_command_provenance_insert_guard
    AFTER INSERT ON accounting_core.reconciliation_run_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.enforce_reconciliation_run_command_provenance();

COMMIT;
