# Provider research foundation

## Architecture and modes

Company research is synchronous and provider-neutral. `ProviderRegistry` advertises a provider key,
capabilities, priority, enabled/configured status, and whether calls may cost credits; it never returns
credentials. `CompanyResearchService` owns tenant authorization, sequencing, freshness, the usage
ledger, run lifecycle, and normalized fact persistence. Adapters only return transport-neutral
`ProviderResult` and `ProviderFact` values.

The default `PUBLIC_ONLY` mode calls only `PUBLIC_WEB`. `PUBLIC_THEN_EXTERNAL` runs public research
before the explicitly selected external adapter, while `EXTERNAL_ONLY` skips public retrieval.
Selecting either external mode is an explicit credit-spend request and requires Organization Admin.
Marketing Users may run public research, Read Only members may only view run/provider information,
and all reads and writes are scoped through the tenant's `OrganizationCompany` relationship.

Successful calls are reused unless `force_refresh=true`. Public results default to 24 hours and
external results to seven days; operators can set `PUBLIC_WEB_FRESHNESS_HOURS` and
`EXTERNAL_PROVIDER_FRESHNESS_HOURS`. These conservative defaults protect credits but are policy,
not claims that company data remains accurate for those periods.

## Public web policy

`PublicWebProvider` starts from a known normalized domain. It fetches the homepage and at most three
same-domain pages whose visible labels clearly identify About, Careers/Jobs, Contact, or Locations.
It does not perform general crawling or visit social networks. Requests use a named user agent,
ten-second timeout, at most three redirects, HTML-only responses, and a 1 MB response limit.

Every initial URL and redirect target is resolved and rejected if any address is non-global,
including loopback, private, link-local, and reserved networks. URL credentials, custom ports, and
non-HTTP(S) schemes are rejected. Production network egress controls should provide a second layer
against DNS rebinding between validation and connection.

Public observations use `PUBLIC_EVIDENCE` and redistribution `UNKNOWN`. A retrieved careers page may
support `active_hiring=true`; a missing page never creates `false`. Descriptions are stored only from
explicit HTML metadata, with URL and retrieval timestamp.

## Hunter configuration and licensed data

Set `HUNTER_API_KEY` in the process environment. An empty/missing key produces `not_configured` and a
categorized request failure rather than an application crash. Keys are never stored in the database,
returned by status routes, included in ledger identifiers, or logged. CI uses mock HTTP transports and
must never use a live key.

The Hunter adapter accepts a normalized domain, invokes its domain endpoint, and maps only
organization fields needed by this pilot (description, industry, country, headquarters, and employee
range). It does not persist or expose returned contacts. Empty results, HTTP errors, and rate limits
are handled without inventing facts or balances. Licensed observations use `THIRD_PARTY_LICENSED`
with redistribution `UNKNOWN`, never automatically `ALLOWED`.

**MarketingIQ does not treat third-party provider data as MarketingIQ-owned redistributable data.**
Customers and operators remain responsible for provider contract, retention, and permitted-use terms.

## Provenance, usage, and raw responses

Each normalized observation appends a `CompanyFact` and `Evidence` linked to its `ResearchRun` and
`DataSource`. Classification, redistribution status, confidence, source URL/reference, retrieval time,
and workflow version are retained. Historical facts are not overwritten. Missing observations create
no fact, rather than a negative assertion.

Every provider attempt creates a sanitized `ProviderUsage` row containing tenant/company, operation,
times, outcome category, known credits/cost, a safe request identifier, error category, and cache-hit
flag. Aggregate reporting returns today's request/success/failure counts, credits used, and last call.
Remaining credits are `null` (unknown) unless an adapter reliably reports them; values are never
fabricated.

Raw provider response bodies are deliberately **not persisted** in this slice. Normalized facts and
the sanitized ledger are sufficient for operation and avoid accidental retention of secrets or
personal/licensed payloads. A future diagnostic store would require explicit opt-in, field-level
redaction, access control, encryption, and a documented deletion period.

## Deferred work

Company/contact search, email finding or verification, contact scraping, provider account-policy
configuration, background workers, scoring, campaigns, billing, raw-payload storage, and additional
adapters are intentionally deferred.
