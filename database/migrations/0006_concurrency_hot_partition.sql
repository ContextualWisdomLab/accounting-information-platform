BEGIN;

ALTER TABLE accounting_integration.outbox_event
    ALTER COLUMN tenant_account_id SET NOT NULL;

-- tenant-leading indexes keep high-write scans bounded while the normalized
-- tables remain ready for a future hash-by-tenant and time partition layout.
CREATE INDEX journal_proposal_tenant_received_index
    ON accounting_integration.journal_proposal_record (
        tenant_account_id,
        received_at,
        proposal_record_id
    );

CREATE INDEX general_journal_tenant_period_date_index
    ON accounting_core.general_journal (
        tenant_account_id,
        fiscal_period_id,
        accounting_date,
        general_journal_id
    );

CREATE INDEX journal_entry_tenant_journal_index
    ON accounting_core.journal_entry_line (
        tenant_account_id,
        general_journal_id,
        line_number
    );

CREATE INDEX outbox_event_pending_created_index
    ON accounting_integration.outbox_event (
        tenant_account_id,
        created_at,
        outbox_event_id
    )
    WHERE published_at IS NULL;

CREATE INDEX reversal_event_tenant_reversed_index
    ON accounting_core.journal_reversal (
        tenant_account_id,
        reversed_at,
        journal_reversal_id
    );

CREATE INDEX posting_receipt_tenant_created_index
    ON accounting_integration.posting_receipt (
        tenant_account_id,
        created_at,
        posting_receipt_id
    );

CREATE INDEX home_tax_submission_tenant_created_index
    ON accounting_integration.home_tax_submission (
        tenant_account_id,
        created_at,
        home_tax_submission_id
    );

COMMIT;
