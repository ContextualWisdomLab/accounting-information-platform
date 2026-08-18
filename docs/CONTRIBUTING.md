# Contributor and agent operations

Buyer and operator documentation lives in the root [README](../README.md).
This file keeps repository-operation rules that are not product claims.

## Writer boundary

- This repository is the only CWL authority for legal books, posted journals,
  reversals, fiscal-period control, trial balances, and
  `accounting_posting_receipt`.
- The Metering Billing Platform owns commercial facts and
  `accounting_journal_proposal`. Billing and other sources may propose
  semantic account roles. They may not select final chart-account identifiers,
  write journal tables, or claim that a proposal has posted.
- AI systems may explain or propose classifications. They cannot approve
  policy, open periods, map chart accounts, or post journals.

Normative development rules are in [AGENTS.md](../AGENTS.md) and
[CLAUDE.md](../CLAUDE.md).

## Independent verification

Run the commands in the root README from this checkout only. Do not require a
Naruon or sibling worktree. Production statement and branch coverage must
remain 100%. `scripts/validate_repository.py` rejects missing required files,
unresolved placeholder tokens, mutable GitHub Action tags, SQL naming for
schemas, tables, columns, policies, and functions, and `UPDATE`/`DELETE` of
`general_journal` or `journal_entry_line`.

## Exact-head CI

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) checks out the exact
commit under test, pins Actions to full commit SHAs, and installs quality
dependencies with `--require-hashes`. Mutable tags such as `@v4` are not
acceptable evidence. GitHub-hosted required checks on that exact head are the
merge-readiness signal. Local reproduction is supporting evidence, not a
substitute for those checks.

## Pull-request stacking

The protected default branch may remain a bootstrap commit while successor
heads carry product work. Current line:

1. `agent/initial-accounting-foundation` — repository bootstrap (also the
   commit currently on `main`).
2. `agent/accounting-posting-foundation` — first executable foundation
   (open draft against `main`).
3. Later successor heads stack on the previous product head.

Do not merge a successor by collapsing it into a bootstrap README or by
treating a review-bot summary as the product description. Open one focused
change per successor. Keep stacked drafts in Draft until exact-head required
checks and independent review complete. Do not treat “may be composed by
Naruon” as a defect; keep independent run documented in the root README.

## Review evidence is not the product

CodeRabbit, OpenCode, Strix, and similar summaries are review artifacts.
They are not the buyer/operator story, the published contract, or proof of
readiness. Do not copy them into the root README.

Human approval, if required by repository settings, is a GitHub branch
protection or org-workflow concern. Do not encode hidden approve-gates,
auto-merge instructions, or bot-only merge language in the root README.

## When behavior changes

Update `CHANGELOG.md`, the relevant ADR, and
[STANDARD_TRACEABILITY.md](doctoring/STANDARD_TRACEABILITY.md) when authority,
contracts, or monetary invariants change. Write a failing test before changing
reference-core behavior.
