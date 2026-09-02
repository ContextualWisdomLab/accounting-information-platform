BEGIN;

-- Exception-resolution authority is only complete when the immutable command,
-- the terminal exception status, and the matching accounting outbox event are
-- committed together. Migration 0020 already defers command/status validation;
-- this forward migration adds the missing third leg without weakening the
-- existing maker-checker or reconciliation lifecycle invariants.
CREATE OR REPLACE FUNCTION accounting_core.enforce_reconciliation_exception_resolution_outbox_pair()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    expected_event_type_code text;
    matching_outbox_event_count integer;
BEGIN
    expected_event_type_code := CASE NEW.target_resolution_status_code
        WHEN 'resolved' THEN 'reconciliation_exception_resolved'
        WHEN 'superseded' THEN 'reconciliation_exception_superseded'
        ELSE NULL
    END;

    IF expected_event_type_code IS NULL THEN
        RAISE EXCEPTION
            'reconciliation exception resolution target status is not supported (reconciliation_exception_resolution_atomic_outbox)'
            USING ERRCODE = '23514';
    END IF;

    SELECT count(*)
    INTO matching_outbox_event_count
    FROM accounting_integration.outbox_event AS event
    WHERE event.tenant_account_id = NEW.tenant_account_id
      AND event.event_type_code = expected_event_type_code
      AND event.aggregate_reference =
          'urn:cwl:accounting:reconciliation_exception:'
          || NEW.reconciliation_exception_id::text
      AND event.payload_reference =
          'urn:cwl:accounting:reconciliation_exception_resolution:'
          || NEW.reconciliation_exception_resolution_command_id::text
      AND event.payload_hash = NEW.reconciliation_exception_resolution_command_hash;

    IF matching_outbox_event_count <> 1 THEN
        RAISE EXCEPTION
            'reconciliation exception resolution command, terminal status, and matching outbox event must commit atomically (reconciliation_exception_resolution_atomic_outbox)'
            USING ERRCODE = '23514';
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER reconciliation_exception_resolution_outbox_pair_guard
    AFTER INSERT ON accounting_core.reconciliation_exception_resolution_command
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.enforce_reconciliation_exception_resolution_outbox_pair();

COMMIT;
