BEGIN;

-- Durable reconciliation candidate, match, and many-to-many allocation.
--
-- This migration persists the deterministic candidate plan and the approved
-- match with exact split/aggregate allocations. Conservation is enforced
-- relationally: a run may host at most one approved match, and every
-- allocation row keeps exact positive amounts with statement and journal
-- identity preserved. The slice still grants no journal-posting or
-- adjustment authority; it records evidence an operator reviews.

CREATE TABLE accounting_core.reconciliation_candidate (
    reconciliation_candidate_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    statement_entry_reference text NOT NULL
        CHECK (btrim(statement_entry_reference) <> ''),
    journal_reference text NOT NULL
        CHECK (btrim(journal_reference) <> ''),
    statement_amount numeric(30, 6) NOT NULL
        CHECK (statement_amount > 0),
    journal_amount numeric(30, 6) NOT NULL
        CHECK (journal_amount > 0),
    rule_code text NOT NULL
        CHECK (btrim(rule_code) <> ''),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, reconciliation_run_id)
        REFERENCES accounting_core.reconciliation_run (
            tenant_account_id,
            reconciliation_run_id
        ),
    UNIQUE (
        tenant_account_id,
        reconciliation_run_id,
        statement_entry_reference,
        journal_reference
    )
);

CREATE INDEX reconciliation_candidate_run_reference_index
    ON accounting_core.reconciliation_candidate (
        tenant_account_id,
        reconciliation_run_id,
        statement_entry_reference
    );

CREATE TABLE accounting_core.reconciliation_match (
    reconciliation_match_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    reconciliation_candidate_id uuid NOT NULL,
    match_status_code text NOT NULL
        CHECK (
            match_status_code IN (
                'proposed',
                'approved',
                'rejected',
                'superseded'
            )
        ),
    approved_at timestamptz,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, reconciliation_run_id)
        REFERENCES accounting_core.reconciliation_run (
            tenant_account_id,
            reconciliation_run_id
        ),
    FOREIGN KEY (reconciliation_candidate_id)
        REFERENCES accounting_core.reconciliation_candidate (
            reconciliation_candidate_id
        ),
    UNIQUE (
        tenant_account_id,
        reconciliation_run_id,
        reconciliation_candidate_id
    )
);

CREATE UNIQUE INDEX reconciliation_match_approved_single
    ON accounting_core.reconciliation_match (
        tenant_account_id,
        reconciliation_run_id
    )
    WHERE match_status_code = 'approved';

CREATE TABLE accounting_core.statement_match_allocation (
    reconciliation_allocation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    reconciliation_match_id uuid NOT NULL,
    statement_entry_reference text NOT NULL
        CHECK (btrim(statement_entry_reference) <> ''),
    allocated_amount numeric(30, 6) NOT NULL
        CHECK (allocated_amount > 0),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, reconciliation_run_id)
        REFERENCES accounting_core.reconciliation_run (
            tenant_account_id,
            reconciliation_run_id
        ),
    FOREIGN KEY (reconciliation_match_id)
        REFERENCES accounting_core.reconciliation_match (
            reconciliation_match_id
        )
);

CREATE TABLE accounting_core.journal_match_allocation (
    reconciliation_allocation_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    reconciliation_match_id uuid NOT NULL,
    journal_reference text NOT NULL
        CHECK (btrim(journal_reference) <> ''),
    allocated_amount numeric(30, 6) NOT NULL
        CHECK (allocated_amount > 0),
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (tenant_account_id, reconciliation_run_id)
        REFERENCES accounting_core.reconciliation_run (
            tenant_account_id,
            reconciliation_run_id
        ),
    FOREIGN KEY (reconciliation_match_id)
        REFERENCES accounting_core.reconciliation_match (
            reconciliation_match_id
        )
);

CREATE INDEX reconciliation_allocation_run_reference_index
    ON accounting_core.statement_match_allocation (
        tenant_account_id,
        reconciliation_run_id,
        statement_entry_reference,
        recorded_at,
        reconciliation_allocation_id
    );

CREATE INDEX journal_allocation_run_reference_index
    ON accounting_core.journal_match_allocation (
        tenant_account_id,
        reconciliation_run_id,
        journal_reference,
        recorded_at,
        reconciliation_allocation_id
    );

ALTER TABLE accounting_core.reconciliation_candidate ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.reconciliation_candidate FORCE ROW LEVEL SECURITY;
CREATE POLICY reconciliation_candidate_isolation
    ON accounting_core.reconciliation_candidate
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
ALTER TABLE accounting_core.reconciliation_match ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.reconciliation_match FORCE ROW LEVEL SECURITY;
CREATE POLICY reconciliation_match_isolation
    ON accounting_core.reconciliation_match
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
ALTER TABLE accounting_core.statement_match_allocation ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.statement_match_allocation FORCE ROW LEVEL SECURITY;
CREATE POLICY statement_match_allocation_isolation
    ON accounting_core.statement_match_allocation
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());
ALTER TABLE accounting_core.journal_match_allocation ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.journal_match_allocation FORCE ROW LEVEL SECURITY;
CREATE POLICY journal_match_allocation_isolation
    ON accounting_core.journal_match_allocation
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

COMMIT;
