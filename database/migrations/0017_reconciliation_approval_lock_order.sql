BEGIN;

-- Reconciliation approval INSERT must use the same parent-row -> snapshot-
-- advisory lock order as allocation INSERT and terminal match UPDATE. The
-- predecessor approval trigger took the advisory lock first and then acquired
-- the parent row implicitly through its foreign key, allowing a cycle with an
-- allocation transaction that already owned the parent row and was waiting for
-- the advisory lock.
--
-- A reviewed match must also represent one candidate-proposed connected
-- component. Per-node incidence is insufficient: candidate edges A-X and B-Y
-- could otherwise be bundled under a match anchored to A-X while preserving
-- equal statement/journal totals. The reusable validator below proves that the
-- complete allocated bipartite graph is reachable from the match's anchor
-- candidate. It is invoked before immutable approval evidence is inserted and
-- again at the terminal proposed -> approved transition.
--
-- Snapshot version 2 extends the database-owned digest for split/aggregate
-- reviewed matches with the authoritative source capacity of every allocated
-- statement and journal source. One-to-one evidence remains version 1 for
-- compatibility. Capacity is derived from immutable candidate facts in the
-- same tenant/run scope; callers cannot supply or override it.
ALTER TABLE accounting_core.reconciliation_approval
    DROP CONSTRAINT reconciliation_approval_reconciliation_snapshot_version_check,
    ADD CONSTRAINT reconciliation_approval_reconciliation_snapshot_version_check
        CHECK (reconciliation_snapshot_version IN (1, 2));

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_match_snapshot_version(
    snapshot_tenant_account_id uuid,
    snapshot_reconciliation_run_id uuid,
    snapshot_reconciliation_match_id uuid
)
RETURNS integer
LANGUAGE sql
STABLE
AS $$
WITH source_counts AS (
    SELECT
        (
            SELECT COUNT(DISTINCT allocation.statement_entry_reference)
            FROM accounting_core.statement_match_allocation AS allocation
            WHERE allocation.tenant_account_id = snapshot_tenant_account_id
              AND allocation.reconciliation_run_id = snapshot_reconciliation_run_id
              AND allocation.reconciliation_match_id = snapshot_reconciliation_match_id
        ) AS statement_source_count,
        (
            SELECT COUNT(DISTINCT allocation.journal_reference)
            FROM accounting_core.journal_match_allocation AS allocation
            WHERE allocation.tenant_account_id = snapshot_tenant_account_id
              AND allocation.reconciliation_run_id = snapshot_reconciliation_run_id
              AND allocation.reconciliation_match_id = snapshot_reconciliation_match_id
        ) AS journal_source_count
)
SELECT CASE
    WHEN statement_source_count > 1 OR journal_source_count > 1 THEN 2
    ELSE 1
END
FROM source_counts;
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
WITH snapshot_version AS (
    SELECT accounting_core.reconciliation_match_snapshot_version(
        snapshot_tenant_account_id,
        snapshot_reconciliation_run_id,
        snapshot_reconciliation_match_id
    ) AS version_number
), candidate_row AS (
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
), statement_capacity AS (
    SELECT
        allocation.statement_entry_reference AS source_reference,
        MIN(candidate.statement_amount) AS source_capacity,
        MAX(candidate.statement_amount) AS maximum_source_capacity
    FROM accounting_core.statement_match_allocation AS allocation
    LEFT JOIN accounting_core.reconciliation_candidate AS candidate
      ON candidate.tenant_account_id = allocation.tenant_account_id
     AND candidate.reconciliation_run_id = allocation.reconciliation_run_id
     AND candidate.statement_entry_reference = allocation.statement_entry_reference
    WHERE allocation.tenant_account_id = snapshot_tenant_account_id
      AND allocation.reconciliation_run_id = snapshot_reconciliation_run_id
      AND allocation.reconciliation_match_id = snapshot_reconciliation_match_id
    GROUP BY allocation.statement_entry_reference
), journal_capacity AS (
    SELECT
        allocation.journal_reference AS source_reference,
        MIN(candidate.journal_amount) AS source_capacity,
        MAX(candidate.journal_amount) AS maximum_source_capacity
    FROM accounting_core.journal_match_allocation AS allocation
    LEFT JOIN accounting_core.reconciliation_candidate AS candidate
      ON candidate.tenant_account_id = allocation.tenant_account_id
     AND candidate.reconciliation_run_id = allocation.reconciliation_run_id
     AND candidate.journal_reference = allocation.journal_reference
    WHERE allocation.tenant_account_id = snapshot_tenant_account_id
      AND allocation.reconciliation_run_id = snapshot_reconciliation_run_id
      AND allocation.reconciliation_match_id = snapshot_reconciliation_match_id
    GROUP BY allocation.journal_reference
), capacity_guard AS (
    SELECT
        NOT EXISTS (
            SELECT 1
            FROM statement_capacity
            WHERE source_capacity IS NULL
               OR source_capacity <> maximum_source_capacity
        )
        AND NOT EXISTS (
            SELECT 1
            FROM journal_capacity
            WHERE source_capacity IS NULL
               OR source_capacity <> maximum_source_capacity
        ) AS complete_and_consistent
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
                ),
                CASE
                    WHEN snapshot_version.version_number = 2 THEN
                        accounting_core.reconciliation_snapshot_value(
                            statement_capacity.source_capacity::text
                        )
                    ELSE NULL
                END
            ),
            E'\n' ORDER BY allocation.reconciliation_allocation_id
        ),
        ''
    ) AS snapshot_value
    FROM accounting_core.statement_match_allocation AS allocation
    JOIN statement_capacity
      ON statement_capacity.source_reference = allocation.statement_entry_reference
    CROSS JOIN snapshot_version
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
                ),
                CASE
                    WHEN snapshot_version.version_number = 2 THEN
                        accounting_core.reconciliation_snapshot_value(
                            journal_capacity.source_capacity::text
                        )
                    ELSE NULL
                END
            ),
            E'\n' ORDER BY allocation.reconciliation_allocation_id
        ),
        ''
    ) AS snapshot_value
    FROM accounting_core.journal_match_allocation AS allocation
    JOIN journal_capacity
      ON journal_capacity.source_reference = allocation.journal_reference
    CROSS JOIN snapshot_version
    WHERE allocation.tenant_account_id = snapshot_tenant_account_id
      AND allocation.reconciliation_run_id = snapshot_reconciliation_run_id
      AND allocation.reconciliation_match_id = snapshot_reconciliation_match_id
)
SELECT CASE
    WHEN candidate_row.reconciliation_candidate_id IS NULL THEN NULL
    WHEN snapshot_version.version_number = 2
         AND capacity_guard.complete_and_consistent IS DISTINCT FROM TRUE THEN NULL
    ELSE 'sha256:' || encode(
        sha256(
            convert_to(
                concat_ws(
                    E'\n',
                    'reconciliation_snapshot_version=' || snapshot_version.version_number::text,
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
CROSS JOIN snapshot_version
CROSS JOIN capacity_guard
CROSS JOIN statement_rows
CROSS JOIN journal_rows;
$$;

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_match_candidate_graph_guard(
    guard_tenant_account_id uuid,
    guard_reconciliation_run_id uuid,
    guard_reconciliation_match_id uuid
)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
    anchor_statement_reference text;
    anchor_journal_reference text;
    graph_is_connected boolean;
BEGIN
    SELECT candidate.statement_entry_reference,
           candidate.journal_reference
    INTO anchor_statement_reference,
         anchor_journal_reference
    FROM accounting_core.reconciliation_match AS match
    JOIN accounting_core.reconciliation_candidate AS candidate
      ON candidate.tenant_account_id = match.tenant_account_id
     AND candidate.reconciliation_run_id = match.reconciliation_run_id
     AND candidate.reconciliation_candidate_id = match.reconciliation_candidate_id
    WHERE match.tenant_account_id = guard_tenant_account_id
      AND match.reconciliation_run_id = guard_reconciliation_run_id
      AND match.reconciliation_match_id = guard_reconciliation_match_id;

    IF anchor_statement_reference IS NULL OR anchor_journal_reference IS NULL THEN
        RAISE EXCEPTION
            'approved reconciliation allocations require the reviewed anchor candidate (reconciliation_allocation_unproposed_pairing)'
            USING ERRCODE = '23514';
    END IF;

    WITH RECURSIVE
    statement_nodes AS (
        SELECT DISTINCT allocation.statement_entry_reference
        FROM accounting_core.statement_match_allocation AS allocation
        WHERE allocation.tenant_account_id = guard_tenant_account_id
          AND allocation.reconciliation_run_id = guard_reconciliation_run_id
          AND allocation.reconciliation_match_id = guard_reconciliation_match_id
    ),
    journal_nodes AS (
        SELECT DISTINCT allocation.journal_reference
        FROM accounting_core.journal_match_allocation AS allocation
        WHERE allocation.tenant_account_id = guard_tenant_account_id
          AND allocation.reconciliation_run_id = guard_reconciliation_run_id
          AND allocation.reconciliation_match_id = guard_reconciliation_match_id
    ),
    candidate_edges AS (
        SELECT DISTINCT candidate.statement_entry_reference,
                        candidate.journal_reference
        FROM accounting_core.reconciliation_candidate AS candidate
        JOIN statement_nodes AS statement_node
          ON statement_node.statement_entry_reference = candidate.statement_entry_reference
        JOIN journal_nodes AS journal_node
          ON journal_node.journal_reference = candidate.journal_reference
        WHERE candidate.tenant_account_id = guard_tenant_account_id
          AND candidate.reconciliation_run_id = guard_reconciliation_run_id
    ),
    graph_edges(from_kind, from_reference, to_kind, to_reference) AS (
        SELECT 'statement'::text,
               edge.statement_entry_reference,
               'journal'::text,
               edge.journal_reference
        FROM candidate_edges AS edge
        UNION ALL
        SELECT 'journal'::text,
               edge.journal_reference,
               'statement'::text,
               edge.statement_entry_reference
        FROM candidate_edges AS edge
    ),
    reachable(node_kind, node_reference) AS (
        SELECT 'statement'::text,
               anchor_statement_reference
        WHERE EXISTS (
                  SELECT 1
                  FROM statement_nodes AS statement_node
                  WHERE statement_node.statement_entry_reference = anchor_statement_reference
              )
          AND EXISTS (
                  SELECT 1
                  FROM journal_nodes AS journal_node
                  WHERE journal_node.journal_reference = anchor_journal_reference
              )
        UNION
        SELECT edge.to_kind,
               edge.to_reference
        FROM graph_edges AS edge
        JOIN reachable AS reached
          ON reached.node_kind = edge.from_kind
         AND reached.node_reference = edge.from_reference
    )
    SELECT EXISTS (SELECT 1 FROM statement_nodes)
       AND EXISTS (SELECT 1 FROM journal_nodes)
       AND EXISTS (
               SELECT 1
               FROM candidate_edges AS edge
               WHERE edge.statement_entry_reference = anchor_statement_reference
                 AND edge.journal_reference = anchor_journal_reference
           )
       AND NOT EXISTS (
               SELECT 1
               FROM statement_nodes AS statement_node
               WHERE NOT EXISTS (
                   SELECT 1
                   FROM reachable AS reached
                   WHERE reached.node_kind = 'statement'
                     AND reached.node_reference = statement_node.statement_entry_reference
               )
           )
       AND NOT EXISTS (
               SELECT 1
               FROM journal_nodes AS journal_node
               WHERE NOT EXISTS (
                   SELECT 1
                   FROM reachable AS reached
                   WHERE reached.node_kind = 'journal'
                     AND reached.node_reference = journal_node.journal_reference
               )
           )
    INTO graph_is_connected;

    IF graph_is_connected IS DISTINCT FROM TRUE THEN
        RAISE EXCEPTION
            'approved reconciliation allocations must form one candidate-proposed component rooted in the reviewed candidate (reconciliation_allocation_unproposed_pairing)'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_candidate_graph_transition_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.match_status_code = 'approved'
       AND OLD.match_status_code IS DISTINCT FROM 'approved' THEN
        PERFORM accounting_core.reconciliation_match_candidate_graph_guard(
            NEW.tenant_account_id,
            NEW.reconciliation_run_id,
            NEW.reconciliation_match_id
        );
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS reconciliation_candidate_graph_transition_guard
    ON accounting_core.reconciliation_match;
CREATE TRIGGER reconciliation_candidate_graph_transition_guard
BEFORE UPDATE OF match_status_code ON accounting_core.reconciliation_match
FOR EACH ROW
EXECUTE FUNCTION accounting_core.reconciliation_candidate_graph_transition_guard();

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_approval_insert_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    current_status text;
    statement_allocation_count bigint;
    journal_allocation_count bigint;
    statement_allocation_total numeric;
    journal_allocation_total numeric;
BEGIN
    -- Establish the canonical reconciliation lock order before taking the
    -- snapshot lock. The FK later requests a compatible lock from this same
    -- transaction, so it cannot invert the allocation path's row -> advisory
    -- ordering.
    SELECT match.match_status_code
    INTO current_status
    FROM accounting_core.reconciliation_match AS match
    WHERE match.tenant_account_id = NEW.tenant_account_id
      AND match.reconciliation_run_id = NEW.reconciliation_run_id
      AND match.reconciliation_match_id = NEW.reconciliation_match_id
    FOR UPDATE OF match;

    IF NOT FOUND OR current_status <> 'proposed' THEN
        RAISE EXCEPTION
            'reconciliation approval evidence requires a proposed match in the same tenant/run scope (reconciliation_approval_scope_mismatch)'
            USING ERRCODE = '23514';
    END IF;

    PERFORM accounting_core.reconciliation_match_snapshot_lock(
        NEW.tenant_account_id,
        NEW.reconciliation_run_id,
        NEW.reconciliation_match_id
    );

    IF NEW.approval_decision_code = 'approved' THEN
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

        IF statement_allocation_count = 0
           OR journal_allocation_count = 0
           OR statement_allocation_total <> journal_allocation_total THEN
            RAISE EXCEPTION
                'approved reconciliation evidence requires non-empty equal statement and journal allocation totals; add or correct allocations before recording approval evidence (reconciliation_match_unbalanced)'
                USING ERRCODE = '23514';
        END IF;

        PERFORM accounting_core.reconciliation_match_candidate_graph_guard(
            NEW.tenant_account_id,
            NEW.reconciliation_run_id,
            NEW.reconciliation_match_id
        );
    END IF;

    NEW.reconciliation_snapshot_version :=
        accounting_core.reconciliation_match_snapshot_version(
            NEW.tenant_account_id,
            NEW.reconciliation_run_id,
            NEW.reconciliation_match_id
        );
    NEW.reconciliation_snapshot_hash :=
        accounting_core.reconciliation_match_snapshot_hash(
            NEW.tenant_account_id,
            NEW.reconciliation_run_id,
            NEW.reconciliation_match_id
        );
    IF NEW.reconciliation_snapshot_hash IS NULL THEN
        RAISE EXCEPTION
            'reconciliation approval evidence has no complete reviewable candidate/source-capacity snapshot (reconciliation_snapshot_missing)'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION accounting_core.reconciliation_match_requires_approval()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    required_decision text;
    required_snapshot_version integer;
BEGIN
    PERFORM accounting_core.reconciliation_match_snapshot_lock(
        NEW.tenant_account_id,
        NEW.reconciliation_run_id,
        NEW.reconciliation_match_id
    );

    IF TG_OP = 'UPDATE'
       AND (
           NEW.tenant_account_id IS DISTINCT FROM OLD.tenant_account_id
           OR NEW.reconciliation_run_id IS DISTINCT FROM OLD.reconciliation_run_id
           OR NEW.reconciliation_match_id IS DISTINCT FROM OLD.reconciliation_match_id
           OR NEW.reconciliation_candidate_id IS DISTINCT FROM OLD.reconciliation_candidate_id
       ) THEN
        PERFORM accounting_core.reconciliation_match_snapshot_lock(
            OLD.tenant_account_id,
            OLD.reconciliation_run_id,
            OLD.reconciliation_match_id
        );
        IF EXISTS (
            SELECT 1
            FROM accounting_core.reconciliation_approval AS approval
            WHERE approval.tenant_account_id = OLD.tenant_account_id
              AND approval.reconciliation_run_id = OLD.reconciliation_run_id
              AND approval.reconciliation_match_id = OLD.reconciliation_match_id
        ) OR EXISTS (
            SELECT 1
            FROM accounting_core.statement_match_allocation AS allocation
            WHERE allocation.tenant_account_id = OLD.tenant_account_id
              AND allocation.reconciliation_run_id = OLD.reconciliation_run_id
              AND allocation.reconciliation_match_id = OLD.reconciliation_match_id
        ) OR EXISTS (
            SELECT 1
            FROM accounting_core.journal_match_allocation AS allocation
            WHERE allocation.tenant_account_id = OLD.tenant_account_id
              AND allocation.reconciliation_run_id = OLD.reconciliation_run_id
              AND allocation.reconciliation_match_id = OLD.reconciliation_match_id
        ) THEN
            RAISE EXCEPTION
                'reviewed reconciliation match identity is immutable; supersede the match instead (reconciliation_match_identity_immutable)'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    IF TG_OP = 'UPDATE'
       AND OLD.match_status_code IN ('approved', 'rejected', 'superseded') THEN
        IF NEW.approved_at IS DISTINCT FROM OLD.approved_at THEN
            RAISE EXCEPTION
                'reviewed reconciliation match evidence is immutable; supersede the match instead (reconciliation_review_terminal)'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.match_status_code = OLD.match_status_code THEN
            RETURN NEW;
        END IF;

        IF OLD.match_status_code IN ('approved', 'rejected')
           AND NEW.match_status_code = 'superseded' THEN
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
    required_snapshot_version := accounting_core.reconciliation_match_snapshot_version(
        NEW.tenant_account_id,
        NEW.reconciliation_run_id,
        NEW.reconciliation_match_id
    );

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
          AND approval.reconciliation_snapshot_version = required_snapshot_version
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

COMMIT;
