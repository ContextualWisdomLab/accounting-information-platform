# ADR 0006: Fiscal-period close is a snapshot-and-status transaction

**Status:** Accepted

## Decision

`PostgresPostingLedger.close_fiscal_period` is the first-class close command. In one PostgreSQL transaction it computes the trial balance for one tenant, legal entity, and book through the fiscal period end date, persists that population on the existing `trial_balance_snapshot` and `trial_balance_line` tables, and sets `fiscal_period.period_status_code` to `soft_closed` or `hard_closed`. Posted journals are never rewritten. Re-invoking close on an already `hard_closed` period, or on a period already at the requested status, replays the existing snapshot and writes no second snapshot or close event.

## Consequences

Controllers close books through the posting adapter instead of a raw status update. Ordinary posting remains rejected for every non-open period status and writes zero proposal, journal, line, or receipt rows. Soft-close may later upgrade to hard-close by reusing the same snapshot. A hard-closed period without a snapshot fails closed and names the restore-and-retry action.
