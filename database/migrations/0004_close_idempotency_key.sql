BEGIN;

ALTER TABLE accounting_reporting.trial_balance_snapshot
    ADD COLUMN close_idempotency_key text NOT NULL DEFAULT '';

ALTER TABLE accounting_reporting.trial_balance_snapshot
    ALTER COLUMN close_idempotency_key DROP DEFAULT;

COMMIT;
