# Company discovery

Company discovery fills the top of the funnel. Until now companies entered MarketingIQ only through
manual entry or CSV import; discovery proposes new companies that may match a product's active ICP,
for a human to accept into the catalog. Accepted companies then flow through the existing research,
fit, qualification, contact-discovery and campaign workflows unchanged.

## Flow

1. `POST .../products/{product_id}/discovery-runs` starts a synchronous run against the product's
   active ICP (highest version). The request to the provider contains the product name,
   description, value proposition, ICP criteria and employee bounds, plus an exclusion list.
2. The provider returns candidate companies (name, domain, optional country/industry, a short
   rationale and source URLs).
3. MarketingIQ validates every candidate and stores it as `PROPOSED` or `SKIPPED` with a reason.
4. A member reviews `GET .../discovery-candidates` and calls `accept` or `reject` on each one.
5. `accept` attaches the company through `CompanyService.attach_company` with
   `lifecycle_status=DISCOVERED`. Nothing is written to the company catalog before this step.

Discovery covers **companies only**. It never searches for, stores or returns people; contacts
still come only from the explicit, credit-gated contact-discovery workflow.

## Grounding and validation

The default adapter is `AnthropicCompanyDiscoveryProvider`: Claude via the Anthropic Messages API
with the server-side web search tool (`web_search_20250305`). Web content is untrusted, so the
application layer does not rely on the model's word:

- **Grounded sources only.** The adapter reports every URL the web search tool actually retrieved
  (search results and citation targets). A candidate keeps only the source URLs that appear in that
  set; a candidate with none is `SKIPPED / UNGROUNDED`. A model cannot cite a page it never fetched.
- **Domain validation.** Domains go through the same `normalize_domain` used by CSV import
  (no IPs, credentials, ports or paths). Invalid ones are `SKIPPED / INVALID_DOMAIN`.
- **De-duplication.** Domains already attached to the tenant (`ALREADY_ATTACHED`), previously
  rejected (`PREVIOUSLY_REJECTED`), still awaiting review (`ALREADY_PROPOSED`) or repeated in the same
  run (`DUPLICATE_IN_RUN`) are skipped. Attached, rejected and proposed domains (up to 200) are also
  sent as an exclusion list so the provider spends its searches on new companies.
- **Identity only on accept.** Acceptance creates or reuses the global company with the verified
  domain and the proposed name only. AI-suggested country, industry and rationale stay on the
  candidate record (`MARKETINGIQ_DERIVED`) and are never promoted to global identity or to
  `CompanyFact`; evidenced facts come from the normal research providers afterwards.
- The system prompt tells the model to treat search content as data and never follow instructions
  found in it. Even if that failed, the output can only become a reviewable candidate.

## Cost control and permissions

| Action | Permission | Roles |
| --- | --- | --- |
| View status, runs, candidates | `READ` | All members |
| Start a run (spends provider budget) | `SPEND_PROVIDER_CREDITS` | Organization Admin, Marketing User |
| Accept / reject a candidate | `WRITE_CATALOG` | Organization Admin, Marketing User |

- `DISCOVERY_DAILY_RUN_LIMIT` (default 5) caps runs per organization per UTC day; failed runs count.
- `max_results` is 1–25 per run (default 10). `DISCOVERY_MAX_SEARCHES` (default 8, max 20) caps web
  searches per run; `DISCOVERY_MAX_OUTPUT_TOKENS` (default 6000) caps output.
- Preconditions (archived product, no active ICP, provider not configured, daily limit) return
  `409` before any provider call.
- Each run records searches used and input/output tokens, so spend is visible per run. Anthropic
  bills web search per search plus normal token usage; check current pricing before raising limits.

## Configuration

```text
ANTHROPIC_API_KEY=            # empty = discovery not configured (no spend possible)
DISCOVERY_AI_MODEL=claude-sonnet-5-5
DISCOVERY_AI_TIMEOUT_SECONDS=180
DISCOVERY_MAX_SEARCHES=8
DISCOVERY_MAX_OUTPUT_TOKENS=6000
DISCOVERY_DAILY_RUN_LIMIT=5
```

The key is read from the environment only; it is never stored, logged, or returned by the status
endpoint. Provider failures are persisted as `FAILED` runs with a safe category (`RATE_LIMITED`,
`AUTHENTICATION_ERROR`, `NETWORK_ERROR`, `PROVIDER_UNAVAILABLE`, `PROVIDER_REJECTED_REQUEST`,
`MALFORMED_RESPONSE`, `INCOMPLETE_RESPONSE`); provider error text is not stored. Long searches that
return `stop_reason: pause_turn` are resumed up to three times.

## API

All routes are below `/api/v1/organizations/{org_id}`:

- `GET  /discovery/status` — provider configuration, model, daily limit and runs remaining
- `POST /products/{product_id}/discovery-runs` — body `{"max_results": 10}`; returns the run with
  candidates
- `GET  /discovery-runs?product_id=` — run history
- `GET  /discovery-runs/{run_id}` — one run with its candidates
- `GET  /discovery-candidates?status=PROPOSED&product_id=&limit=100` — review queue
  (`status` may be `PROPOSED`, `ACCEPTED`, `REJECTED` or `SKIPPED`)
- `POST /discovery-candidates/{candidate_id}/accept` — body `{"note": "optional"}`
- `POST /discovery-candidates/{candidate_id}/reject` — body `{"note": "optional"}`

## Adding providers

`CompanyDiscoveryProvider` (in `marketingiq.domain.discovery`) is provider-neutral: an adapter
returns `DiscoveredCompany` values plus the set of URLs it retrieved. A licensed company-search API
(Apollo, People Data Labs and similar) can implement the same port; its results should report the
provider's record URLs as retrieved URLs so the grounding rule still holds.

## Deferred

Scheduled discovery runs, automatic acceptance, and product-level automation that researches and
qualifies newly accepted companies are follow-up increments.
