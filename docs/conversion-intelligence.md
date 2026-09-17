# Conversion intelligence

MarketingIQ conversion intelligence reports tenant-scoped historical outcomes from the sales pipeline. It is descriptive only: it does not predict future wins, change fit or qualification scores, rank leads, or infer causality.

## Available views

The learning API exposes:

- overall conversion summary
- buyer-role performance
- message-angle performance
- qualification-grade performance
- industry performance
- country performance
- monthly opportunity cohorts

Every grouped view reports sample size, observed funnel counts, and rates for response, meeting, proposal, win, and loss. `min_sample_size` can suppress groups that are too small for useful interpretation.

## Historical dimension sources

Buyer role is read from the selected tenant-private contact candidate and message angle from the immutable campaign draft.

Qualification grade is read from the immutable lead qualification linked to the opportunity.

Industry and country are read from the immutable fit-assessment evidence snapshot linked through that qualification. They are intentionally not resolved from the company's current facts. If company intelligence changes later, a historical opportunity remains grouped by the evidence that supported its qualification at the time.

If the original fit assessment did not capture a requested fact such as country, the dimension is reported as `UNKNOWN`. MarketingIQ does not backfill the historical group from newer research.

## Monthly cohorts

`GET /learning/monthly-cohorts` groups opportunities by the calendar month in which the opportunity entered the pipeline using `SalesOpportunity.created_at`.

Each cohort reports the opportunity sample size and the funnel stages those opportunities have reached as of the current query. For example, an August opportunity that receives a reply in September remains part of the August cohort while its observed response count becomes visible there.

The response explicitly labels:

- `cohort_basis=OPPORTUNITY_CREATED_AT_MONTH`
- `outcome_basis=OBSERVED_STAGE_HISTORY_TO_DATE`

Only months containing opportunities are returned. `limit` controls how many of the latest populated monthly cohorts are included and `min_sample_size` can suppress very small cohorts. The API does not calculate a trend score, forecast the next period, or label any month as better or worse.

## Safety and interpretation

- Results are organization-scoped and require normal read permission.
- No contact email address, provider payload, mailbox content, or restricted raw evidence is included in these aggregates.
- Rates are descriptive observed outcomes with the tenant opportunity set or cohort as the denominator.
- A larger observed rate does not prove that a buyer role, message angle, industry, country, qualification grade, or time period caused the result.
- Recent monthly cohorts can be immature because some opportunities may not yet have had enough time to progress through later funnel stages.
- No automated outbound action, scoring change, or pipeline-stage change is triggered from these reports.

These measurements are intended to become part of MarketingIQ's proprietary first-party learning layer while preserving evidence lineage and historical reproducibility.
