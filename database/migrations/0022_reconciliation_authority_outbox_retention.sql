BEGIN;

-- Migration 0021 proves that each reconciliation authority command commits with
-- exactly one matching accounting outbox event. Preserve that authority after
-- commit as well: publishing may update delivery metadata, but deleting or
-- re-keying the tenant/type/reference/hash identity must not detach a committed
-- reconciliation command from its durable evidence.
CREATE OR REPLACE FUNCTION accounting_core.enforce_reconciliation_authority_outbox_retention()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    linked_resolution record;
    linked_transition record;
    matching_outbox_event_count integer;
BEGIN
    FOR linked_resolution IN
        SELECT resolution.tenant_account_id,
               resolution.reconciliation_exception_id,
               resolution.reconciliation_exception_resolution_command_id,
               resolution.target_resolution_status_code,
               resolution.reconciliation_exception_resolution_command_hash
        FROM accounting_core.reconciliation_exception_resolution_command AS resolution
        WHERE resolution.tenant_account_id = OLD.tenant_account_id
          AND OLD.event_type_code = CASE resolution.target_resolution_status_code
              WHEN 'resolved' THEN 'reconciliation_exception_resolved'
              WHEN 'superseded' THEN 'reconciliation_exception_superseded'
              ELSE NULL
          END
          AND OLD.aggregate_reference =
              'urn:cwl:accounting:reconciliation_exception:'
              || resolution.reconciliation_exception_id::text
          AND OLD.payload_reference =
              'urn:cwl:accounting:reconciliation_exception_resolution:'
              || resolution.reconciliation_exception_resolution_command_id::text
          AND OLD.payload_hash = resolution.reconciliation_exception_resolution_command_hash
    LOOP
        SELECT count(*)
        INTO matching_outbox_event_count
        FROM accounting_integration.outbox_event AS event
        WHERE event.tenant_account_id = linked_resolution.tenant_account_id
          AND event.event_type_code = CASE linked_resolution.target_resolution_status_code
              WHEN 'resolved' THEN 'reconciliation_exception_resolved'
              WHEN 'superseded' THEN 'reconciliation_exception_superseded'
              ELSE NULL
          END
          AND event.aggregate_reference =
              'urn:cwl:accounting:reconciliation_exception:'
              || linked_resolution.reconciliation_exception_id::text
          AND event.payload_reference =
              'urn:cwl:accounting:reconciliation_exception_resolution:'
              || linked_resolution.reconciliation_exception_resolution_command_id::text
          AND event.payload_hash =
              linked_resolution.reconciliation_exception_resolution_command_hash;

        IF matching_outbox_event_count <> 1 THEN
            RAISE EXCEPTION
                'committed reconciliation exception authority must retain exactly one matching outbox event (reconciliation_authority_outbox_retention)'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;

    FOR linked_transition IN
        SELECT transition.tenant_account_id,
               transition.reconciliation_run_id,
               transition.reconciliation_run_transition_command_id,
               transition.reconciliation_transition_command_hash
        FROM accounting_core.reconciliation_run_transition_command AS transition
        WHERE transition.tenant_account_id = OLD.tenant_account_id
          AND OLD.event_type_code = 'reconciliation_run_reconciled'
          AND OLD.aggregate_reference =
              'urn:cwl:accounting:reconciliation_run:'
              || transition.reconciliation_run_id::text
          AND OLD.payload_reference =
              'urn:cwl:accounting:reconciliation_run_transition:'
              || transition.reconciliation_run_transition_command_id::text
          AND OLD.payload_hash = transition.reconciliation_transition_command_hash
    LOOP
        SELECT count(*)
        INTO matching_outbox_event_count
        FROM accounting_integration.outbox_event AS event
        WHERE event.tenant_account_id = linked_transition.tenant_account_id
          AND event.event_type_code = 'reconciliation_run_reconciled'
          AND event.aggregate_reference =
              'urn:cwl:accounting:reconciliation_run:'
              || linked_transition.reconciliation_run_id::text
          AND event.payload_reference =
              'urn:cwl:accounting:reconciliation_run_transition:'
              || linked_transition.reconciliation_run_transition_command_id::text
          AND event.payload_hash = linked_transition.reconciliation_transition_command_hash;

        IF matching_outbox_event_count <> 1 THEN
            RAISE EXCEPTION
                'committed reconciliation lifecycle authority must retain exactly one matching outbox event (reconciliation_authority_outbox_retention)'
                USING ERRCODE = '23514';
        END IF;
    END LOOP;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER reconciliation_authority_outbox_retention_delete_guard
    AFTER DELETE ON accounting_integration.outbox_event
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.enforce_reconciliation_authority_outbox_retention();

CREATE CONSTRAINT TRIGGER reconciliation_authority_outbox_retention_update_guard
    AFTER UPDATE OF tenant_account_id,
                    event_type_code,
                    aggregate_reference,
                    payload_reference,
                    payload_hash
    ON accounting_integration.outbox_event
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.enforce_reconciliation_authority_outbox_retention();

COMMIT;
