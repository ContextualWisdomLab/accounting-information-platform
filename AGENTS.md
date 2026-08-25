# Agent Development Rules

## Authority

- Preserve the boundary between commercial billing truth and statutory accounting truth.
- Only this repository may issue an authoritative `posted`, `held`, `rejected`, or `reversed` accounting receipt.
- A source system may propose semantic account roles but may not select final chart-account identifiers or claim posting.

## Accounting invariants

- Use exact decimal arithmetic for every amount affecting a journal, balance, report, or reconciliation.
- Every journal must balance before persistence.
- Never update or delete a posted journal; correct it with an explicit reversal and replacement lineage.
- Every command uses an idempotency key and immutable source-payload hash.
- A closed fiscal period rejects ordinary posting.

## Database

- Use third-normal-form relational records for authoritative facts.
- Every database object name contains at least two `snake_case` words.
- Enforce tenant scope through composite foreign keys and PostgreSQL row-level security.
- Raw source payloads live in immutable object storage; relational rows retain hashes and references.

## Development

- Write a failing test before changing behavior.
- Require production statement and branch coverage of 100%.
- Document every public API, accounting policy decision, and monetary invariant.
- Pin GitHub Actions by full commit SHA and hash-lock quality dependencies.
- Update `CHANGELOG.md`, relevant ADRs, and standards traceability when behavior or authority changes.
