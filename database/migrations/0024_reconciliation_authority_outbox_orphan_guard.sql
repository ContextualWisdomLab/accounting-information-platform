BEGIN;

-- Reconciliation authority event types are reserved accounting-control evidence.
-- Migration 0022 protects the event retained by an existing immutable command,
-- but an authority-shaped outbox row must also be unable to exist on its own.
-- Otherwise a privileged writer could publish a fabricated resolved/reconciled
-- event that has no durable command behind it and mislead downstream consumers.
--
-- Forced RLS applies to all three tables inspected by the upgrade preflight.
-- Give only the current migration user transaction-scoped all-tenant SELECT
-- visibility, then remove those temporary policies before installing the durable
-- runtime guard. No financial amount or journal authority is introduced here.
CREATE POLICY reconciliation_authority_orphan_upgrade_resolution_visibility
    ON accounting_core.reconciliation_exception_resolution_command
    FOR SELECT
    TO current_user
    USING (true);

CREATE POLICY reconciliation_authority_orphan_upgrade_transition_visibility
    ON accounting_core.reconciliation_run_transition_command
    FOR SELECT
    TO current_user
    USING (true);

CREATE POLICY reconciliation_authority_orphan_upgrade_outbox_visibility
    ON accounting_integration.outbox_event
    FOR SELECT
    TO current_user
    USING (true);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM accounting_integration.outbox_event AS event
        WHERE event.event_type_code IN (
                  'reconciliation_exception_resolved',
                  'reconciliation_exception_superseded',
                  'reconciliation_run_reconciled'
              )
          AND NOT EXISTS (
              SELECT 1
              FROM accounting_core.reconciliation_exception_resolution_command AS resolution
              WHERE resolution.tenant_account_id = event.tenant_account_id
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
          )
          AND NOT EXISTS (
              SELECT 1
              FROM accounting_core.reconciliation_run_transition_command AS transition
              WHERE transition.tenant_account_id = event.tenant_account_id
                AND event.event_type_code = 'reconciliation_run_reconciled'
                AND event.aggregate_reference =
                    'urn:cwl:accounting:reconciliation_run:'
                    || transition.reconciliation_run_id::text
                AND event.payload_reference =
                    'urn:cwl:accounting:reconciliation_run_transition:'
                    || transition.reconciliation_run_transition_command_id::text
                AND event.payload_hash = transition.reconciliation_transition_command_hash
          )
    ) THEN
        RAISE EXCEPTION
            'existing reconciliation authority-shaped outbox event has no matching immutable command; quarantine the event and reconstruct verified provenance before migration 0023 (reconciliation_authority_outbox_orphan_preflight)'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

DROP POLICY reconciliation_authority_orphan_upgrade_outbox_visibility
    ON accounting_integration.outbox_event;
DROP POLICY reconciliation_authority_orphan_upgrade_transition_visibility
    ON accounting_core.reconciliation_run_transition_command;
DROP POLICY reconciliation_authority_orphan_upgrade_resolution_visibility
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
    linked_authority_count integer := 0;
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
        linked_authority_count := linked_authority_count + 1;

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
        linked_authority_count := linked_authority_count + 1;

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

    IF checked_event_type_code IN (
           'reconciliation_exception_resolved',
           'reconciliation_exception_superseded',
           'reconciliation_run_reconciled'
       )
       AND linked_authority_count = 0 THEN
        RAISE EXCEPTION
            'reconciliation authority-shaped outbox event requires one matching immutable command (reconciliation_authority_outbox_orphan)'
            USING ERRCODE = '23514';
    END IF;
END;
$$;

COMMIT;
