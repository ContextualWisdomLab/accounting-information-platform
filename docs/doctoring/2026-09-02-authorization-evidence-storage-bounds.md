# Doctoring record: bounded authorization evidence

**Date:** 2026-09-02

**Scope:** durable purpose-bound authorization-decision evidence

## Research question

How should AIS prevent authenticated identity metadata from becoming an unbounded PostgreSQL audit-storage input without retaining bearer material or inventing a probabilistic truncation rule?

## Decision

The trusted identity adapter continues to validate external credentials and supplies only normalized opaque CWL references to AIS. Durable authorization evidence now treats those references as a bounded internal protocol rather than as arbitrary identity-provider strings.

Each normalized identity reference persisted by `authorization_decision_record` is an ASCII `urn:cwl:` reference with an authorization-profile ceiling of 255 octets. This is a CWL profile decision, not a claim that every OIDC field is limited to 255 characters. The ceiling aligns the normalized principal identifier budget with the normative OpenID Connect `sub` ceiling: OpenID Connect Core specifies that the Subject Identifier must not exceed 255 ASCII characters. If a provider-native identifier plus CWL namespace material cannot fit the normalized profile, the trusted adapter must derive a stable opaque reference rather than copying an oversized raw claim into the accounting audit store.

RFC 8141 permits a URN namespace definition to specify its own syntax and interoperability constraints. AIS therefore constrains its internal `urn:cwl:` authorization references at the anti-corruption boundary while leaving provider-native claim representation in the identity system that owns it.

The remaining text budgets are derived from executable contracts rather than free-form estimates: operation and purpose codes follow the existing 64-octet code grammar; a permission is two such code components plus the separator (129 octets); `policy_version` is a bounded release identifier (64 octets); and `correlation_reference` mirrors the existing 512-octet HTTP evidence contract. PostgreSQL repeats these bounds so a privileged/direct SQL path cannot bypass the application checks and inflate append-only evidence.

## DDD and security boundary

Identity/Policy is a foreign bounded context. `AuthenticatedPrincipal` is an anti-corruption value object; `authorization_decision_record` is Accounting-owned immutable evidence of the policy-enforcement decision. Raw JWTs, bearer tokens, provider profile documents, names, email addresses, and other unnecessary PII are not durable accounting authorization evidence.

The database constraints are fail-closed. Oversized or malformed normalized references write no authorization row and therefore cannot reach an accounting operation through the normal HTTP enforcement path. This is a storage-availability and provenance control, not a certification claim.

## RED → GREEN traceability

- Review finding: caller-derived identity references were unbounded PostgreSQL `text` columns.
- GREEN schema: all five persisted identity references require bounded CWL URN syntax; operation, permission, purpose, policy-version, and correlation evidence have explicit database ceilings.
- Regression: `tests/test_authorization_evidence_storage_contract.py` ratchets every durable text budget and the normalized reference grammar.
- Existing PostgreSQL/HTTP authorization suites continue to prove tenant isolation, append-only evidence, malformed-close authorization, and the 512-octet correlation edge.

## Research basis

International Telecommunication Union. (2025). *OpenID Connect Core 1.0—Errata Set 2 (Recommendation ITU-T X.1285)*. https://www.itu.int/rec/T-REC-X.1285-202505-I/en

Sakimura, N., Bradley, J., Jones, M., de Medeiros, B., & Mortimore, C. (2014). *OpenID Connect Core 1.0 incorporating errata set 2*. OpenID Foundation. https://openid.net/specs/openid-connect-core-1_0.html

Saint-Andre, P., & Klensin, J. (2017). *Uniform Resource Names (URNs) (RFC 8141)*. Internet Engineering Task Force. https://doi.org/10.17487/RFC8141

## Evidence rule

These sources justify the identity-boundary/profile design; they do not establish accounting authority or compliance status. Merge or release requires one unchanged exact head to pass the repository and organization gates applicable to that head.
