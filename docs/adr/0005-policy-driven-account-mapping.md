# ADR 0005: Policy-driven account mapping

**Status:** Accepted

## Decision

Source proposals use semantic account roles. The accounting boundary resolves those roles to effective-dated chart accounts under explicit accounting-policy and posting-rule versions.

## Consequences

A customer may change charts of accounts without changing every upstream product. Historical postings retain the mapping and policy versions used at posting time.
