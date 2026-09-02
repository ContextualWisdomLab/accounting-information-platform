BEGIN;

-- Migration 0021 proves that each reconciliation authority command commits with
-- exactly one matching accounting outbox event. Preserve that authority after
-- commit as well: publishing may update delivery metadata, but deleting,
-- duplicating, re-keying the tenant/type/reference/hash identity, or rewriting
-- the retained event id/creation time must not detach, ambiguate, or falsify a
-- committed reconciliation command's durable audit evidence.
--
-- Fail the forward migration if a database was damaged after 0021 but before
-- this retention guard was installed. A new trigger cannot repair an already
-- missing/re-keyed/duplicated event, so accepting that state would silently
-- bless broken authority provenance.
--
-- The command and outbox tables use FORCE RLS. The damage preflight is an
-- all-tenant migration-owner check, so give only current_user temporary SELECT
-- visibility for the three exact tables it reads. These policies live inside
-- this migration transaction and are removed before durable runtime guards are
-- installed; they do not widen application authority.
CREATE POLICY reconciliation_authority_retention_upgrade_resolution_visibility
    ON accounting_core.reconciliation_exception_resolution_command
    FOR SELECT
    TO current_user
    USING (true);

CREATE POLICY reconciliation_authority_retention_upgrade_transition_visibility
    ON accounting_core.reconciliation_run_transition_command
    FOR SELECT
    TO current_user
    USING (true);

CREATE POLICY reconciliation_authority_retention_upgrade_outbox_visibility
    ON accounting_integration.outbox_event
    FOR SELECT
    TO current_user
    USING (true);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_exception_resolution_command AS resolution
        WHERE (
            SELECT count(*)
            FROM accounting_integration.outbox_event AS event
            WHERE event.tenant_account_id = resolution.tenant_account_id
              AND event.event_type_code = CASE resolution.target_resolution_status_code
                  WHEN 'resolved' THEN 'reconciliation_exception_resolved'
                  WHEN 'superseded' THEN 'reconciliation_exception_superseded'
                  ELSE NULL
              END
              AND event.aggregate_reference =
                  'urn:cwl:accounting:reconciliation_exception:'
                  || resolution.reconciliation_exception_id::text
              AND event.payload_reference =
                  'urn:cwl:accounting:reconciliation_exception_resolution:'
                  || resolution.reconciliation_exception_resolution_command_id::text
              AND event.payload_hash =
                  resolution.reconciliation_exception_resolution_command_hash
        ) <> 1
    ) THEN
        RAISE EXCEPTION
            'existing reconciliation exception authority does not have exactly one matching outbox event; restore or reconstruct verified provenance before migration 0022 (reconciliation_authority_outbox_retention_preflight)'
            USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM accounting_core.reconciliation_run_transition_command AS transition
        WHERE (
            SELECT count(*)
            FROM accounting_integration.outbox_event AS event
            WHERE event.tenant_account_id = transition.tenant_account_id
              AND event.event_type_code = 'reconciliation_run_reconciled'
              AND event.aggregate_reference =
                  'urn:cwl:accounting:reconciliation_run:'
                  || transition.reconciliation_run_id::text
              AND event.payload_reference =
                  'urn:cwl:accounting:reconciliation_run_transition:'
                  || transition.reconciliation_run_transition_command_id::text
              AND event.payload_hash = transition.reconciliation_transition_command_hash
        ) <> 1
    ) THEN
        RAISE EXCEPTION
            'existing reconciliation lifecycle authority does not have exactly one matching outbox event; restore or reconstruct verified provenance before migration 0022 (reconciliation_authority_outbox_retention_preflight)'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

DROP POLICY reconciliation_authority_retention_upgrade_outbox_visibility
    ON accounting_integration.outbox_event;
DROP POLICY reconciliation_authority_retention_upgrade_transition_visibility
    ON accounting_core.reconciliation_run_transition_command;
DROP POLICY reconciliation_authority_retention_upgrade_resolution_visibility
    ON accounting_core.reconciliation_exception_resolution_command;

CREATE OR REPLACE FUNCTION accounting_core.assert_reconciliation_authority_outbox_identity(
    checked_tenant_account_id uuid,
    checked_event_type_code text,
    checked_aggregate_reference text,
    checked_payload_reference text,
    checked_payload_hash text
)
RETURNS void
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
        WHERE resolution.tenant_account_id = checked_tenant_account_id
          AND checked_event_type_code = CASE resolution.target_resolution_status_code
              WHEN 'resolved' THEN 'reconciliation_exception_resolved'
              WHEN 'superseded' THEN 'reconciliation_exception_superseded'
              ELSE NULL
          END
          AND checked_aggregate_reference =
              'urn:cwl:accounting:reconciliation_exception:'
              || resolution.reconciliation_exception_id::text
          AND checked_payload_reference =
              'urn:cwl:accounting:reconciliation_exception_resolution:'
              || resolution.reconciliation_exception_resolution_command_id::text
          AND checked_payload_hash =
              resolution.reconciliation_exception_resolution_command_hash
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
        WHERE transition.tenant_account_id = checked_tenant_account_id
          AND checked_event_type_code = 'reconciliation_run_reconciled'
          AND checked_aggregate_reference =
              'urn:cwl:accounting:reconciliation_run:'
              || transition.reconciliation_run_id::text
          AND checked_payload_reference =
              'urn:cwl:accounting:reconciliation_run_transition:'
              || transition.reconciliation_run_transition_command_id::text
          AND checked_payload_hash = transition.reconciliation_transition_command_hash
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
END;
$$;

CREATE OR REPLACE FUNCTION accounting_core.enforce_reconciliation_authority_outbox_retention()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND (
           OLD.outbox_event_id IS DISTINCT FROM NEW.outbox_event_id
           OR OLD.created_at IS DISTINCT FROM NEW.created_at
       )
       AND (
           EXISTS (
               SELECT 1
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
                 AND OLD.payload_hash =
                     resolution.reconciliation_exception_resolution_command_hash
           )
           OR EXISTS (
               SELECT 1
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
           )
       ) THEN
        RAISE EXCEPTION
            'committed reconciliation authority outbox event id and created_at are immutable; only publication metadata may change (reconciliation_authority_outbox_audit_identity)'
            USING ERRCODE = '23514';
    END IF;

    IF TG_OP IN ('DELETE', 'UPDATE') THEN
        PERFORM accounting_core.assert_reconciliation_authority_outbox_identity(
            OLD.tenant_account_id,
            OLD.event_type_code,
            OLD.aggregate_reference,
            OLD.payload_reference,
            OLD.payload_hash
        );
    END IF;

    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM accounting_core.assert_reconciliation_authority_outbox_identity(
            NEW.tenant_account_id,
            NEW.event_type_code,
            NEW.aggregate_reference,
            NEW.payload_reference,
            NEW.payload_hash
        );
    END IF;

    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER reconciliation_authority_outbox_retention_insert_guard
    AFTER INSERT ON accounting_integration.outbox_event
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.enforce_reconciliation_authority_outbox_retention();

CREATE CONSTRAINT TRIGGER reconciliation_authority_outbox_retention_delete_guard
    AFTER DELETE ON accounting_integration.outbox_event
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.enforce_reconciliation_authority_outbox_retention();

CREATE CONSTRAINT TRIGGER reconciliation_authority_outbox_retention_update_guard
    AFTER UPDATE OF outbox_event_id,
                    tenant_account_id,
                    event_type_code,
                    aggregate_reference,
                    payload_reference,
                    payload_hash,
                    created_at
    ON accounting_integration.outbox_event
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.enforce_reconciliation_authority_outbox_retention();

COMMIT;
