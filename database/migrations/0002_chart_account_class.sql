BEGIN;

ALTER TABLE accounting_core.chart_account ADD COLUMN account_class_code text;

UPDATE accounting_core.chart_account
SET account_class_code = CASE chart_account_code
    WHEN '110100' THEN 'asset'
    WHEN '110200' THEN 'asset'
    WHEN '210100' THEN 'liability'
    WHEN '210200' THEN 'liability'
    WHEN '310100' THEN 'equity'
    WHEN '410100' THEN 'revenue'
    WHEN '510100' THEN 'expense'
    ELSE account_class_code
END
WHERE account_class_code IS NULL;

ALTER TABLE accounting_core.chart_account ALTER COLUMN account_class_code SET NOT NULL;

ALTER TABLE accounting_core.chart_account ADD CONSTRAINT account_class_check CHECK (
    account_class_code IN ('asset', 'liability', 'equity', 'revenue', 'expense')
);

COMMIT;
