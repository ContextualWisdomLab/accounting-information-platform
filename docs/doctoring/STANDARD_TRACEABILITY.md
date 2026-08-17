# Standard Traceability

| Authority or standard | Product decision | Initial evidence |
|---|---|---|
| IFRS 15 / ASC 606 | Invoice, payment, and payout do not automatically determine revenue recognition; policy remains versioned and approved | Accounting/billing boundary and policy manifest |
| IAS 1 | Statement of financial position presents assets, liabilities, and equity; statement of profit or loss presents income and expenses; AIS stores that split as `account_class_code`. Hard-close transfers period profit or loss into equity account 310100 so the next sheet does not need a floating earnings plug. Comparative information for a prior period is an optional query on the same statement GET | Chart-account class migration, HTTP financial-statement read, ADR 0024, and ADR 0025 |
| IAS 10 | Soft-close rejects ordinary posts and allows append-only reversing adjustments before hard-close snapshots and locks the period | Two-step `POST /period-closes` and ADR 0023 |
| IFRS 18 | Financial-statement presentation is a versioned projection separate from the journal core | Reporting boundary and roadmap |
| ISO 20022-1:2026 | Bank and financial-message fields remain adapter contracts with explicit versioning and traceability | Future bank adapter boundary |
| PostgreSQL 18.4 | Use current supported minor release, UUIDv7, exact numeric types, composite foreign keys, and row-level security | Initial migration |
| RFC 9562 | New persistence identifiers use UUIDv7 | Initial migration |
| CloudEvents 1.0.2 | Commit authoritative events through a transactional outbox and replay by event identity | Outbox table and architecture |
| AICPA Trust Services Criteria (SOC 2) | Auditors read an append-only history of posted, reversed, and closed facts from existing `outbox_event` rows, including already-published rows, without marking publish | HTTP audit-event history and ADR 0027 |
| W3C PROV-O | Preserve entity, activity, agent, derivation, and attribution references across source proposal and posting | Source-reference and receipt contracts |
| ISO/IEC/IEEE 42010:2022 | Keep stakeholder concerns, authority boundaries, architecture views, and decisions explicit | Architecture and ADR set |
| JSON Schema Draft 2020-12 | Close external objects, version contracts, reject extra fields, and validate exact value formats | Three contract schemas |
| XBRL 2.1 | Treat external reporting taxonomy as a versioned projection rather than core ledger columns | Reporting roadmap |

The initial milestone does not claim production compliance with a jurisdiction's accounting, tax, or statutory reporting rules. It establishes controls and traceability required to implement reviewed policies without changing the journal authority model.
