# PostgreSQL runtime baseline

## Current baseline

Accounting integration and migration regressions run on PostgreSQL **18.6**, the current supported PostgreSQL 18 minor release as of 2026-09-05. PostgreSQL 18.6 was released on 2026-08-13. The PostgreSQL project states that the release fixes 28 security vulnerabilities and more than 110 bugs across the supported branches; PostgreSQL 18.5 was not shipped because of a regression.

GitHub Actions pins the official multi-platform `postgres:18.6` image by immutable OCI index digest:

`sha256:4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280`

The tag is kept alongside the digest so reviewers can see the intended minor version; the digest, not the mutable tag, is the execution identity. Accounting tests still target PostgreSQL major-version 18 semantics. This minor update does not alter journal, posting, close, reconciliation, Billing ACL, or accounting-policy authority.

## Upgrade acceptance

The exact-head Accounting Foundation job must execute the complete behavior suite, real PostgreSQL regressions, 100% production statement/branch coverage, repository contracts, packaging, SBOM, and reproducibility checks against the pinned 18.6 service before the baseline can be treated as GREEN. A Docker Hub digest lookup is provenance for selecting the image, not execution evidence.

Historical test and ADR statements that explicitly record PostgreSQL 18.4 remain historical evidence when they describe an exact past run. Statements that claim 18.4 is the *current* runtime baseline must be read as superseded by this document and should be corrected when their canonical owner lane next edits them.

## References

PostgreSQL Global Development Group. (2026a, August 13). *PostgreSQL 18.6, 17.11, 16.15, 15.19, 14.24 and 19 Beta 3 released*. https://www.postgresql.org/about/news/postgresql-186-1711-1615-1519-1424-and-19-beta-3-released-3365/

PostgreSQL Global Development Group. (2026b). *PostgreSQL release notes*. https://www.postgresql.org/docs/release/

Docker, Inc. (2026). *Official postgres:18.6 image*. https://hub.docker.com/_/postgres
