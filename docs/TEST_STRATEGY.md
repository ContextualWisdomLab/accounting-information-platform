# Test Strategy

## Test layers

1. **Value contracts:** decimal, currency, codes, references, hashes, line sidedness.
2. **Proposal invariants:** minimum lines, unique line numbers, exact balance.
3. **Policy resolution:** tenant, legal entity, book role, period, currency, and account mapping.
4. **Posting behavior:** idempotent replay, hash conflict, immutable receipt and journal.
5. **Reversal behavior:** original preservation, equal-and-opposite lines, duplicate reversal idempotency.
6. **Trial balance:** scope and date filters, exact debit/credit equality, reversal net-zero fixture.
7. **Persistence:** clean migration, upgrade rehearsal, concurrent idempotency, transaction rollback, RLS.
8. **Contracts:** producer and consumer fixtures for proposal, policy, receipt, and outbox events, including Billing `proposal_status` ingest (`validated`/`exported` accept; `draft`/`rejected`/`posted` and operational reject rows fail closed).
9. **Security:** cross-tenant references, oversized payloads, malformed decimals, replay storms, unauthorized close or reversal.

## Merge gates

- Production statement coverage: 100%.
- Production branch coverage: 100%.
- Public API docstrings: 100% through `-Werror` documentation and package inspection.
- No skipped required test evidence.
- Exact-head CI, SAST, secret scan, dependency policy, SBOM, and provenance.
- Hash-locked coverage 7.15.4 wheels (including the CPython 3.13 manylinux x86_64 artifact) plus setuptools, wheel, packaging, psycopg, and psycopg-binary so `--require-hashes --only-binary=:all:` and `--no-build-isolation` packaging smoke tests resolve offline.
- PostgreSQL 18 integration evidence for a two-line post, idempotent replay, append-only reversal, trial-balance tie-out to the journal population, first-class period close with a durable snapshot, closed-period zero-row rejection, idempotent re-close, continued posting into a later open period, catalog policy resolution from a Billing `validated` proposal, a Billing `validated` cash receipt (`cash_receipt` debit / `accounts_receivable` credit) posted from catalog mapping with ingest+post+replay, HTTP `POST /journal-proposals` accept (catalog policy on the receipt, idempotent replay, and zero-row rejection for draft, operational reject, closed period, and cross-tenant), HTTP `GET /posting-receipts` lookup by Billing invoice_draft or cash_receipt idempotency key (same receipt after post and replay; cross-tenant and unknown key write zero rows), HTTP `POST /period-closes` plus `GET /trial-balances` (close receipt and snapshot TB after posted journals, idempotent re-close, live TB on an open period, cross-tenant and missing period write zero rows, `GET /healthz` 200), Billing #15 pull (`pull_validated_journal_proposals` / `accept_pulled_proposals` / `POST /billing-proposal-pulls`) of invoice and cash `validated` proposals against a fake Billing WSGI/HTTP app (same receipt on `GET /posting-receipts`, replay writes zero extra journals, `draft`/`exported`/`rejected` wire items are not posted, cross-tenant Billing 404 and AIS tenant mismatch write zero rows, missing `BILLING_BASE_URL` and Billing 422/5xx fail closed with a next action), HTTP `POST /journal-reversals` (append-only reverse of a posted invoice, original receipt lookup unchanged, reversing receipt at `reversal:{journal_reference}`, idempotent replay, cross-tenant 403, unknown journal or key and reverse after hard close write zero reversing journals, `GET /journal-reversals` 405), HTTP `GET /account-role-mappings` (seeded `accounts_receivable` 110100, `usage_revenue` 410100, and `cash_receipt` 110200 plus stored policy versions; cross-tenant 403 writes zero rows; unknown book or entity fails closed; `POST /account-role-mappings` 405), HTTP `GET /journals` (posted invoice lines 110100/410100 by Billing idempotency key or journal reference; original key still returns the original journal after `POST /journal-reversals`; reversing key or reference returns the reversing lines; cross-tenant 403 writes zero rows; unknown key is 404; `POST /journals` 405), and HTTP `POST /fiscal-periods` plus `GET /fiscal-periods` (open the next period and post into it, idempotent replay writes no second row, GET returns open status and dates, cross-tenant 403 writes zero rows, open of `hard_closed` fails closed, `POST /period-closes` still closes a period opened over HTTP).

## Required real-world fixtures

- Billing invoice with receivable, revenue, and tax.
- Billing cash receipt settling receivable (`cash_receipt` debit / `accounts_receivable` credit).
- Prepaid credit producing contract liability rather than immediate revenue.
- Provider payout with cash clearing and provider fee.
- Refund and chargeback correction.
- Closed-period hold and next-period adjustment.
- Duplicate proposal replay and conflicting payload reuse.
- Reversal and replacement.
- Cross-tenant account mapping attempt.
