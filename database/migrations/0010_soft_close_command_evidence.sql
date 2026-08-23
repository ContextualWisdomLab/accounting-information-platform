BEGIN;

ALTER TABLE accounting_core.accounting_book_period_control
    ADD COLUMN soft_close_idempotency_key text,
    ADD COLUMN soft_close_source_payload_hash text,
    ADD COLUMN soft_close_source_journal_count integer,
    ADD CONSTRAINT soft_close_evidence_complete_check
    CHECK (
        (
            soft_close_idempotency_key IS NULL
            AND soft_close_source_payload_hash IS NULL
            AND soft_close_source_journal_count IS NULL
        )
        OR
        (
            soft_close_idempotency_key IS NOT NULL
            AND btrim(soft_close_idempotency_key) <> ''
            AND soft_close_source_payload_hash ~ '^sha256:[0-9a-f]{64}$'
            AND soft_close_source_journal_count IS NOT NULL
            AND soft_close_source_journal_count >= 0
        )
    );

CREATE UNIQUE INDEX accounting_book_period_soft_close_key_index
    ON accounting_core.accounting_book_period_control (
        tenant_account_id, soft_close_idempotency_key
    )
    WHERE soft_close_idempotency_key IS NOT NULL;

CREATE OR REPLACE FUNCTION accounting_core.guard_soft_close_evidence_update()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, accounting_core
AS $$
BEGIN
    IF OLD.soft_close_idempotency_key IS NOT NULL
       AND (
            NEW.soft_close_idempotency_key IS DISTINCT FROM OLD.soft_close_idempotency_key
            OR NEW.soft_close_source_payload_hash IS DISTINCT FROM OLD.soft_close_source_payload_hash
            OR NEW.soft_close_source_journal_count IS DISTINCT FROM OLD.soft_close_source_journal_count
       )
    THEN
        RAISE EXCEPTION
            'soft-close command evidence is immutable once recorded'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER soft_close_evidence_immutable_guard
    BEFORE UPDATE OF soft_close_idempotency_key,
                     soft_close_source_payload_hash,
                     soft_close_source_journal_count
    ON accounting_core.accounting_book_period_control
    FOR EACH ROW
    EXECUTE FUNCTION accounting_core.guard_soft_close_evidence_update();

COMMIT;
