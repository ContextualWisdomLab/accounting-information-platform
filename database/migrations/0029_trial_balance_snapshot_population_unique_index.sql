-- PostgreSQL requires CREATE INDEX CONCURRENTLY to run outside a transaction block.
-- Keep this migration to the single concurrent build so the canonical installer can
-- apply it as one autocommit statement without blocking ordinary table writes.
CREATE UNIQUE INDEX CONCURRENTLY trial_balance_snapshot_one_population_per_book_period
ON accounting_reporting.trial_balance_snapshot
    (tenant_account_id, accounting_book_id, fiscal_period_id);
