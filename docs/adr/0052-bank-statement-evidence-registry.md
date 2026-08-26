# ADR 0052: Immutable ISO 20022 bank-statement evidence registry

**Status:** Accepted

## Decision

AIS owns accepted immutable bank-statement evidence, bank-account-to-book mapping, and normalized statement entries. The bank or provider owns the raw statement facts. A statement entry is evidence only: it cannot post, reverse, approve, or change a journal.

The first adapter pins `camt.053.001.14` / `BankToCustomerStatementV14`. The parser dispatches by the preserved message-definition identifier, validates against an integrity-pinned vendored fixture/manifest set, and rejects another revision unless a later compatibility adapter is introduced. Runtime parsing performs no network or filesystem retrieval based on input content. DTD, external entities, XInclude, stylesheet execution, and schema download are disabled. Input bytes, XML depth, element count, attribute count, text bytes, statement count, and entry count are bounded. Monetary values become exact decimals with currency preserved separately. A `TxDtls` detail records `AmtDtls/TxAmt/Amt` only; instructed, charge, or other sibling amounts are not substituted, and a missing or non-`CRDT`/`DBIT` detail indicator fails closed. Every normalized entry keeps a source locator.

The relational database stores hashes, message metadata, locators, and normalized facts. The original artifact belongs in a host-owned immutable evidence store; HTTP binds a process-lifetime store, and a library caller must supply one. Reuse of an ingestion idempotency key with a changed artifact fails closed. The same source bytes produce one canonical statement population and serialize on that digest so concurrent keys cannot race the unique constraint into an unhandled error. Concurrent ingest of the same bank-account statement identity serializes on that identity so a changed material payload fail-closes instead of raising an unhandled unique violation. Balance amounts may be zero; entry and detail movement amounts stay strictly positive. The same statement identity with unchanged normalized entries is duplicate delivery and replays the first artifact; changed material entry evidence, including remittance text and counterparty hash, fails closed until an explicit correction contract exists. Raw artifact bytes are stored before the relational rows commit so a receipt never references missing bytes; identical-digest orphans are idempotent.

Bank-account assignment is effective-dated and binds one bank account to a legal entity, accounting book, and a chart account that must belong to that same book at the PostgreSQL boundary. The assignment row also carries the composite `(tenant, legal_entity, accounting_book)` foreign key so a book cannot be paired with another legal entity. Migration `0012_bank_assignment_command_identity.sql` completes the repository command contract for assignments: every assignment carries tenant-scoped `assignment_idempotency_key` plus an immutable canonical `assignment_command_hash`, so an exact retry returns the original binding (`replayed: true`) while reuse of the key with different evidence fails closed as `409`. A partial unique index admits only one active (`valid_to IS NULL`) binding per tenant, bank account, and book; re-binding requires closing the prior row with an explicit `valid_to`. Ingest compares the parsed statement `account_identifier_hash` to the registered bank account; a same-currency statement for a different IBAN/Othr identity fails closed. Every entry must carry the registered statement account currency: foreign-exchange accounting stays explicitly out of scope until rate source, rate type, rounding, remeasurement, and translation policy exist (TRD), so a foreign-currency entry fails closed before persistence. Entry material evidence includes the ISO 20022 bank-transaction-code domain/family/sub-family, and counterparty evidence follows entry direction — a `CRDT` entry records the payer (`Dbtr`) name and a `DBIT` entry records the payee (`Cdtr`) name before falling back to whichever party the statement supplies. Generic list/read models expose `account_identifier_hash`, not a plaintext account identifier.

HTTP surfaces are `POST /bank-accounts`, `POST /bank-account-assignments`, `POST /bank-statements`, `GET /bank-statements`, and `GET /bank-statement-entries`. Keyverse remains the owner of `X-CWL-Tenant-Reference`; AIS fail-closes on a missing or mismatched header and does not provision tenants. Billing consume, pull, and `proposal_status` are unchanged.

## Consequences

Controllers can ingest the same statement repeatedly without duplicating evidence and can prove which original artifact produced each normalized entry. Matching, exception workflow, and journal creation remain later slices.

## References

International Organization for Standardization. (2026). *ISO 20022-1:2026 Financial services—Universal financial industry message scheme—Part 1: Metamodel*. https://www.iso.org/standard/20022-1

International Organization for Standardization. (2026). *ISO 20022-4:2026 Financial services—Universal financial industry message scheme—Part 4: XML Schema generation*. https://www.iso.org/standard/20022-4

International Organization for Standardization. (2026). *ISO 20022-9:2026 Financial services—Universal financial industry message scheme—Part 9: Syntax generation requirements and rules*. https://www.iso.org/standard/20022-9

ISO 20022 Registration Authority. (2026). *Bank-to-Customer Cash Management: camt.053.001.14 BankToCustomerStatementV14*. https://www.iso20022.org/iso-20022-message-definitions?search=camt.053
