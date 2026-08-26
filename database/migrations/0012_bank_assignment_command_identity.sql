-- Bank-account assignment command identity and active-binding scope.
--
-- Every state-changing accounting command carries tenant-scoped idempotency
-- identity plus immutable command evidence (repository invariant; AGENTS.md).
-- Migration 0011 introduced effective-dated bank-account assignments without
-- that identity, so a retried HTTP request inserted a second active binding
-- for the same bank account. This migration closes the gap without rewriting
-- history: it appends the command identity columns, pins their formats, and
-- constrains one active binding per tenant, bank account, and accounting book.
--
-- The table is empty in every environment while this branch is pending, so
-- ADD COLUMN ... NOT NULL is safe here; later migrations must follow the
-- repository's install/upgrade rehearsal contract instead.

ALTER TABLE accounting_core.bank_account_assignment
    ADD COLUMN assignment_idempotency_key text NOT NULL,
    ADD CONSTRAINT bank_account_assignment_command_key_present
        CHECK (btrim(assignment_idempotency_key) <> ''),
    ADD COLUMN assignment_command_hash text NOT NULL,
    ADD CONSTRAINT bank_account_assignment_command_hash_format
        CHECK (assignment_command_hash LIKE 'sha256:%');

COMMENT ON COLUMN accounting_core.bank_account_assignment.assignment_idempotency_key IS
    'Tenant-scoped replay identity supplied by the assignment caller.';
COMMENT ON COLUMN accounting_core.bank_account_assignment.assignment_command_hash IS
    'SHA-256 of the canonical assignment command evidence; reuse with different evidence fails closed.';

CREATE UNIQUE INDEX bank_account_assignment_command_key_scope
    ON accounting_core.bank_account_assignment (
        tenant_account_id, assignment_idempotency_key
    );

CREATE UNIQUE INDEX bank_account_assignment_active_book_scope
    ON accounting_core.bank_account_assignment (
        tenant_account_id, bank_account_record_id, accounting_book_id
    )
    WHERE valid_to IS NULL;
