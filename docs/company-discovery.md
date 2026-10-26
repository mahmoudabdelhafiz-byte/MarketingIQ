# Company discovery

MarketingIQ can find new target companies that match a product's ideal customer profile and add
them to the tenant's company list automatically.

## Source

Discovery uses [Hunter Discover](https://hunter.io/api-documentation/v2) (`POST /v2/discover`),
which searches companies by headquarters country, industry, headcount bucket and keywords, or by a
plain-English description. Discover calls do **not** consume Hunter credits. Free Hunter plans
return only the first page (up to 100 companies) per search; paid plans can page further, which
scheduled rules do automatically. The same `HUNTER_API_KEY` used for contact discovery is required.

## AI web search source

A second source, **AI web search**, researches the web with the configured AI provider (OpenAI,
Claude or DeepSeek; see [AI provider selection](ai-providers.md)). It takes the same filters and
returns companies in the same shape, so previews, "add selected" and automatic rules work the same.
Choose it with **Search with** in *Find companies now*; a rule keeps the source it was created with
(`provider_key` = `HUNTER` or `AI_WEB_SEARCH`).

- **Grounded only.** Each adapter reports the pages its search actually retrieved; a company is
  shown only if it cites one of them. Others are dropped with a note. Previews show the reason and
  source links so you can check before adding.
- **No repeats.** The companies already in your list (up to 200) are sent as an exclusion list.
- **Cost control.** AI search requires `SPEND_PROVIDER_CREDITS`. Every search (preview or rule run)
  writes an audit entry with provider, model, searches and tokens, and counts toward
  `DISCOVERY_AI_DAILY_LIMIT` (default 10 per organization per UTC day). A rule over the limit fails
  that run with `DISCOVERY_DAILY_LIMIT` and runs again on its next schedule.
- AI suggestions never become company facts; research providers establish facts after a company is
  added.

## Web interface

**Automation → Find companies now** pre-fills filters from the selected product's active ICP
(`country`/`location` criteria that are 2-letter codes become countries; `industry`, `keyword` and
`business_service` values become keywords; employee bounds become Hunter headcount buckets). Search
results show which companies are already in your list; selected new ones are added with lifecycle
status `discovered`.

**Save as automatic rule…** stores the filters as a scheduled rule:

- cadence: every 6 hours, daily, every 3 days or weekly;
- a maximum number of *new* companies added per run (1–100); existing companies are never
  duplicated;
- optionally run research → fit → qualification on each new company, and optionally contact
  discovery (Hunter credits) for qualified ones.

Discovery never drafts, approves or sends email. Those remain human actions.

Unrecognised Hunter industry names are retried automatically as keywords. When every result of a
search is already in the list and no further page is available, the run records a note suggesting
broader filters.

## API

```http
GET   /api/v1/organizations/{org_id}/company-discovery/sources
GET   /api/v1/organizations/{org_id}/company-discovery/suggest?product_id=…
POST  /api/v1/organizations/{org_id}/company-discovery/preview
POST  /api/v1/organizations/{org_id}/company-discovery/add
GET   /api/v1/organizations/{org_id}/company-discovery/policies
POST  /api/v1/organizations/{org_id}/company-discovery/policies
PATCH /api/v1/organizations/{org_id}/company-discovery/policies/{policy_id}
GET   /api/v1/organizations/{org_id}/company-discovery/policies/{policy_id}/runs
POST  /api/v1/organizations/{org_id}/company-discovery/policies/{policy_id}/run
```

Preview/add require the `IMPORT_COMPANIES` permission (Organization Admin); rules require
`MANAGE_AUTOMATION_POLICIES`. `preview`, rule creation and rule updates accept an optional
`provider_key` (`HUNTER` by default, or `AI_WEB_SEARCH`, which also requires
`SPEND_PROVIDER_CREDITS`). Each scheduled run re-checks that the rule's owner is still an active
member with the needed permissions, exactly like automation policies.

## Cron

```bash
python -m marketingiq.jobs.run_company_discovery
```

The job takes a MySQL advisory lock (`company-discovery`) so overlapping invocations exit with
`status=skipped`. `DISCOVERY_CRON_BATCH_SIZE` (default 5) limits how many due rules run per
invocation. See `docs/cpanel-passenger.md` for the cPanel cron lines.
