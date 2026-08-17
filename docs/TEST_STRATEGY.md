# Test Strategy

## Test layers

1. **Value contracts:** decimal, currency, codes, references, hashes, line sidedness.
2. **Proposal invariants:** minimum lines, unique line numbers, exact balance.
3. **Policy resolution:** tenant, legal entity, book role, period, currency, and account mapping.
4. **Posting behavior:** idempotent replay, hash conflict, immutable receipt and journal.
5. **Reversal behavior:** original preservation, equal-and-opposite lines, duplicate reversal idempotency.
6. **Trial balance:** scope and date filters, exact debit/credit equality, reversal net-zero fixture.
7. **Persistence:** clean migration, upgrade rehearsal, concurrent idempotency, transaction rollback, RLS.
8. **Contracts:** producer and consumer fixtures for proposal, policy, receipt, and outbox events.
9. **Security:** cross-tenant references, oversized payloads, malformed decimals, replay storms, unauthorized close or reversal.

## Merge gates

- Production statement coverage: 100%.
- Production branch coverage: 100%.
- Public API docstrings: 100% through `-Werror` documentation and package inspection.
- No skipped required test evidence.
- Exact-head CI, SAST, secret scan, dependency policy, SBOM, and provenance.
- Hash-locked coverage 7.15.4 wheels (including the CPython 3.13 manylinux x86_64 artifact) plus setuptools, wheel, packaging, psycopg, and psycopg-binary so `--require-hashes --only-binary=:all:` and `--no-build-isolation` packaging smoke tests resolve offline.
- PostgreSQL 18 integration evidence for a two-line post, idempotent replay, append-only reversal, trial-balance tie-out to the journal population, first-class period close with a durable snapshot, closed-period zero-row rejection, idempotent re-close, and continued posting into a later open period.

## Required real-world fixtures

- Billing invoice with receivable, revenue, and tax.
- Prepaid credit producing contract liability rather than immediate revenue.
- Provider payout with cash clearing and provider fee.
- Refund and chargeback correction.
- Closed-period hold and next-period adjustment.
- Duplicate proposal replay and conflicting payload reuse.
- Reversal and replacement.
- Cross-tenant account mapping attempt.
