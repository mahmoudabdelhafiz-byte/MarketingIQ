# Contact discovery

Contact discovery is an explicit, tenant-authenticated workflow for a current `HIGH_PRIORITY` or `QUALIFIED` lead. It never runs when a company is attached and never sends outreach. Before an adapter call the service verifies tenant ownership, qualification/company lineage, fit freshness, and a recommended buyer role. A stale assessment produces a structured requalification-required conflict rather than silently requalifying.

## Provider boundary and credits

`ContactSearchProvider` and `EmailProvider` expose provider-neutral search, finder, and verifier results. Hunter uses Domain Search, Email Finder, and Email Verifier behind that boundary; missing credentials, authentication, rate limiting, network errors, and malformed responses become safe provider errors. No plan limits or credit prices are assumed. Every attempt has a `ProviderUsage` row (`CONTACT_SEARCH`, `EMAIL_FIND`, or `EMAIL_VERIFY`); provider-reported credit values are retained and otherwise remain null/unknown. Configurable `CONTACT_SEARCH_FRESHNESS_HOURS`, `EMAIL_FIND_FRESHNESS_HOURS`, and `EMAIL_VERIFY_FRESHNESS_HOURS` caches prevent repeat spend unless `force_refresh` is explicit.

## Matching and identity

The recommended buyer role is compared deterministically with normalized title tokens, department, and seniority, producing `EXACT`, `STRONG`, `PARTIAL`, or `UNKNOWN` plus a reason. No generative model is involved. A safe provider reference deduplicates candidates within a tenant; names alone are never fuzzy-merged. Emails are normalized and unique per contact.

## Privacy, provenance, and licensing

Records are tenant-private and contain only necessary B2B professional identity, role, and business-email fields. The workflow does not collect private phones, home addresses, sensitive attributes, family data, scrape social networks, or perform reverse-person lookup. Hunter observations are `THIRD_PARTY_LICENSED` and `RESTRICTED`, never automatically redistributable. Full provider payloads are not persisted. MarketingIQ therefore provides a tenant workflow, not a copied or public contact database.

Marketing Users and Organization Admins may explicitly spend provider credits; Read Only users may only view contacts. Audits contain IDs, provider, operation, result category, and buyer role—never an API key, provider payload, or email address. Application logs likewise receive no secrets or email payloads.

Verification maps only Hunter `valid` to `VALID`; `invalid`, `accept_all`, and risky categories remain distinct, and unknown or unrecognized results become `UNVERIFIABLE`. Ambiguity is never promoted to valid.
