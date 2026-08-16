# ADR 0003: Append-only journals and reversals

**Status:** Accepted

## Decision

A posted general journal is immutable. Correction creates a linked equal-and-opposite reversal and, when necessary, a separately approved replacement.

## Consequences

Historical audit evidence remains intact. APIs and database permissions must not expose journal update or delete operations. Reporting reconstructs effects from the complete population.
