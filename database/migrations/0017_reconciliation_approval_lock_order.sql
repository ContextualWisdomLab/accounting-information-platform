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
    statement_allocation_total numeric(30, 6);
    journal_allocation_total numeric(30, 6);
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

COMMIT;