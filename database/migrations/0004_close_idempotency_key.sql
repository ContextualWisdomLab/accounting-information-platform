BEGIN;

ALTER TABLE accounting_reporting.trial_balance_snapshot
    ADD COLUMN close_idempotency_key text;

UPDATE accounting_reporting.trial_balance_snapshot AS snapshot_record
SET close_idempotency_key =
    tenant_record.tenant_account_code || ':period_close:' || period_record.period_code
FROM accounting_core.tenant_account AS tenant_record,
     accounting_core.fiscal_period AS period_record
WHERE tenant_record.tenant_account_id = snapshot_record.tenant_account_id
  AND period_record.tenant_account_id = snapshot_record.tenant_account_id
  AND period_record.fiscal_period_id = snapshot_record.fiscal_period_id
  AND snapshot_record.close_idempotency_key IS NULL;

ALTER TABLE accounting_reporting.trial_balance_snapshot
    ALTER COLUMN close_idempotency_key SET NOT NULL;

ALTER TABLE accounting_reporting.trial_balance_snapshot
    ADD CONSTRAINT close_idempotency_nonempty_check
    CHECK (btrim(close_idempotency_key) <> '');

ALTER TABLE accounting_reporting.trial_balance_snapshot
    ADD CONSTRAINT close_idempotency_tenant_key_unique
    UNIQUE (tenant_account_id, close_idempotency_key);

ALTER TABLE accounting_reporting.trial_balance_snapshot
    ADD CONSTRAINT close_snapshot_scope_unique
    UNIQUE (
        tenant_account_id,
        legal_entity_id,
        accounting_book_id,
        fiscal_period_id
    );

COMMIT;
