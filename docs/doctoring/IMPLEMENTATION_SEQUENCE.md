# Implementation Sequence

The protected `develop` foundation already contains the durable PostgreSQL accounting boundary and the host-mountable HTTP surface. `PostgresPostingLedger` is the authoritative durable adapter for accepted accounting commands; the in-memory ledger remains a reference oracle for behavior parity. Documentation must not describe persistence or HTTP as unimplemented.

## Current integrated foundation

The integrated accounting system of record owns legal entities, accounting books, chart accounts, fiscal-period controls, immutable balanced journals and reversals, posting receipts, transactional outbox evidence, close evidence, trial balance, and current statutory/management projections. Commands and their outbox effects commit atomically. Posted facts remain append-only; corrections use reversal and reposting. Exact decimals are preserved at the command, domain, and PostgreSQL boundaries.

Release evidence remains exact-head evidence. A successful predecessor PR head is not release evidence for a later merge commit, even when the source tree is identical. Integrated-head package, SBOM, provenance, migration/recovery, security, and review gates must be observed on the protected head before release or tagging.

## Next buyer-visible dependency

After the documentation successor is integrated, the next bounded accounting path is:

1. accept immutable bank-statement evidence through an explicitly versioned ISO 20022 adapter boundary;
2. preserve the original artifact hash, normalized statement/entry identity, tenant, legal entity, accounting book, bank account, exact amount/currency, source locator, and recorded time;
3. perform deterministic reconciliation before any probabilistic or model-assisted proposal;
4. abstain on ambiguous evidence and create an explicit exception with an owner and next action;
5. conserve exact amounts across one-to-one, split, and aggregate allocations;
6. produce an exact book-to-bank bridge and close evidence from immutable bank and posted-book populations;
7. require explicit review for any later adjusting-journal proposal.

There is **no automatic posting from statement evidence**. A bank statement entry is evidence, not a journal command, posting receipt, approval, or accounting-policy decision. Any approved adjustment must re-enter the existing accounting command boundary with its own idempotency key, immutable source evidence, open-period checks, policy resolution, and authoritative posting receipt.

## Later commercial increments

After deterministic reconciliation is integrated, continue with purpose-bound accounting authorization, multi-currency/FX, consolidation and intercompany, fixed assets, revenue-recognition support, tax interfaces, management reporting, and operational/recovery hardening. Each increment must preserve tenant isolation, database-owned invariants where feasible, exact decimal arithmetic, append-only accounting facts, and source-to-receipt provenance.

## Standards boundary

Financial-statement presentation remains a versioned projection over the immutable journal population. IFRS 18 is the presentation/disclosure authority and is effective for annual reporting periods beginning on or after 1 January 2027, with earlier application permitted; this repository does not claim statutory compliance merely by implementing a projection. XBRL 2.1 remains an external reporting representation/taxonomy boundary rather than a ledger schema authority. APA 7 references are maintained in `docs/doctoring/REFERENCES.md`, with decision mapping in `docs/doctoring/STANDARD_TRACEABILITY.md`.