# ADR 0052: Immutable ISO 20022 bank-statement evidence registry

**Status:** Accepted

## Decision

AIS owns accepted immutable bank-statement evidence, bank-account-to-book mapping, and normalized statement entries. The bank or provider owns the raw statement facts. A statement entry is evidence only: it cannot post, reverse, approve, or change a journal.

The first adapter pins `camt.053.001.14` / `BankToCustomerStatementV14`. The parser dispatches by the preserved message-definition identifier, validates against an integrity-pinned vendored fixture/manifest set, and rejects another revision unless a later compatibility adapter is introduced. Runtime parsing performs no network or filesystem retrieval based on input content. DTD, external entities, XInclude, stylesheet execution, and schema download are disabled. Input bytes, XML depth, element count, attribute count, text bytes, statement count, and entry count are bounded. Monetary values become exact decimals with currency preserved separately. Every normalized entry keeps a source locator.

The relational database stores hashes, message metadata, locators, and normalized facts. The original artifact belongs in a host-owned immutable evidence store. Reuse of an ingestion idempotency key with a changed artifact fails closed. The same source bytes produce one canonical statement population. The same statement identity with changed material entry evidence fails closed until an explicit correction contract exists.

Bank-account assignment is effective-dated and binds one bank account to a legal entity, accounting book, and a chart account that must belong to that same book at the PostgreSQL boundary. Generic list/read models expose `account_identifier_hash`, not a plaintext account identifier.

HTTP surfaces are `POST /bank-accounts`, `POST /bank-account-assignments`, `POST /bank-statements`, `GET /bank-statements`, and `GET /bank-statement-entries`. Keyverse remains the owner of `X-CWL-Tenant-Reference`; AIS fail-closes on a missing or mismatched header and does not provision tenants. Billing consume, pull, and `proposal_status` are unchanged.

## Consequences

Controllers can ingest the same statement repeatedly without duplicating evidence and can prove which original artifact produced each normalized entry. Matching, exception workflow, and journal creation remain later slices.

## References

International Organization for Standardization. (2026). *ISO 20022-1:2026 Financial services—Universal financial industry message scheme—Part 1: Metamodel*. https://www.iso.org/standard/20022-1

International Organization for Standardization. (2026). *ISO 20022-4:2026 Financial services—Universal financial industry message scheme—Part 4: XML Schema generation*. https://www.iso.org/standard/20022-4

International Organization for Standardization. (2026). *ISO 20022-9:2026 Financial services—Universal financial industry message scheme—Part 9: Syntax generation requirements and rules*. https://www.iso.org/standard/20022-9

ISO 20022 Registration Authority. (2026). *Bank-to-Customer Cash Management: camt.053.001.14 BankToCustomerStatementV14*. https://www.iso20022.org/iso-20022-message-definitions?search=camt.053
