# Company discovery

MarketingIQ can find new target companies that match a product's ideal customer profile and add
them to the tenant's company list automatically.

## Source

Discovery uses [Hunter Discover](https://hunter.io/api-documentation/v2) (`POST /v2/discover`),
which searches companies by headquarters country, industry, headcount bucket and keywords, or by a
plain-English description. Discover calls do **not** consume Hunter credits. Free Hunter plans
return only the first page (up to 100 companies) per search; paid plans can page further, which
scheduled rules do automatically. The same `HUNTER_API_KEY` used for contact discovery is required.

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
`MANAGE_AUTOMATION_POLICIES`. Each scheduled run re-checks that the rule's owner is still an active
member with the needed permissions, exactly like automation policies.

## Cron

```bash
python -m marketingiq.jobs.run_company_discovery
```

The job takes a MySQL advisory lock (`company-discovery`) so overlapping invocations exit with
`status=skipped`. `DISCOVERY_CRON_BATCH_SIZE` (default 5) limits how many due rules run per
invocation. See `docs/cpanel-passenger.md` for the cPanel cron lines.
