# ADR 0003: Append-only journals and reversals

**Status:** Accepted

## Context

Posted journals are legal-book facts. Updating or deleting a posted journal would destroy the evidence path from trial balance through journal lines, posting receipts, source proposals, and payload hashes.

Corrections must remain reconstructable as derivation: the original journal stays an entity, reversal is a later activity, and the replacement (when required) is a separately approved entity attributed to policy and actor references (World Wide Web Consortium, 2013).

## Decision

A posted general journal is immutable. Correction creates a linked equal-and-opposite reversal and, when necessary, a separately approved replacement.

## Consequences

Historical audit evidence remains intact. APIs and database permissions must not expose journal update or delete operations. Reporting reconstructs effects from the complete population. Recovery procedures reconcile hashes and issue reversals; they do not rewrite a posted journal.

## References

World Wide Web Consortium. (2013). *PROV-O: The PROV ontology*. https://www.w3.org/TR/prov-o/
