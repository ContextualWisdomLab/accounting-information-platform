# ADR 0046: HTTP fail-closed HomeTax submission

**Status:** Accepted

## Decision

AIS exposes `accept_home_tax_submission` and `POST /home-tax-submissions` on the same stdlib HTTP surface as the period VAT register (ADR 0045). The only request identity header is purpose-limited `X-CWL-Tenant-Reference`. Body scope keys are the same as the register: `legal_entity_reference`, `book_reference`, and `fiscal_period_reference`. Optional `book_reference` may be supplied as `accounting_book_reference`. A tenant-header mismatch is rejected before the read or write and writes zero rows.

The command first loads that scope through the existing VAT register loader (`lookup_vat_period_register` / `PostgresPostingLedger.load_vat_period_register`). Unknown legal entity, book, or period is the same 404 as `GET /vat-period-registers`. A missing or incomplete loadable register document is HTTP 422 with `submission_status_code=rejected` and `rejection_reason_code=register_unavailable`. AIS does not call a network NTS/HomeTax API in that case.

A loadable register is not enough to transmit. Presence of the purpose-limited AIS secret `ACCOUNTING_HOMETAX_CREDENTIAL` is checked only as a non-empty environment value. AIS does not read NTS passwords, does not invent credential field names, and does not add `COPILOT_GITHUB_TOKEN`. A missing credential is HTTP 422 with `rejection_reason_code=hometax_credential_missing`. A present credential still does not open a network socket in this slice; the command persists `rejection_reason_code=hometax_transport_unavailable`. This slice never returns `submission_status_code=transmitted`.

Rejected receipts that have resolved catalog foreign keys are persisted on `accounting_integration.home_tax_submission`. The command must carry a non-empty tenant-scoped `idempotency_key`; the database makes that key unique per tenant and stores `as_of_date`, `closing_amount`, and `register_payload_hash`. Reusing the same key and unchanged scope/evidence returns the original receipt without inserting another row. Reusing it with changed evidence or scope fails closed with an idempotency conflict. The row does not store raw register JSON, NTS payloads, or secrets. `GET /home-tax-submissions?legal_entity_reference=&book_reference=&fiscal_period_reference=` lists those receipts. An empty scope is `home_tax_submissions` [], not 404. Reconstruct `vat_period_register` as `as_of_date` plus `closing_amount`. `POST /vat-period-registers` remains 405. Billing does not receive a VAT or HomeTax endpoint. AIS does not invent `tax_invoice` or `input_vat` catalog roles.

Always-present command keys are two-word (or longer) `snake_case`: `tenant_reference`, `legal_entity_reference`, `book_reference`, `fiscal_period_reference`, `vat_period_register`, `submission_status_code`, and `rejection_reason_code`. AIS does not add `party_reference` or echo NTS secrets.

IAS 1 requires presentation that helps users assess financial position, including current liabilities, accompanied by information that explains those statements (IFRS Foundation, 2022). Output VAT payable is that current liability. A filing command that transmits without the period VAT register would assert a liability the books cannot show. Processing integrity also requires fail-closed handling when the purpose-limited HomeTax credential is absent (American Institute of Certified Public Accountants, 2017).

## Consequences

Controllers can attempt a HomeTax filing from AIS and receive a durable rejected receipt instead of a fake NTS success. Transmission remains blocked until a later slice adds a real HomeTax transport. `GET /vat-period-registers` is unchanged. Catalog roles stay `tax_payable` → 210100. Billing remains the commercial tax assessor and does not grow a tax endpoint.
