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

-- PR #43 owns the complete database-derived reconciliation bridge and both
-- population identities through migration 0019's authority overlay. The child
-- maker-checker slice must not replace that stronger parent boundary with a
-- second partial bridge implementation. Instead, derive the parent authority
-- again and compose the immutable exception-resolution command population into
-- the final lifecycle snapshot before the existing transition command hash is
-- assigned. This keeps the stacked boundary monotonic: parent accounting facts
-- remain authoritative and child maker-checker evidence becomes inseparable
-- from the persisted lifecycle digest.
CREATE OR REPLACE FUNCTION accounting_core.assign_reconciliation_run_resolution_snapshot()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    database_snapshot_hash text;
    database_statement_reference text;
    database_book_reference text;
    resolution_command_population jsonb;
    resolution_snapshot_payload jsonb;
BEGIN
    SELECT authority.database_snapshot_hash,
           authority.database_statement_reference,
           authority.database_book_reference
    INTO database_snapshot_hash,
         database_statement_reference,
         database_book_reference
    FROM accounting_core.reconciliation_run_database_snapshot_authority(
        NEW.tenant_account_id,
        NEW.reconciliation_run_id
    ) AS authority;

    IF database_snapshot_hash IS NULL
       OR database_statement_reference IS NULL
       OR database_book_reference IS NULL THEN
        RAISE EXCEPTION
            'reconciliation resolution snapshot requires database-owned parent authority (reconciliation_resolution_snapshot_parent_authority)'
            USING ERRCODE = '23514';
    END IF;

    SELECT COALESCE(
               jsonb_agg(
                   jsonb_build_object(
                       'reconciliation_exception_resolution_command_id',
                           resolution.reconciliation_exception_resolution_command_id::text,
                       'reconciliation_exception_id',
                           resolution.reconciliation_exception_id::text,
                       'reconciliation_resolution_idempotency_key',
                           resolution.reconciliation_resolution_idempotency_key,
                       'target_resolution_status_code',
                           resolution.target_resolution_status_code,
                       'reconciliation_evidence_id',
                           resolution.reconciliation_evidence_id::text,
                       'resolution_evidence_reference',
                           resolution.resolution_evidence_reference,
                       'resolution_evidence_hash',
                           resolution.resolution_evidence_hash,
                       'source_payload_hash',
                           resolution.source_payload_hash,
                       'reconciliation_exception_resolution_command_hash',
                           resolution.reconciliation_exception_resolution_command_hash,
                       'actor_reference',
                           resolution.actor_reference,
                       'purpose_code',
                           resolution.purpose_code,
                       'effective_at',
                           resolution.effective_at,
                       'recorded_at',
                           resolution.recorded_at
                   )
                   ORDER BY resolution.reconciliation_exception_id,
                            resolution.reconciliation_exception_resolution_command_id
               ),
               '[]'::jsonb
           )
    INTO resolution_command_population
    FROM accounting_core.reconciliation_exception_resolution_command AS resolution
    WHERE resolution.tenant_account_id = NEW.tenant_account_id
      AND resolution.reconciliation_run_id = NEW.reconciliation_run_id;

    resolution_snapshot_payload := jsonb_build_object(
        'database_snapshot_hash', database_snapshot_hash,
        'statement_population_reference', database_statement_reference,
        'book_population_reference', database_book_reference,
        'resolution_commands', resolution_command_population
    );

    NEW.statement_population_reference := database_statement_reference;
    NEW.book_population_reference := database_book_reference;
    NEW.reconciliation_snapshot_hash := 'sha256:' || encode(
        sha256(
            convert_to(
                'reconciliation_run_resolution_snapshot:v1|'
                || resolution_snapshot_payload::text,
                'UTF8'
            )
        ),
        'hex'
    );
    RETURN NEW;
END;
$$;

-- PostgreSQL executes same-kind triggers in lexical name order. The parent
-- `...database_authority_guard` runs first; this child `...evidence_snapshot_guard`
-- then composes immutable resolution commands; the existing `...hash_guard` runs
-- last and binds the final database-owned snapshot plus both population identities.
CREATE TRIGGER accounting_reconciliation_transition_evidence_snapshot_guard
    BEFORE INSERT ON accounting_core.reconciliation_run_transition_command
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.assign_reconciliation_run_resolution_snapshot();

COMMIT;
