# PostgreSQL runtime baseline

## Current baseline

Accounting integration and migration regressions run on PostgreSQL **18.6**, the current supported PostgreSQL 18 minor release as of 2026-09-05. PostgreSQL 18.6 was released on 2026-08-13. The PostgreSQL project states that the release fixes 28 security vulnerabilities and more than 110 bugs across the supported branches; PostgreSQL 18.5 was not shipped because of a regression.

GitHub Actions pins the official multi-platform `postgres:18.6` image by immutable OCI index digest:

`sha256:4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280`

The tag is kept alongside the digest so reviewers can see the intended minor version; the digest, not the mutable tag, is the execution identity. Accounting tests still target PostgreSQL major-version 18 semantics. This minor update does not alter journal, posting, close, reconciliation, Billing ACL, or accounting-policy authority.

## Exact-head acceptance

The exact-head Accounting Foundation job must execute the complete behavior suite, real PostgreSQL regressions, 100% production statement/branch coverage, repository contracts, packaging, SBOM, and reproducibility checks against the pinned 18.6 service before the CI baseline can be treated as GREEN. A Docker Hub digest lookup is provenance for selecting the image, not execution evidence.

Historical test and ADR statements that explicitly record PostgreSQL 18.4 remain historical evidence when they describe an exact past run. Statements that claim 18.4 is the *current* runtime baseline must be read as superseded by this document and should be corrected when their canonical owner lane next edits them.

## Existing-cluster upgrade acceptance

Changing the ephemeral GitHub Actions service image **does not prove an existing database upgraded safely**. PostgreSQL states that an 18.x-to-18.6 upgrade does not require dump/restore, but the 18.6 release notes identify configuration, data, script, and index checks that can require operator action. Release evidence for an existing accounting database therefore keeps the CI image change separate from an operator-controlled cluster upgrade.

Before upgrading an existing cluster, inventory and retain the following evidence for the target cluster and every replica or failover member that can become authoritative:

- logical replication slots and non-core logical-decoding plugins. PostgreSQL 18.6 introduces `output_plugin_libraries`; installations that depend on third-party output plugins must explicitly allow them, and `pg_upgrade --check` can reject an incompatible target configuration;
- use of `pgcrypto` PGP encryption with legacy algorithms that OpenSSL can reject. The 18.6 security fix makes unsupported-cipher failures visible; potentially affected ciphertext must be identified and recovered/re-encrypted according to the release notes rather than treated as valid encrypted evidence;
- operational or migration scripts containing `COPY ... FROM STDIN`. Scripts that intentionally exercise a `COPY` command which can fail before copy-in begins must retain a `\.` terminator so following data cannot be interpreted as SQL;
- GIN indexes and table statistics. The release notes describe possibly corrupt `reltuples` after affected parallel GIN index builds; the upgrade runbook must inspect the documented catalog condition and repair affected statistics with the PostgreSQL-recommended `ANALYZE` or equivalent index operation before accepting the cluster;
- `btree_gist` and `ltree` indexes. If present, follow the 18.6 release-note reindex guidance before treating the upgraded database as release-ready.

The preflight must determine applicability from the live database catalogs and configuration; absence is evidence only when the exact target cluster was queried. Repository source search or a clean CI database is not sufficient proof that a production cluster has no affected extension, index, replication slot, historical ciphertext, or operator script.

After upgrade, retain the exact server identity (`SHOW server_version` and `SHOW server_version_num`), extension/index/replication-slot postchecks that correspond to the preflight inventory, migration status, application readiness, and the full accounting acceptance suite. A rollback or failover decision must preserve immutable posted facts and reconciliation/close evidence; PostgreSQL runtime recovery is not permission to rewrite accounting history.

## References

PostgreSQL Global Development Group. (2026a, August 13). *PostgreSQL 18.6, 17.11, 16.15, 15.19, 14.24 and 19 Beta 3 released*. https://www.postgresql.org/about/news/postgresql-186-1711-1615-1519-1424-and-19-beta-3-released-3365/

PostgreSQL Global Development Group. (2026b, August 13). *PostgreSQL 18.6 release notes*. https://www.postgresql.org/docs/18/release-18-6.html

Docker, Inc. (2026). *Official postgres:18.6 image*. https://hub.docker.com/_/postgres
