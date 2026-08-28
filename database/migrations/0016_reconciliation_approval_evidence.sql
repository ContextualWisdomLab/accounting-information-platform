BEGIN;

-- Durable human reconciliation approval evidence.
--
-- Approval is an immutable control fact. Its source_payload_hash identifies the
-- command evidence supplied by the caller, while reconciliation_snapshot_hash
-- is always computed by PostgreSQL from the candidate and allocation rows that
-- the decision reviews. Neither field grants journal, close, or policy authority.

CREATE TABLE accounting_core.reconciliation_approval (
    reconciliation_approval_id uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_account_id uuid NOT NULL,
    reconciliation_run_id uuid NOT NULL,
    reconciliation_match_id uuid NOT NULL,
    approval_command_key text NOT NULL
        CHECK (btrim(approval_command_key) <> ''),
    source_payload_hash text NOT NULL
        CHECK (source_payload_hash ~ '^sha256:[0-9a-f]{64}$'),
    reconciliation_snapshot_version integer NOT NULL DEFAULT 1
        CHECK (reconciliation_snapshot_version = 1),
    reconciliation_snapshot_hash text NOT NULL
        CHECK (reconciliation_snapshot_hash ~ '^sha256:[0-9a-f]{64}$'),
    approver_reference text NOT NULL
        CHECK (btrim(approver_reference) <> ''),
    approval_purpose_code text NOT NULL
        CHECK (btrim(approval_purpose_code) <> ''),
    approval_decision_code text NOT NULL
        CHECK (approval_decision_code IN ('approved', 'rejected')),
    effective_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    FOREIGN KEY (
        tenant_account_id,
        reconciliation_run_id,
        reconciliation_match_id
    )
        REFERENCES accounting_core.reconciliation_match (
            tenant_account_id,
            reconciliation_run_id,
            reconciliation_match_id
        ),
    UNIQUE (tenant_account_id, approval_command_key),
    UNIQUE (
        tenant_account_id,
        reconciliation_run_id,
        reconciliation_match_id
    )
);

CREATE INDEX reconciliation_approval_run_index
    ON accounting_core.reconciliation_approval (
        tenant_account_id,
        reconciliation_run_id,
        approval_decision_code,
        recorded_at,
        reconciliation_approval_id
    );

ALTER TABLE accounting_core.reconciliation_approval ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting_core.reconciliation_approval FORCE ROW LEVEL SECURITY;

CREATE POLICY reconciliation_approval_isolation
    ON accounting_core.reconciliation_approval
    USING (tenant_account_id = accounting_core.current_tenant_account_id())
    WITH CHECK (tenant_account_id = accounting_core.current_tenant_account_id());

REVOKE ALL ON accounting_core.reconciliation_approval FROM PUBLIC;

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_snapshot_value(
    snapshot_value text
)
RETURNS text
LANGUAGE sql
IMMUTABLE
STRICT
AS $$
    SELECT octet_length(snapshot_value)::text || ':' || snapshot_value;
$$;

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_match_snapshot_hash(
    snapshot_tenant_account_id uuid,
    snapshot_reconciliation_run_id uuid,
    snapshot_reconciliation_match_id uuid
)
RETURNS text
LANGUAGE sql
STABLE
AS $$
WITH candidate_row AS (
    SELECT
        candidate.reconciliation_candidate_id,
        candidate.statement_entry_reference,
        candidate.journal_reference,
        candidate.statement_amount,
        candidate.journal_amount,
        candidate.rule_code
    FROM accounting_core.reconciliation_match AS match
    JOIN accounting_core.reconciliation_candidate AS candidate
      ON candidate.tenant_account_id = match.tenant_account_id
     AND candidate.reconciliation_run_id = match.reconciliation_run_id
     AND candidate.reconciliation_candidate_id = match.reconciliation_candidate_id
    WHERE match.tenant_account_id = snapshot_tenant_account_id
      AND match.reconciliation_run_id = snapshot_reconciliation_run_id
      AND match.reconciliation_match_id = snapshot_reconciliation_match_id
), statement_rows AS (
    SELECT COALESCE(
        string_agg(
            concat_ws(
                '|',
                'statement',
                accounting_core.reconciliation_snapshot_value(
                    allocation.reconciliation_allocation_id::text
                ),
                accounting_core.reconciliation_snapshot_value(
                    allocation.statement_entry_reference
                ),
                accounting_core.reconciliation_snapshot_value(
                    allocation.allocated_amount::text
                )
            ),
            E'\n' ORDER BY allocation.reconciliation_allocation_id
        ),
        ''
    ) AS snapshot_value
    FROM accounting_core.statement_match_allocation AS allocation
    WHERE allocation.tenant_account_id = snapshot_tenant_account_id
      AND allocation.reconciliation_run_id = snapshot_reconciliation_run_id
      AND allocation.reconciliation_match_id = snapshot_reconciliation_match_id
), journal_rows AS (
    SELECT COALESCE(
        string_agg(
            concat_ws(
                '|',
                'journal',
                accounting_core.reconciliation_snapshot_value(
                    allocation.reconciliation_allocation_id::text
                ),
                accounting_core.reconciliation_snapshot_value(
                    allocation.journal_reference
                ),
                accounting_core.reconciliation_snapshot_value(
                    allocation.allocated_amount::text
                )
            ),
            E'\n' ORDER BY allocation.reconciliation_allocation_id
        ),
        ''
    ) AS snapshot_value
    FROM accounting_core.journal_match_allocation AS allocation
    WHERE allocation.tenant_account_id = snapshot_tenant_account_id
      AND allocation.reconciliation_run_id = snapshot_reconciliation_run_id
      AND allocation.reconciliation_match_id = snapshot_reconciliation_match_id
)
SELECT CASE
    WHEN candidate_row.reconciliation_candidate_id IS NULL THEN NULL
    ELSE 'sha256:' || encode(
        sha256(
            convert_to(
                concat_ws(
                    E'\n',
                    'reconciliation_snapshot_version=1',
                    'tenant=' || accounting_core.reconciliation_snapshot_value(
                        snapshot_tenant_account_id::text
                    ),
                    'run=' || accounting_core.reconciliation_snapshot_value(
                        snapshot_reconciliation_run_id::text
                    ),
                    'match=' || accounting_core.reconciliation_snapshot_value(
                        snapshot_reconciliation_match_id::text
                    ),
                    'candidate=' || accounting_core.reconciliation_snapshot_value(
                        candidate_row.reconciliation_candidate_id::text
                    ),
                    'statement_reference=' || accounting_core.reconciliation_snapshot_value(
                        candidate_row.statement_entry_reference
                    ),
                    'journal_reference=' || accounting_core.reconciliation_snapshot_value(
                        candidate_row.journal_reference
                    ),
                    'statement_amount=' || accounting_core.reconciliation_snapshot_value(
                        candidate_row.statement_amount::text
                    ),
                    'journal_amount=' || accounting_core.reconciliation_snapshot_value(
                        candidate_row.journal_amount::text
                    ),
                    'rule=' || accounting_core.reconciliation_snapshot_value(
                        candidate_row.rule_code
                    ),
                    'statement_allocations=' || statement_rows.snapshot_value,
                    'journal_allocations=' || journal_rows.snapshot_value
                ),
                'UTF8'
            )
        ),
        'hex'
    )
END
FROM candidate_row
CROSS JOIN statement_rows
CROSS JOIN journal_rows;
$$;

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_match_snapshot_lock(
    snapshot_tenant_account_id uuid,
    snapshot_reconciliation_run_id uuid,
    snapshot_reconciliation_match_id uuid
)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            concat_ws(
                ':',
                'reconciliation-match-snapshot',
                snapshot_tenant_account_id::text,
                snapshot_reconciliation_run_id::text,
                snapshot_reconciliation_match_id::text
            ),
            0
        )
    );
    RETURN;
END;
$$;

CREATE OR REPLACE FUNCTION accounting_core.reject_reconciliation_approval_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION
        'recorded reconciliation approval evidence is immutable; create a new reviewed match instead (reconciliation_approval_immutable)'
        USING ERRCODE = '23514';
END;
$$;

CREATE TRIGGER reconciliation_approval_immutability_guard
BEFORE UPDATE OR DELETE
ON accounting_core.reconciliation_approval
FOR EACH ROW EXECUTE FUNCTION accounting_core.reject_reconciliation_approval_mutation();

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_approval_insert_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    current_status text;
BEGIN
    PERFORM accounting_core.reconciliation_match_snapshot_lock(
        NEW.tenant_account_id,
        NEW.reconciliation_run_id,
        NEW.reconciliation_match_id
    );

    SELECT match_status_code
    INTO current_status
    FROM accounting_core.reconciliation_match
    WHERE tenant_account_id = NEW.tenant_account_id
      AND reconciliation_run_id = NEW.reconciliation_run_id
      AND reconciliation_match_id = NEW.reconciliation_match_id;

    IF NOT FOUND OR current_status <> 'proposed' THEN
        RAISE EXCEPTION
            'reconciliation approval evidence requires a proposed match in the same tenant/run scope (reconciliation_approval_scope_mismatch)'
            USING ERRCODE = '23514';
    END IF;

    NEW.reconciliation_snapshot_version := 1;
    NEW.reconciliation_snapshot_hash :=
        accounting_core.reconciliation_match_snapshot_hash(
            NEW.tenant_account_id,
            NEW.reconciliation_run_id,
            NEW.reconciliation_match_id
        );
    IF NEW.reconciliation_snapshot_hash IS NULL THEN
        RAISE EXCEPTION
            'reconciliation approval evidence has no reviewable candidate snapshot (reconciliation_snapshot_missing)'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER reconciliation_approval_insert_guard
BEFORE INSERT
ON accounting_core.reconciliation_approval
FOR EACH ROW EXECUTE FUNCTION accounting_core.reconciliation_approval_insert_guard();

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_approval_allocation_lock()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM accounting_core.reconciliation_match_snapshot_lock(
        NEW.tenant_account_id,
        NEW.reconciliation_run_id,
        NEW.reconciliation_match_id
    );
    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_approval AS approval
        WHERE approval.tenant_account_id = NEW.tenant_account_id
          AND approval.reconciliation_run_id = NEW.reconciliation_run_id
          AND approval.reconciliation_match_id = NEW.reconciliation_match_id
    ) THEN
        RAISE EXCEPTION
            'reconciliation allocations are frozen after approval evidence; create a new proposed match instead (reconciliation_snapshot_frozen)'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER reconciliation_statement_allocation_snapshot_lock
BEFORE INSERT
ON accounting_core.statement_match_allocation
FOR EACH ROW EXECUTE FUNCTION accounting_core.reconciliation_approval_allocation_lock();

CREATE TRIGGER reconciliation_journal_allocation_snapshot_lock
BEFORE INSERT
ON accounting_core.journal_match_allocation
FOR EACH ROW EXECUTE FUNCTION accounting_core.reconciliation_approval_allocation_lock();

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_match_requires_approval()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    required_decision text;
BEGIN
    PERFORM accounting_core.reconciliation_match_snapshot_lock(
        NEW.tenant_account_id,
        NEW.reconciliation_run_id,
        NEW.reconciliation_match_id
    );

    IF TG_OP = 'UPDATE'
       AND OLD.match_status_code IN ('approved', 'rejected') THEN
        IF NEW.tenant_account_id IS DISTINCT FROM OLD.tenant_account_id
           OR NEW.reconciliation_run_id IS DISTINCT FROM OLD.reconciliation_run_id
           OR NEW.reconciliation_match_id IS DISTINCT FROM OLD.reconciliation_match_id
           OR NEW.reconciliation_candidate_id IS DISTINCT FROM OLD.reconciliation_candidate_id THEN
            RAISE EXCEPTION
                'reviewed reconciliation match identity is immutable; supersede the match instead (reconciliation_match_identity_immutable)'
                USING ERRCODE = '23514';
        END IF;

        IF NEW.match_status_code = OLD.match_status_code THEN
            IF NEW.approved_at IS DISTINCT FROM OLD.approved_at THEN
                RAISE EXCEPTION
                    'reviewed reconciliation match evidence is immutable; supersede the match instead (reconciliation_review_terminal)'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;

        IF NEW.match_status_code = 'superseded' THEN
            RETURN NEW;
        END IF;

        RAISE EXCEPTION
            'reviewed reconciliation match cannot reopen or change decision; supersede it instead (reconciliation_review_terminal)'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.match_status_code NOT IN ('approved', 'rejected') THEN
        RETURN NEW;
    END IF;

    required_decision := NEW.match_status_code;

    IF required_decision = 'approved' AND NEW.approved_at IS NULL THEN
        RAISE EXCEPTION
            'approved reconciliation match requires approved_at and durable approved evidence (reconciliation_approval_required)'
            USING ERRCODE = '23514';
    END IF;

    IF required_decision = 'rejected' AND NEW.approved_at IS NOT NULL THEN
        RAISE EXCEPTION
            'rejected reconciliation match cannot carry approved_at (reconciliation_approval_required)'
            USING ERRCODE = '23514';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_approval AS approval
        WHERE approval.tenant_account_id = NEW.tenant_account_id
          AND approval.reconciliation_run_id = NEW.reconciliation_run_id
          AND approval.reconciliation_match_id = NEW.reconciliation_match_id
          AND approval.approval_decision_code = required_decision
          AND approval.reconciliation_snapshot_version = 1
          AND approval.reconciliation_snapshot_hash = accounting_core.reconciliation_match_snapshot_hash(
              NEW.tenant_account_id,
              NEW.reconciliation_run_id,
              NEW.reconciliation_match_id
          )
    ) THEN
        RAISE EXCEPTION
            'reviewed reconciliation match requires durable decision-consistent approval evidence bound to the current snapshot (reconciliation_approval_required)'
            USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER reconciliation_match_requires_approval_guard
BEFORE INSERT OR UPDATE
ON accounting_core.reconciliation_match
FOR EACH ROW EXECUTE FUNCTION accounting_core.reconciliation_match_requires_approval();

COMMIT;
