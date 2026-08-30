BEGIN;

-- Immutable application command identity for a proposed reconciliation match.
-- This records reviewable candidate evidence only; it cannot approve, close, or
-- post a journal.

-- Command provenance must name the candidate actually referenced by the match.
-- The existing match primary key proves row identity, while this tenant/run
-- composite key gives downstream evidence a database-owned same-chain target.
ALTER TABLE accounting_core.reconciliation_match
    ADD CONSTRAINT reconciliation_match_candidate_chain_unique
    UNIQUE (
        tenant_account_id,
        reconciliation_run_id,
        reconciliation_match_id,
        reconciliation_candidate_id
    );

CREATE TABLE accounting_core.reconciliation_match_command (
    reconciliation_match_command_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    reconciliation_candidate_id uuid NOT NULL,
    reconciliation_match_id uuid NOT NULL,
    candidate_idempotency_key text NOT NULL
        CHECK (btrim(candidate_idempotency_key) <> ''),
    candidate_command_hash text NOT NULL
        CHECK (candidate_command_hash ~ '^sha256:[0-9a-f]{64}$'),
    source_payload_hash text NOT NULL
        CHECK (source_payload_hash ~ '^sha256:[0-9a-f]{64}$'),
    source_payload_reference text NOT NULL
        CHECK (btrim(source_payload_reference) <> ''),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (
        tenant_account_id,
        reconciliation_run_id,
        reconciliation_candidate_id
    ) REFERENCES accounting_core.reconciliation_candidate (
        tenant_account_id,
        reconciliation_run_id,
        reconciliation_candidate_id
    ),
    FOREIGN KEY (
        tenant_account_id,
        reconciliation_run_id,
        reconciliation_match_id,
        reconciliation_candidate_id
    ) REFERENCES accounting_core.reconciliation_match (
        tenant_account_id,
        reconciliation_run_id,
        reconciliation_match_id,
        reconciliation_candidate_id
    ),
    UNIQUE (tenant_account_id, candidate_idempotency_key),
    UNIQUE (tenant_account_id, reconciliation_run_id, reconciliation_match_id)
);

CREATE INDEX reconciliation_match_command_run_index
    ON accounting_core.reconciliation_match_command (
        tenant_account_id,
        reconciliation_run_id,
        recorded_at,
        reconciliation_match_command_id
    );

ALTER TABLE accounting_core.reconciliation_match_command ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.reconciliation_match_command FORCE ROW LEVEL SECURITY;

CREATE POLICY reconciliation_match_command_isolation
    ON accounting_core.reconciliation_match_command
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

REVOKE ALL ON accounting_core.reconciliation_match_command FROM PUBLIC;

CREATE OR REPLACE FUNCTION accounting_core.reject_reconciliation_match_command_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'recorded reconciliation match command evidence is immutable; create a new proposed match instead (reconciliation_match_command_immutable)'
        USING ERRCODE = '23514';
END;
$$;

CREATE TRIGGER reconciliation_match_command_immutability_guard
BEFORE UPDATE OR DELETE
ON accounting_core.reconciliation_match_command
FOR EACH ROW EXECUTE FUNCTION accounting_core.reject_reconciliation_match_command_mutation();

CREATE OR REPLACE FUNCTION accounting_core.enforce_reconciliation_match_command_allocations()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    statement_allocation_count bigint;
    journal_allocation_count bigint;
    statement_allocation_total numeric(30, 6);
    journal_allocation_total numeric(30, 6);
    candidate_statement_amount numeric(30, 6);
    candidate_journal_amount numeric(30, 6);
BEGIN
    -- Share the parent match lock with the allocation conservation trigger so
    -- command insertion and a concurrent allocation cannot both commit.
    PERFORM 1
    FROM accounting_core.reconciliation_match AS match
    WHERE match.tenant_account_id = NEW.tenant_account_id
      AND match.reconciliation_run_id = NEW.reconciliation_run_id
      AND match.reconciliation_match_id = NEW.reconciliation_match_id
    FOR UPDATE;

    SELECT candidate.statement_amount, candidate.journal_amount
    INTO candidate_statement_amount, candidate_journal_amount
    FROM accounting_core.reconciliation_candidate AS candidate
    WHERE candidate.tenant_account_id = NEW.tenant_account_id
      AND candidate.reconciliation_run_id = NEW.reconciliation_run_id
      AND candidate.reconciliation_candidate_id = NEW.reconciliation_candidate_id;

    SELECT COUNT(*), COALESCE(SUM(allocation.allocated_amount), 0)
    INTO statement_allocation_count, statement_allocation_total
    FROM accounting_core.statement_match_allocation AS allocation
    WHERE allocation.tenant_account_id = NEW.tenant_account_id
      AND allocation.reconciliation_run_id = NEW.reconciliation_run_id
      AND allocation.reconciliation_match_id = NEW.reconciliation_match_id;

    SELECT COUNT(*), COALESCE(SUM(allocation.allocated_amount), 0)
    INTO journal_allocation_count, journal_allocation_total
    FROM accounting_core.journal_match_allocation AS allocation
    WHERE allocation.tenant_account_id = NEW.tenant_account_id
      AND allocation.reconciliation_run_id = NEW.reconciliation_run_id
      AND allocation.reconciliation_match_id = NEW.reconciliation_match_id;

    IF statement_allocation_count <> 1
       OR journal_allocation_count <> 1
       OR statement_allocation_total <> journal_allocation_total
       OR statement_allocation_total <> candidate_statement_amount
       OR journal_allocation_total <> candidate_journal_amount THEN
        RAISE EXCEPTION
            'reconciliation match command requires exactly one statement and one journal allocation matching candidate amounts (reconciliation_match_command_allocation)'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER z_reconciliation_match_command_allocation_guard
AFTER INSERT
ON accounting_core.reconciliation_match_command
FOR EACH ROW EXECUTE FUNCTION accounting_core.enforce_reconciliation_match_command_allocations();

CREATE OR REPLACE FUNCTION accounting_core.reject_reconciliation_match_command_allocation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_match_command AS command
        WHERE command.tenant_account_id = NEW.tenant_account_id
          AND command.reconciliation_run_id = NEW.reconciliation_run_id
          AND command.reconciliation_match_id = NEW.reconciliation_match_id
    ) THEN
        RAISE EXCEPTION
            'reconciliation match command evidence freezes its allocation population; create a new proposed match instead (reconciliation_match_command_allocation_frozen)'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER z_reconciliation_match_command_allocation_frozen_guard
BEFORE INSERT
ON accounting_core.statement_match_allocation
FOR EACH ROW EXECUTE FUNCTION accounting_core.reject_reconciliation_match_command_allocation();

CREATE TRIGGER z_reconciliation_match_command_allocation_frozen_guard
BEFORE INSERT
ON accounting_core.journal_match_allocation
FOR EACH ROW EXECUTE FUNCTION accounting_core.reject_reconciliation_match_command_allocation();

COMMIT;
