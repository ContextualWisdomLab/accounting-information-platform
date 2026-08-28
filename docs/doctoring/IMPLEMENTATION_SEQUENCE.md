# Implementation Sequence

The protected `develop` foundation already contains the durable PostgreSQL accounting boundary, the host-mountable HTTP surface, and the immutable ISO 20022 `camt.053.001.14` bank-statement evidence registry. `PostgresPostingLedger` is the authoritative durable adapter for accepted accounting commands; the in-memory ledger remains a reference oracle for behavior parity. Documentation must not describe persistence, HTTP, or bank-statement evidence acceptance as unimplemented.

## Current integrated foundation

The integrated accounting system of record owns legal entities, accounting books, chart accounts, fiscal-period controls, immutable balanced journals and reversals, posting receipts, transactional outbox evidence, close evidence, trial balance, current statutory/management projections, bank-account-to-book assignments, immutable bank-statement source evidence, and normalized statement entries. Commands and their outbox effects commit atomically. Posted facts remain append-only; corrections use reversal and reposting. Exact decimals are preserved at command, domain, PostgreSQL, and bank-evidence boundaries.

A bank-statement entry is evidence only. It cannot post, reverse, approve, or mutate a journal, and its immutable source provenance remains distinct from authoritative posting receipts.

Release evidence remains exact-head evidence. A successful predecessor PR head is not release evidence for a later merge commit, even when the source tree is identical. Integrated-head package, SBOM, provenance, migration/recovery, security, and review gates must be observed on the protected head before release or tagging.

## Next buyer-visible dependency: deterministic reconciliation

The next bounded accounting path is:

1. open one reconciliation run scoped to tenant, legal entity, accounting book, bank account, currency, and cutoff;
2. select immutable unmatched statement entries and authoritative posted cash-journal populations without rewriting either source;
3. apply deterministic matching precedence: stable provider/reference identity first, then exact amount/currency plus bounded date policy, then an explicitly approved composite rule;
4. abstain on ambiguous evidence and create an explicit exception with an owner and next action;
5. represent one-to-one, split, and aggregate matches with link rows whose exact allocation amounts conserve both source populations and prevent double consumption by another active match;
6. record review/approval decisions with actor, purpose, evidence, effective time, and system time; model output may summarize or prioritize but never approve;
7. produce an exact book-to-bank bridge that explains statement closing balance versus posted book cash balance using reproducible outstanding-item populations;
8. emit close evidence and exportable provenance without automatically posting from statement lines.

The durable review successor records one immutable human decision per tenant,
run, and match. PostgreSQL computes the approval snapshot from the candidate and
allocation rows, locks the parent match row before the snapshot advisory lock
across approval/allocation races, and
rejects terminal transition when the reviewed snapshot is no longer current.
This is review evidence only; it never becomes journal-posting authority.

Any approved adjusting-journal proposal must re-enter the existing accounting command boundary with its own idempotency key, immutable source evidence, open-period checks, policy resolution, purpose-bound authorization, and authoritative posting receipt.

## Later commercial increments

After deterministic reconciliation is integrated, continue with purpose-bound accounting authorization, multi-currency/FX, consolidation and intercompany, fixed assets, revenue-recognition support, tax interfaces, management reporting, and operational/recovery hardening. Each increment must preserve tenant isolation, database-owned invariants where feasible, exact decimal arithmetic, append-only accounting facts, and source-to-receipt provenance.

## Standards boundary

Financial-statement presentation remains a versioned projection over the immutable journal population. IFRS 18 is the presentation/disclosure authority and is effective for annual reporting periods beginning on or after 1 January 2027, with earlier application permitted; this repository does not claim statutory compliance merely by implementing a projection. XBRL 2.1 remains an external reporting representation/taxonomy boundary rather than a ledger schema authority. ISO 20022 remains the bank-message adapter authority, not a journal authority. APA 7 references are maintained in `docs/doctoring/REFERENCES.md`, with decision mapping in `docs/doctoring/STANDARD_TRACEABILITY.md`.
