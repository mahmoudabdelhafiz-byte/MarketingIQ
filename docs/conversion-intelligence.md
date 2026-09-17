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

Every grouped view reports sample size, observed funnel counts, and rates for response, meeting, proposal, win, and loss. `min_sample_size` can suppress groups that are too small for useful interpretation.

## Historical dimension sources

Buyer role is read from the selected tenant-private contact candidate and message angle from the immutable campaign draft.

Qualification grade is read from the immutable lead qualification linked to the opportunity.

Industry and country are read from the immutable fit-assessment evidence snapshot linked through that qualification. They are intentionally not resolved from the company's current facts. If company intelligence changes later, a historical opportunity remains grouped by the evidence that supported its qualification at the time.

If the original fit assessment did not capture a requested fact such as country, the dimension is reported as `UNKNOWN`. MarketingIQ does not backfill the historical group from newer research.

## Safety and interpretation

- Results are organization-scoped and require normal read permission.
- No contact email address, provider payload, mailbox content, or restricted raw evidence is included in these aggregates.
- Rates are descriptive observed outcomes with the tenant opportunity set as the denominator.
- A larger observed rate does not prove that a buyer role, message angle, industry, country, or qualification grade caused the result.
- No automated outbound action, scoring change, or pipeline-stage change is triggered from these reports.

These measurements are intended to become part of MarketingIQ's proprietary first-party learning layer while preserving evidence lineage and historical reproducibility.
