# ADR 0001: Accounting authority

**Status:** Accepted

## Context

CWL already has a Metering Billing Platform for usage, pricing, entitlement, invoice intent, payments, refunds, provider settlement, and commercial reconciliation. Extending that product into a general ledger would collapse commercial billing truth and statutory accounting truth into one system, and would let provider events dictate book treatment.

Financial-statement presentation and disclosure are a later projection of legal books, not a property of invoices or settlements (IFRS Foundation, 2024). Stakeholder concerns, authority boundaries, and architecture views must stay explicit so billing, operations, audit, and reporting do not share one write path (International Organization for Standardization, 2022).

## Decision

The Accounting Information Platform is the sole CWL authority for legal accounting books, posted journals, reversals, fiscal-period control, trial balances, and accounting posting receipts.

## Consequences

Billing and operational products submit proposals and retain their own source facts. They cannot write accounting tables, choose final chart accounts, or claim that a proposal has posted. Only this repository may issue an authoritative `posted`, `held`, `rejected`, or `reversed` receipt.

## References

IFRS Foundation. (2024). *IFRS 18 presentation and disclosure in financial statements*. https://www.ifrs.org/projects/completed-projects/2024/primary-financial-statements/

International Organization for Standardization. (2022). *ISO/IEC/IEEE 42010:2022 software, systems and enterprise—Architecture description*. https://www.iso.org/standard/74393.html
